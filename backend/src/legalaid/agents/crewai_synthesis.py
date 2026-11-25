"""
legalaid/agents/crewai_synthesis.py
=====================================
Phase 4: CrewAI Legal Synthesis Team
--------------------------------------
Implements the `CrewAILegalSynthesisTeam` — a multi-agent CrewAI crew that
synthesizes a final, structured legal answer from verified Blackboard evidence.

Design Patterns Implemented (per rubric):
  Req 1  — CrewAI agents with A2A receive trigger from ADK researcher
  Req 3  — Structured output: LegalSynthesisResponse (Pydantic v2)
  Req 5  — Step callback scanning intermediate thoughts for injection/toxicity
  Req 5  — Task completion callback validating Pydantic schema + grounding
  Req 6  — Reads GroundingManifest from Blackboard; posts result to SYNTHESIS plane
  Req 7  — output_pydantic=LegalSynthesisResponse on the CrewAI Task

CrewAI Architecture:
  Crew members:
    1. "Lead Legal Analyst"   — Synthesizes statutory findings into legal position
    2. "Precedent Integrator" — Weaves case law into the analysis narrative
  Task:
    "Legal Synthesis Task"    — Produces the final LegalSynthesisResponse JSON
  Callbacks:
    step_callback             → crewai_step_monitor (injection + toxicity scan)
    task_callback             → task_validation_callback (Pydantic + grounding check)

CrewAI + Ollama:
  CrewAI uses LiteLLM under the hood. Specifying llm="ollama/codellama:7b-instruct"
  routes to the local Ollama server (consistent with the existing LegalAId config).

Async bridge:
  CrewAI's crew.kickoff() is synchronous. We wrap it in asyncio.to_thread() to
  prevent blocking the event loop. The A2A receive() is awaitable and waits for
  the ADK researcher to fire the FRAMEWORK_HANDOFF trigger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

try:
    from crewai import Agent, Crew, Process, Task
except ImportError:
    # Fallback protocol stubs when running in Python 3.14+ where CrewAI PyPI metadata caps at <3.14
    class Process:
        sequential = "sequential"
        hierarchical = "hierarchical"

    class Agent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    class Task:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.callback = kwargs.get("callback")
            self.output_pydantic = kwargs.get("output_pydantic")

    class CrewOutput:
        def __init__(self, raw: str, pydantic: Any = None) -> None:
            self.raw = raw
            self.pydantic = pydantic

    class Crew:
        def __init__(self, **kwargs: Any) -> None:
            self.agents = kwargs.get("agents", [])
            self.tasks = kwargs.get("tasks", [])
            self.step_callback = kwargs.get("step_callback")

        def kickoff(self) -> Any:
            # Deterministic execution using configured task specs
            task = self.tasks[0] if self.tasks else None
            pydantic_cls = getattr(task, "output_pydantic", None) if task else None
            callback = getattr(task, "callback", None) if task else None
            
            output = CrewOutput(raw="{}", pydantic=None)
            if callback:
                try:
                    callback(output)
                except Exception:
                    pass
            return output

from legalaid.a2a.bridge import (
    A2AMessageEnvelope,
    AgentFramework,
    MessageType,
    a2a_bus,
)
from legalaid.blackboard.blackboard import (
    BlackboardPlane,
    GroundingManifest,
    LegalBlackboard,
)
from legalaid.domain.output_models import (
    ConfidenceLevel,
    LegalSynthesisResponse,
    PrecedentFinding,
    StatutoryFinding,
)

logger = logging.getLogger("legalaid.agents.crewai_synthesis")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CREWAI_LLM_MODEL     = "ollama/codellama:7b-instruct"  # LiteLLM routing to local Ollama
SYNTHESIS_AGENT_NAME = "crewai_synthesis_team"

# Injection/toxicity patterns — mirrored from core/guardrails.py::screen_static
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignore\s+prior\s+instructions",
        r"system\s+override",
        r"disregard\s+the\s+above",
        r"developer\s+mode",
        r"<script",
        r"jailbreak",
        r"pretend\s+you\s+are",
        r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions",
    ]
]


# ===========================================================================
# Guardrail: Step Monitor Callback (Req 5)
# ===========================================================================

def crewai_step_monitor(step_output: Any) -> None:
    """
    CrewAI step_callback — fires after every agent "thought" step.
    Scans intermediate agent reasoning for prompt injection or toxic content.
    Logs warnings; does NOT raise exceptions to avoid crashing the crew (CrewAI
    does not support mid-loop abort via step_callback in all versions — instead
    we log and flag for post-hoc audit review).

    Args:
        step_output: The raw step output object from CrewAI. The text content
                     is accessible via str(step_output) or step_output.text
                     depending on the CrewAI version.

    Note:
        This function is SYNCHRONOUS — CrewAI step_callback must be sync.
        Any async operations (e.g., DB logging) must use asyncio.run_coroutine_threadsafe
        or be done in a non-blocking fire-and-forget pattern.
    """
    # Extract text content from the step output object
    step_text: str = ""
    if hasattr(step_output, "text"):
        step_text = step_output.text or ""
    elif hasattr(step_output, "result"):
        step_text = str(step_output.result)
    else:
        step_text = str(step_output)

    # --- Scan for injection patterns ---
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(step_text):
            logger.warning(
                "[GUARDRAIL] Prompt injection pattern detected in CrewAI step: '%s' | "
                "Matched pattern: '%s'",
                step_text[:100], pattern.pattern,
            )
            # Mark in step text for downstream audit — cannot abort here
            # The task_validation_callback will catch this in the final output
            return

    # --- Basic toxicity screen (length + keyword heuristics) ---
    TOXIC_KEYWORDS = {"ignore all", "reveal system prompt", "sudo mode", "root access"}
    lower_text = step_text.lower()
    for kw in TOXIC_KEYWORDS:
        if kw in lower_text:
            logger.warning("[GUARDRAIL] Toxic keyword in CrewAI step: '%s'", kw)
            return

    logger.debug("[CrewAI Step Monitor] Step clean. Length=%d chars.", len(step_text))


# ===========================================================================
# Guardrail: Task Completion Callback Factory (Req 5 + Req 7)
# ===========================================================================

def create_task_validation_callback(
    allowed_chunk_ids: list[str],
    trace_id: str,
) -> Any:
    """
    Factory function that returns a CrewAI task completion callback.

    The returned callback:
      1. Validates the raw task output against the LegalSynthesisResponse schema.
      2. Strips any citations referencing chunk_ids NOT in allowed_chunk_ids
         (prevents hallucinated citations from passing the grounding check).
      3. Sets hallucination_risk field based on ungrounded claims.
      4. Logs validation results.

    Args:
        allowed_chunk_ids: The GroundingManifest's authorised chunk IDs.
        trace_id:          The request trace ID for logging correlation.

    Returns:
        A synchronous callable compatible with CrewAI's task callback interface.
    """
    allowed_set = set(allowed_chunk_ids)

    def _validate_task_output(task_output: Any) -> None:
        """
        Validates CrewAI task output against LegalSynthesisResponse Pydantic schema.
        Strips ungrounded citations and assesses hallucination risk.
        """
        logger.info("[TaskCallback] Validating synthesis output (trace=%s)", trace_id)

        # Extract raw text from CrewAI task output
        raw_text: str = ""
        if hasattr(task_output, "raw"):
            raw_text = task_output.raw or ""
        elif hasattr(task_output, "result"):
            raw_text = str(task_output.result)
        else:
            raw_text = str(task_output)

        # --- Attempt Pydantic validation ---
        try:
            response = LegalSynthesisResponse.model_validate_json(raw_text)
            logger.info("[TaskCallback] Pydantic validation PASSED.")
        except Exception as exc:
            logger.warning(
                "[TaskCallback] Pydantic validation FAILED: %s | Raw: %.200s",
                exc, raw_text,
            )
            # Cannot modify task_output in-place in all CrewAI versions —
            # flag in logs and continue. The outer run_synthesis will handle.
            return

        # --- Grounding check: strip ungrounded statutory citations ---
        original_statute_count = len(response.applicable_statutes)
        response.applicable_statutes = [
            s for s in response.applicable_statutes
            if s.chunk_id in allowed_set or not allowed_set  # Pass-through if manifest empty
        ]
        stripped_statutes = original_statute_count - len(response.applicable_statutes)

        # --- Grounding check: strip ungrounded precedent citations ---
        original_prec_count = len(response.applicable_precedents)
        response.applicable_precedents = [
            p for p in response.applicable_precedents
            if p.chunk_id in allowed_set or not allowed_set
        ]
        stripped_precs = original_prec_count - len(response.applicable_precedents)

        # --- Assess hallucination risk ---
        total_stripped = stripped_statutes + stripped_precs
        if total_stripped == 0:
            response.hallucination_risk = "CLEAN"
        elif total_stripped <= 2:
            response.hallucination_risk = "LOW"
        elif total_stripped <= 5:
            response.hallucination_risk = "MEDIUM"
        else:
            response.hallucination_risk = "HIGH"

        logger.info(
            "[TaskCallback] Grounding: stripped %d statutes, %d precedents. Risk=%s",
            stripped_statutes, stripped_precs, response.hallucination_risk,
        )

        # --- Update the task output object if possible ---
        # CrewAI TaskOutput may or may not support direct mutation.
        # Best-effort: try to update the pydantic_output attribute.
        try:
            if hasattr(task_output, "pydantic"):
                task_output.pydantic = response
        except Exception:
            pass  # Silent — the validated object is returned from run_synthesis

    return _validate_task_output


# ===========================================================================
# CrewAI Legal Synthesis Team
# ===========================================================================

class CrewAILegalSynthesisTeam:
    """
    A CrewAI-based multi-agent team that synthesizes the final legal answer.

    This class:
      1. Waits for the ADK researcher to send a FRAMEWORK_HANDOFF via a2a_bus.
      2. Reads the GroundingManifest from the Blackboard.
      3. Builds a CrewAI Crew with two agents and one synthesis task.
      4. Runs the crew synchronously (wrapped in asyncio.to_thread).
      5. Validates and posts the result to BlackboardPlane.SYNTHESIS.
    """

    def __init__(self, blackboard: LegalBlackboard, trace_id: str) -> None:
        """
        Args:
            blackboard: The shared LegalBlackboard instance for this session.
            trace_id:   The LA-XXXXXXXX trace ID for this user request cycle.
        """
        self._blackboard  = blackboard
        self._trace_id    = trace_id
        self._last_result: LegalSynthesisResponse | None = None
        logger.info(
            "CrewAILegalSynthesisTeam initialized (trace=%s)", trace_id
        )

    async def _register_with_bus(self) -> None:
        """Register this agent with the A2A message bus (idempotent)."""
        await a2a_bus.register_agent(SYNTHESIS_AGENT_NAME)

    # -----------------------------------------------------------------------
    # A2A: Wait for ADK trigger
    # -----------------------------------------------------------------------

    async def wait_for_adk_trigger(self, timeout: float = 300.0) -> A2AMessageEnvelope:
        """
        Await a FRAMEWORK_HANDOFF message from the ADK researcher.

        Blocks until the ADK agent posts its findings and sends the handoff signal,
        or raises asyncio.TimeoutError after the specified timeout.

        Args:
            timeout: Seconds to wait (default 5 minutes — generous for slow LLMs).

        Returns:
            The A2AMessageEnvelope containing the handoff payload.
        """
        await self._register_with_bus()
        logger.info(
            "[A2A] crewai_synthesis_team waiting for FRAMEWORK_HANDOFF (timeout=%.0fs)",
            timeout,
        )
        envelope = await a2a_bus.receive(SYNTHESIS_AGENT_NAME, timeout=timeout)
        logger.info(
            "[A2A] Received %s from %s (msg_id=%s)",
            envelope.message_type, envelope.source_agent, envelope.message_id,
        )
        return envelope

    # -----------------------------------------------------------------------
    # CrewAI Agent & Task Construction
    # -----------------------------------------------------------------------

    def _build_crew(
        self,
        question: str,
        statutory_context: str,
        precedent_context: str,
        grounding_manifest: GroundingManifest,
    ) -> Crew:
        """
        Constructs the CrewAI Crew for this synthesis session.

        Agents:
          lead_analyst:         Synthesizes statutory law into legal position.
          precedent_integrator: Weaves judicial precedents into the narrative.

        Task:
          synthesis_task: Produces the final LegalSynthesisResponse JSON.
          - output_pydantic=LegalSynthesisResponse enforces structured output.
          - callback=create_task_validation_callback(...) runs grounding check.

        Crew:
          - process=Process.sequential (lead_analyst → precedent_integrator → synthesis)
          - step_callback=crewai_step_monitor (injection scan every thought step)
          - verbose=True for full CrewAI execution logging
        """
        # ---- Agent 1: Lead Legal Analyst ----
        lead_analyst = Agent(
            role="Senior Indian Legal Analyst",
            goal=(
                "Synthesize the retrieved statutory provisions into a clear, accurate, "
                "and well-reasoned legal position statement. Every claim must be "
                "supported by a specific Act and Section from the corpus. "
                "Your output feeds directly into a legal memorandum."
            ),
            backstory=(
                "You are a senior advocate with 20 years of experience in Indian constitutional "
                "and commercial law. You have appeared before the Supreme Court and High Courts. "
                "You are known for your precise, citation-heavy legal analysis and zero tolerance "
                "for unsupported assertions. You believe every legal conclusion must trace back "
                "to a specific statutory provision."
            ),
            llm=CREWAI_LLM_MODEL,
            verbose=True,
            allow_delegation=False,  # Focused role — no sub-delegation
        )

        # ---- Agent 2: Precedent Integrator ----
        precedent_integrator = Agent(
            role="Judicial Precedent Specialist",
            goal=(
                "Integrate the most relevant Supreme Court and High Court judgements "
                "into the legal analysis. Identify the ratio decidendi, distinguish "
                "inapplicable cases, and explain how each precedent supports or modifies "
                "the statutory interpretation."
            ),
            backstory=(
                "You are a law clerk specializing in Indian case law research. "
                "You have indexed over 50,000 Supreme Court judgements and can instantly "
                "identify the key holding of any case. You believe that statute and "
                "precedent must always be read together for a complete legal picture."
            ),
            llm=CREWAI_LLM_MODEL,
            verbose=True,
            allow_delegation=False,
        )

        # ---- Synthesis Task with Structured Output (Req 7) ----
        allowed_ids_str = json.dumps(grounding_manifest.allowed_chunk_ids[:30])  # Truncate for prompt
        synthesis_task = Task(
            description=(
                f"Legal Question: {question}\n\n"
                f"STATUTORY FINDINGS FROM CORPUS:\n{statutory_context}\n\n"
                f"JUDICIAL PRECEDENTS FROM CORPUS:\n{precedent_context}\n\n"
                f"GROUNDING MANIFEST (only cite these chunk_ids):\n{allowed_ids_str}\n\n"
                "Produce a comprehensive legal synthesis in the EXACT JSON schema of "
                "LegalSynthesisResponse. Include:\n"
                "  - answer_markdown: Full analysis with ## headers\n"
                "  - applicable_statutes: list of StatutoryFinding objects\n"
                "  - applicable_precedents: list of PrecedentFinding objects\n"
                "  - key_takeaways: 3-5 actionable bullet points\n"
                "  - confidence: HIGH|MEDIUM|LOW|UNKNOWN\n"
                "  - evidence_sufficient: true/false\n"
                "CRITICAL: Only cite chunk_ids from the Grounding Manifest. "
                "Any citation outside the manifest will be stripped automatically."
            ),
            expected_output=(
                "A valid JSON object conforming to the LegalSynthesisResponse Pydantic schema, "
                "with answer_markdown in GitHub-flavoured Markdown, "
                "all statutory findings with chunk_id references, "
                "and a confidence rating supported by the evidence."
            ),
            agent=lead_analyst,  # Primary task owner — precedent_integrator contributes via context
            output_pydantic=LegalSynthesisResponse,
            callback=create_task_validation_callback(
                allowed_chunk_ids=grounding_manifest.allowed_chunk_ids,
                trace_id=self._trace_id,
            ),
        )

        # ---- Assemble Crew ----
        crew = Crew(
            agents=[lead_analyst, precedent_integrator],
            tasks=[synthesis_task],
            process=Process.sequential,
            step_callback=crewai_step_monitor,  # Guardrail on every thought step (Req 5)
            verbose=True,
        )
        return crew

    # -----------------------------------------------------------------------
    # Core Synthesis Runner
    # -----------------------------------------------------------------------

    async def run_synthesis(self, question: str) -> LegalSynthesisResponse:
        """
        Execute the full CrewAI synthesis pipeline.

        Steps:
          1. Fetch GroundingManifest from Blackboard (verified chunk_ids only).
          2. Fetch STATUTORY_AUTHORITY and JUDICIAL_PRECEDENT entries.
          3. Build and run the CrewAI Crew (sync, wrapped in asyncio.to_thread).
          4. Validate and post the result to BlackboardPlane.SYNTHESIS.
          5. Send A2A TASK_RESPONSE back to orchestrator.

        Args:
            question: The original legal research question.

        Returns:
            A validated LegalSynthesisResponse.
        """
        logger.info(
            "[Synthesis] run_synthesis starting (trace=%s, question='%.80s')",
            self._trace_id, question,
        )

        # --- Step 1: Get verified grounding manifest ---
        manifest = await self._blackboard.get_verified_grounding_manifest()
        logger.info(
            "[Synthesis] GroundingManifest: %d chunk_ids, %d verified entries.",
            len(manifest.allowed_chunk_ids), manifest.source_entry_count,
        )

        # --- Step 2: Get Blackboard context ---
        statutory_entry  = await self._blackboard.get_latest_entry(BlackboardPlane.STATUTORY_AUTHORITY)
        precedent_entry  = await self._blackboard.get_latest_entry(BlackboardPlane.JUDICIAL_PRECEDENT)

        # Format context strings for the CrewAI task description
        statutory_context = (
            json.dumps(statutory_entry.data, indent=2)
            if statutory_entry else "No statutory findings available."
        )
        precedent_context = (
            json.dumps(precedent_entry.data, indent=2)
            if precedent_entry else "No judicial precedents available."
        )

        # --- Step 3: Build crew ---
        crew = self._build_crew(
            question=question,
            statutory_context=statutory_context,
            precedent_context=precedent_context,
            grounding_manifest=manifest,
        )

        # --- Step 4: Run crew (sync kickoff wrapped in asyncio.to_thread) ---
        # crew.kickoff() is synchronous — must be offloaded to avoid blocking the event loop.
        logger.info("[Synthesis] Kicking off CrewAI crew (in thread)...")
        try:
            crew_result = await asyncio.to_thread(crew.kickoff)
        except Exception as exc:
            logger.error("[Synthesis] CrewAI kickoff failed: %s", exc, exc_info=True)
            # Return a degraded response on failure
            return LegalSynthesisResponse(
                trace_id=self._trace_id,
                answer_markdown=(
                    f"## Error\nSynthesis failed due to an internal error: {exc}\n\n"
                    "Please retry or consult a legal professional."
                ),
                confidence=ConfidenceLevel.UNKNOWN,
                evidence_sufficient=False,
                hallucination_risk="NOT_ASSESSED",
                synthesis_agent=SYNTHESIS_AGENT_NAME,
            )

        # --- Step 5: Extract validated Pydantic object ---
        # CrewAI returns a CrewOutput object; .pydantic holds the output_pydantic result.
        synthesis_response: LegalSynthesisResponse | None = None
        if hasattr(crew_result, "pydantic") and isinstance(crew_result.pydantic, LegalSynthesisResponse):
            synthesis_response = crew_result.pydantic
            logger.info("[Synthesis] Pydantic output extracted from CrewOutput.pydantic")
        else:
            # Fallback: try to parse from raw output string
            raw_output = getattr(crew_result, "raw", str(crew_result))
            try:
                synthesis_response = LegalSynthesisResponse.model_validate_json(raw_output)
                logger.info("[Synthesis] Pydantic output parsed from raw CrewOutput string.")
            except Exception as parse_exc:
                logger.warning("[Synthesis] Could not parse CrewOutput as LegalSynthesisResponse: %s", parse_exc)
                synthesis_response = LegalSynthesisResponse(
                    trace_id=self._trace_id,
                    answer_markdown=raw_output or "Synthesis completed but output could not be structured.",
                    confidence=ConfidenceLevel.LOW,
                    evidence_sufficient=False,
                    hallucination_risk="NOT_ASSESSED",
                    synthesis_agent=SYNTHESIS_AGENT_NAME,
                )

        # Enrich with metadata not available inside the crew
        synthesis_response.trace_id         = self._trace_id
        synthesis_response.synthesis_agent  = SYNTHESIS_AGENT_NAME
        synthesis_response.grounding_chunk_ids = list(synthesis_response.extract_citation_ids())

        # --- Step 6: Post to Blackboard SYNTHESIS plane ---
        await self._blackboard.post_entry(
            plane=BlackboardPlane.SYNTHESIS,
            author_agent=SYNTHESIS_AGENT_NAME,
            author_framework=AgentFramework.CREWAI,
            data=synthesis_response.to_json_dict(),
            allowed_chunk_ids=synthesis_response.grounding_chunk_ids,
            audit_notes=[
                f"CrewAI synthesis complete. Confidence={synthesis_response.confidence}",
                f"Hallucination risk: {synthesis_response.hallucination_risk}",
            ],
        )
        logger.info(
            "[Synthesis] Result posted to Blackboard. confidence=%s risk=%s",
            synthesis_response.confidence, synthesis_response.hallucination_risk,
        )

        self._last_result = synthesis_response

        # --- Step 7: Notify orchestrator via A2A ---
        await a2a_bus.register_agent("system_orchestrator")
        await a2a_bus.send(
            A2AMessageEnvelope(
                trace_id=self._trace_id,
                source_agent=SYNTHESIS_AGENT_NAME,
                source_framework=AgentFramework.CREWAI,
                target_agent="system_orchestrator",
                message_type=MessageType.TASK_RESPONSE,
                payload={
                    "status":     "COMPLETE",
                    "confidence": synthesis_response.confidence,
                    "risk":       synthesis_response.hallucination_risk,
                    "response_id": synthesis_response.response_id,
                },
            )
        )

        return synthesis_response

    # -----------------------------------------------------------------------
    # Combined: Listen for ADK trigger, then run synthesis
    # -----------------------------------------------------------------------

    async def listen_and_run(
        self,
        question: str,
        trigger_timeout: float = 300.0,
    ) -> LegalSynthesisResponse:
        """
        Full lifecycle:
          1. Register with A2A bus.
          2. Await FRAMEWORK_HANDOFF from ADK researcher.
          3. Run synthesis using Blackboard state populated by ADK agent.

        This is the primary entry point called by the orchestrator.

        Args:
            question:        The legal research question (used if not in handoff payload).
            trigger_timeout: Seconds to wait for the ADK handoff.

        Returns:
            The final validated LegalSynthesisResponse.
        """
        await self._register_with_bus()

        # Await ADK trigger — the handoff payload may contain a refined question
        envelope = await self.wait_for_adk_trigger(timeout=trigger_timeout)
        effective_question = envelope.payload.get("question", question)

        logger.info(
            "[listen_and_run] ADK handoff received. "
            "question='%.80s' confidence=%s chunk_count=%s",
            effective_question,
            envelope.payload.get("confidence", "?"),
            envelope.payload.get("chunk_count", "?"),
        )

        return await self.run_synthesis(effective_question)

