"""
backend/tests/test_a2a_broker.py
=================================
Unit tests for the Agent-to-Agent (A2A) Message Broker (`legalaid.a2a.bridge`).
Validates rubric requirement 1 (A2A interoperability).
"""

import asyncio
import pytest
from pydantic import ValidationError

from legalaid.a2a.bridge import (
    A2AMessageBroker,
    A2AMessageEnvelope,
    AgentFramework,
    MessageType,
)


@pytest.mark.asyncio
async def test_agent_registration_and_deregistration():
    broker = A2AMessageBroker()
    agent_name = "test_adk_agent"

    assert agent_name not in broker.registered_agents
    await broker.register_agent(agent_name)
    assert agent_name in broker.registered_agents

    # Idempotent registration
    await broker.register_agent(agent_name)
    assert len(broker.registered_agents) == 1

    await broker.deregister_agent(agent_name)
    assert agent_name not in broker.registered_agents


@pytest.mark.asyncio
async def test_unicast_message_delivery():
    broker = A2AMessageBroker()
    sender = "adk_legal_researcher"
    recipient = "crewai_synthesis_team"

    await broker.register_agent(sender)
    await broker.register_agent(recipient)

    envelope = A2AMessageEnvelope(
        trace_id="LA-TEST-001",
        source_agent=sender,
        source_framework=AgentFramework.GOOGLE_ADK,
        target_agent=recipient,
        message_type=MessageType.FRAMEWORK_HANDOFF,
        payload={"task": "synthesize", "sections": ["Section 27"]},
    )

    receipt = await broker.send(envelope)
    assert receipt.target_agent == recipient
    assert receipt.message_id == envelope.message_id
    assert broker.queue_depth(recipient) == 1

    received = await broker.receive(recipient, timeout=2.0)
    assert received.message_id == envelope.message_id
    assert received.source_framework == AgentFramework.GOOGLE_ADK
    assert received.payload["sections"] == ["Section 27"]
    assert broker.queue_depth(recipient) == 0


@pytest.mark.asyncio
async def test_broadcast_and_wildcard_observer():
    broker = A2AMessageBroker()
    agent_a = "agent_a"
    agent_b = "agent_b"
    observer = "*"

    await broker.register_agent(agent_a)
    await broker.register_agent(agent_b)
    await broker.register_agent(observer)

    # Broadcast message to "*"
    broadcast_env = A2AMessageEnvelope(
        trace_id="LA-BROADCAST",
        source_agent="system_admin",
        source_framework=AgentFramework.SYSTEM,
        target_agent="*",
        message_type=MessageType.TASK_REQUEST,
        payload={"broadcast_alert": "pause"},
    )
    await broker.send(broadcast_env)

    # Both agent_a and agent_b must receive it
    msg_a = await broker.receive(agent_a, timeout=1.0)
    msg_b = await broker.receive(agent_b, timeout=1.0)
    assert msg_a.payload["broadcast_alert"] == "pause"
    assert msg_b.payload["broadcast_alert"] == "pause"

    # Wildcard observer must also observe unicast messages
    unicast_env = A2AMessageEnvelope(
        trace_id="LA-UNICAST",
        source_agent=agent_a,
        source_framework=AgentFramework.GOOGLE_ADK,
        target_agent=agent_b,
        message_type=MessageType.CRITIC_REVISION,
        payload={"revision": 1},
    )
    await broker.send(unicast_env)

    obs_msg = await broker.receive(observer, timeout=1.0)
    assert obs_msg.target_agent in (agent_b, "*")


@pytest.mark.asyncio
async def test_envelope_immutability():
    envelope = A2AMessageEnvelope(
        trace_id="LA-FROZEN",
        source_agent="agent_1",
        source_framework=AgentFramework.GOOGLE_ADK,
        target_agent="agent_2",
        message_type=MessageType.TASK_RESPONSE,
        payload={"status": "OK"},
    )

    # Attempting to mutate a frozen Pydantic model must raise ValidationError
    with pytest.raises(ValidationError):
        envelope.trace_id = "LA-MUTATED"


@pytest.mark.asyncio
async def test_receive_timeout():
    broker = A2AMessageBroker()
    await broker.register_agent("quiet_agent")

    with pytest.raises(asyncio.TimeoutError):
        await broker.receive("quiet_agent", timeout=0.1)

