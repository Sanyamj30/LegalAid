"""
legalaid/a2a/bridge.py
=======================
Phase 3: Agent-to-Agent (A2A) Message Broker
---------------------------------------------
Provides universal, framework-agnostic message passing between:
  - Google ADK agents     (async Python, ReAct loops)
  - CrewAI agents         (sync kickoff, wrapped in asyncio.to_thread)
  - System-level services (orchestrator, guardrails, observability)

Architecture:
  Each agent has a named asyncio.Queue. The broker routes envelopes by
  target_agent name. Wildcard subscriber "*" receives a copy of every message
  (used by the ObservabilityBridge for audit logging without coupling).

Thread Safety:
  All queue operations are asyncio-native and safe for concurrent coroutines.
  The agent registry dict is protected by an asyncio.Lock to allow dynamic
  agent registration at runtime.

Message Format:
  A2AMessageEnvelope is frozen (immutable after creation) to prevent
  in-flight mutation — treat it as an immutable wire format.

Usage:
  from legalaid.a2a.bridge import a2a_bus, A2AMessageEnvelope, MessageType, AgentFramework

  # Register your agent
  await a2a_bus.register_agent("my_agent")

  # Send a message
  await a2a_bus.send(A2AMessageEnvelope(
      source_agent="my_agent",
      source_framework=AgentFramework.GOOGLE_ADK,
      target_agent="crewai_synthesis_team",
      message_type=MessageType.FRAMEWORK_HANDOFF,
      payload={"research_complete": True},
  ))

  # Receive with timeout
  envelope = await a2a_bus.receive("crewai_synthesis_team", timeout=60.0)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger("legalaid.a2a.bridge")


# ===========================================================================
# Enumerations
# ===========================================================================

class AgentFramework(StrEnum):
    """
    Identifies the runtime framework that produced or is consuming a message.
    Used for routing logic and observability tagging.
    """
    GOOGLE_ADK = "GOOGLE_ADK"    # Google Agent Development Kit agents (ReAct, async)
    CREWAI     = "CREWAI"        # CrewAI agents (role-based, sync kickoff)
    SYSTEM     = "SYSTEM"        # Orchestrator, guardrails, or internal services


class MessageType(StrEnum):
    """
    Semantic classification of the message payload.
    Agents use this to dispatch to the correct handler without inspecting the payload.
    """
    TASK_REQUEST          = "TASK_REQUEST"           # Kick off a task in the target agent
    TASK_RESPONSE         = "TASK_RESPONSE"           # Completed task result sent back
    CRITIC_REVISION       = "CRITIC_REVISION"         # ADK critic requesting CrewAI revision
    BLACKBOARD_STATE_UPDATE = "BLACKBOARD_STATE_UPDATE" # Notify agents of a Blackboard write
    FRAMEWORK_HANDOFF     = "FRAMEWORK_HANDOFF"       # ADK → CrewAI or vice-versa trigger
    ABORT                 = "ABORT"                   # Terminate the current task gracefully


# ===========================================================================
# Wire-Format Models (Pydantic v2, frozen/immutable)
# ===========================================================================

class A2AMessageEnvelope(BaseModel):
    """
    Universal message envelope passed between agents across framework boundaries.

    Design:
      - frozen=True: Envelopes are immutable once created. Agents must create
        new envelopes rather than mutating existing ones (prevents race conditions).
      - message_id: Globally unique per message (not per conversation).
      - trace_id: Shared across all messages in a single user request cycle.
        Used for end-to-end observability correlation.
    """
    model_config = ConfigDict(frozen=True)

    message_id:       str            = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    trace_id:         str            = Field(default="trace_unknown")
    source_agent:     str            = Field(..., description="Name of the sending agent")
    source_framework: AgentFramework = Field(..., description="Framework of the sender")
    target_agent:     str            = Field(..., description="Name of the receiving agent, or '*' for broadcast")
    message_type:     MessageType    = Field(..., description="Semantic type of the payload")
    payload:          dict[str, Any] = Field(default_factory=dict)
    metadata:         dict[str, Any] = Field(
        default_factory=dict,
        description="Optional headers: retry count, priority, TTL, etc.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of envelope creation",
    )


class DeliveryReceipt(BaseModel):
    """
    Returned by broker.send() to confirm delivery details.
    Agents can log receipts for retry logic without querying the broker.
    """
    model_config = ConfigDict(frozen=True)

    message_id:  str      = Field(...)
    target_agent: str     = Field(...)
    delivered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    queue_depth_after: int = Field(0, description="Queue depth after delivery (for backpressure monitoring)")


# ===========================================================================
# Message Broker
# ===========================================================================

class A2AMessageBroker:
    """
    Asynchronous message broker enabling Agent-to-Agent communication across
    framework boundaries (Google ADK ↔ CrewAI ↔ System).

    Internal structure:
      _queues:   Dict[agent_name → asyncio.Queue[A2AMessageEnvelope]]
      _registry: Set of registered agent names
      _history:  Ordered list of all envelopes sent (for audit/replay)
      _lock:     asyncio.Lock protecting _queues and _registry mutations

    Routing rules:
      - target_agent == specific name → unicast to that agent's queue
      - target_agent == "*"           → broadcast copy to ALL registered queues
      - Wildcard subscriber "*"       → receives a copy of every message (observer)
    """

    def __init__(self, history_limit: int = 1000) -> None:
        self._queues:        dict[str, asyncio.Queue[A2AMessageEnvelope]] = {}
        self._registry:      set[str] = set()
        self._history:       list[A2AMessageEnvelope] = []
        self._history_limit: int = history_limit
        self._lock:          asyncio.Lock = asyncio.Lock()
        logger.info("A2AMessageBroker initialized (history_limit=%d)", history_limit)

    # -----------------------------------------------------------------------
    # Agent Lifecycle
    # -----------------------------------------------------------------------

    async def register_agent(self, agent_name: str, queue_maxsize: int = 100) -> None:
        """
        Register an agent with the broker, creating its message queue.

        Args:
            agent_name:   Unique agent identifier (e.g., "crewai_synthesis_team").
            queue_maxsize: Max envelopes buffered before backpressure kicks in.
                           0 = unlimited (use carefully — can exhaust memory).
        """
        async with self._lock:
            if agent_name in self._registry:
                logger.debug("Agent '%s' already registered — skipping.", agent_name)
                return
            self._queues[agent_name] = asyncio.Queue(maxsize=queue_maxsize)
            self._registry.add(agent_name)
            logger.info("Agent registered: '%s' (queue_maxsize=%d)", agent_name, queue_maxsize)

    async def deregister_agent(self, agent_name: str) -> None:
        """
        Remove an agent from the broker. In-flight messages in its queue are discarded.
        Call during graceful shutdown or agent teardown.
        """
        async with self._lock:
            self._queues.pop(agent_name, None)
            self._registry.discard(agent_name)
            logger.info("Agent deregistered: '%s'", agent_name)

    # -----------------------------------------------------------------------
    # Send
    # -----------------------------------------------------------------------

    async def send(self, envelope: A2AMessageEnvelope) -> DeliveryReceipt:
        """
        Route an envelope to the target agent's queue.

        Routing logic:
          - Unicast:   envelope.target_agent is a registered agent name.
          - Broadcast: envelope.target_agent == "*" → put into all queues.
          - Wildcard observer "*" always gets a copy of every unicast message.

        Raises:
            ValueError:  If target agent is not registered (for unicast).
            asyncio.QueueFull: If the target queue is at capacity (backpressure).
        """
        # Always append to history (audit trail, bounded by history_limit)
        self._history.append(envelope)
        if len(self._history) > self._history_limit:
            self._history.pop(0)  # FIFO eviction — keep recent messages

        logger.debug(
            "send: %s → %s [%s] trace=%s",
            envelope.source_agent, envelope.target_agent,
            envelope.message_type, envelope.trace_id,
        )

        async with self._lock:
            targets: list[asyncio.Queue[A2AMessageEnvelope]] = []

            if envelope.target_agent == "*":
                # Broadcast: send to all registered queues
                targets = list(self._queues.values())
            else:
                # Unicast
                q = self._queues.get(envelope.target_agent)
                if q is None:
                    raise ValueError(
                        f"Target agent '{envelope.target_agent}' is not registered. "
                        f"Registered agents: {sorted(self._registry)}"
                    )
                targets.append(q)
                # Also deliver to wildcard observer if it exists
                wildcard_q = self._queues.get("*")
                if wildcard_q is not None and wildcard_q is not q:
                    targets.append(wildcard_q)

        # Put into queues outside the lock to avoid blocking other senders
        for q in targets:
            await q.put(envelope)

        depth_after = self._queues.get(envelope.target_agent, asyncio.Queue()).qsize()
        return DeliveryReceipt(
            message_id=envelope.message_id,
            target_agent=envelope.target_agent,
            queue_depth_after=depth_after,
        )

    # -----------------------------------------------------------------------
    # Receive
    # -----------------------------------------------------------------------

    async def receive(self, agent_name: str, timeout: float = 300.0) -> A2AMessageEnvelope:
        """
        Block until a message is available for agent_name, or raise asyncio.TimeoutError.

        Args:
            agent_name: The receiving agent's registered name.
            timeout:    Seconds to wait before giving up (default 5 minutes).

        Returns:
            The next A2AMessageEnvelope from the agent's queue.

        Raises:
            asyncio.TimeoutError: If no message arrives within the timeout.
            KeyError: If the agent is not registered.
        """
        q = self._queues.get(agent_name)
        if q is None:
            raise KeyError(
                f"Agent '{agent_name}' has no queue. Did you call register_agent()?"
            )
        logger.debug("receive: '%s' waiting (timeout=%.1fs)", agent_name, timeout)
        envelope = await asyncio.wait_for(q.get(), timeout=timeout)
        logger.debug(
            "receive: '%s' got message from '%s' [%s]",
            agent_name, envelope.source_agent, envelope.message_type,
        )
        return envelope

    def receive_nowait(self, agent_name: str) -> A2AMessageEnvelope | None:
        """
        Non-blocking dequeue. Returns None if the queue is empty.
        Useful for polling patterns in sync CrewAI wrappers.
        """
        q = self._queues.get(agent_name)
        if q is None:
            return None
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty:
            return None

    # -----------------------------------------------------------------------
    # Introspection
    # -----------------------------------------------------------------------

    def queue_depth(self, agent_name: str) -> int:
        """Returns the current number of messages queued for an agent."""
        q = self._queues.get(agent_name)
        return q.qsize() if q else 0

    @property
    def registered_agents(self) -> list[str]:
        """Returns sorted list of all currently registered agent names."""
        return sorted(self._registry)

    @property
    def history(self) -> list[A2AMessageEnvelope]:
        """
        Returns the bounded message history. Used by the ObservabilityBridge
        to persist message traces to the SQLite telemetry DB.
        Read-only — do not mutate.
        """
        return list(self._history)


# ===========================================================================
# Module-level singleton
# ===========================================================================

#: Global broker instance shared across all agents in the same process.
#: Import and use this directly — do not create additional broker instances.
a2a_bus = A2AMessageBroker(history_limit=2000)

