"""
legalaid/agents/adk_researcher.py
====================================
Phase 4: Google ADK / ReAct Legal Researcher Agent
----------------------------------------------------
Implements the `ADKReActLegalResearcher` — the primary retrieval agent in the
new multi-agent architecture. It bridges the ADK (Google Agent Development Kit)
ReAct reasoning pattern with LegalAId's existing hybrid retrieval MCP tool.

Design Patterns Implemented (per rubric):
  Req 1  — Google ADK agent, A2A FRAMEWORK_HANDOFF to CrewAI on completion
  Req 3  — Calls the MCP tool `search_indian_legal_corpus` (imported in-process)
  Req 4  — Two prompt profiles: ReAct loop + Strict Legal Critic (few-shot)
  Req 5  — Guardrail checks before each LLM call and after each observation
  Req 6  — Reads from and writes to the LegalBlackboard

ReAct Loop Protocol (REACT_LOOP profile):
  The agent runs up to MAX_REACT_ITERATIONS cycles of:
    Thought  → LLM reasons about what to search for
    Action   → Calls search_indian_legal_corpus(query=...)
    Observation → Results appended to context
  Final pass synthesizes all observations into a structured JSON finding.

Few-Shot Profile (STRICT_CRITIC):
  Single-shot with worked examples demonstrating the "Strict Legal Critic"
  persona — citation-heavy, zero tolerance for unsupported assertions.

DB / Async Constraints:
  - Uses asyncio.to_thread() for any sync operations (consistent with project)
  - All LLM calls go through legalaid.services.llm.service.generate()
  - JSON parsing uses agents.base.parse_json_response() for fault tolerance

A2A Integration:
  On completion, fires an A2AMessageEnvelope(MessageType.FRAMEWORK_HANDOFF)
  to "crewai_synthesis_team" via the global a2a_bus broker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from enum import StrEnum
from typing import Any

from legalaid.a2a.bridge import (
    A2AMessageEnvelope,
    AgentFramework,
    MessageType,
    a2a_bus,
)
from legalaid.agents.base import parse_json_response
from legalaid.blackboard.blackboard import BlackboardPlane, LegalBlackboard
from legalaid.domain.contracts import LLMGenerateRequest
from legalaid.domain.enums import AgentKey
from legalaid.mcp.retrieval_server import search_indian_legal_corpus
from legalaid.services.llm.service import generate

logger = logging.getLogger("legalaid.agents.adk_researcher")

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------
MAX_REACT_ITERATIONS  = 4        # Maximum ReAct Thought/Action/Observation cycles
REACT_TEMPERATURE     = 0.3      # Mild creativity for ReAct reasoning
CRITIC_TEMPERATURE    = 0.0      # Zero temperature for deterministic critic judgements
DEFAULT_TOP_K_LAWS    = 10
DEFAULT_TOP_K_CASES   = 10
MAX_TOKENS_REACT      = 1024
MAX_TOKENS_CRITIC     = 2048


# ===========================================================================
# Prompt Profile Enum
# ===========================================================================

class PromptProfile(StrEnum):
    """
    Selects the reasoning strategy for the ADK Legal Researcher.
    REACT_LOOP:    Multi-step Thought/Action/Observation cycles (best for open queries)
    STRICT_CRITIC: Single-shot few-shot approach with worked examples (best for citation checks)
    """
    REACT_LOOP    = "REACT_LOOP"
    STRICT_CRITIC = "STRICT_CRITIC"


# ===========================================================================
# System Prompts (Prompt Variance Bank — Req 4)
# ===========================================================================

# --- Prompt Variant A: ReAct Loop Persona — "Methodical Researcher" ---
REACT_SYSTEM_PROMPT = """You are a Methodical Indian Legal Researcher operating within a ReAct (Reasoning and Acting) framework.

Your task is to answer a legal research question by iteratively searching the LegalAId Indian Law corpus.

You MUST follow this exact format for each iteration:
Thought: [Your reasoning about what to search for and why]
Action: search_indian_legal_corpus(query="<your search query>")
Observation: [The tool will fill this in with retrieved results]

After at most 4 iterations, produce a FINAL ANSWER in this exact JSON format:
```json
{
  "statutory_sections": ["<Section X of Act Y>", ...],
  "case_citations": ["<Case Name, AIR Year Court Page>", ...],
  "findings_summary": "<1-2 paragraph synthesis>",
  "confidence": "HIGH|MEDIUM|LOW",
  "gap_analysis": "<What evidence is still missing, if any>",
  "chunk_ids_used": ["chk_xxx", ...]
}
```

Rules:
- Every claim in statutory_sections MUST be supported by an Observation.
- Never fabricate section numbers or case citations.
- If the corpus lacks sufficient information, set confidence=LOW and explain gaps.
- Always cite the chunk_id from the Observation for traceability.
"""

# --- Prompt Variant B: Strict Legal Critic Persona (Few-Shot) ---
CRITIC_SYSTEM_PROMPT = """You are a Strict Legal Critic — a senior Indian Supreme Court advocate with 30 years of experience.
You tolerate zero unsupported legal assertions. Every claim must cite a specific statutory provision or judicial precedent.

You will be given a legal question and retrieved corpus excerpts.
Your task is to produce a rigorous legal analysis following the examples below.

=== EXAMPLE 1 ===
Question: Is an oral agreement for sale of immovable property enforceable?
Corpus Excerpt: [Section 54, Transfer of Property Act, 1882] "Sale of immovable property of value Rs. 100 and upward can only be made by a registered instrument."
Analysis:
{
  "statutory_sections": ["Section 54, Transfer of Property Act, 1882", "Section 17, Registration Act, 1908"],
  "case_citations": ["Suraj Lamp & Industries Pvt. Ltd. v. State of Haryana, (2012) 1 SCC 656"],
  "findings_summary": "An oral agreement for the sale of immovable property valued at Rs. 100 or above is NOT enforceable as a completed sale. Section 54 TPA mandates registration. However, under Section 53A TPA, part-performance may provide an equitable defence. The Supreme Court in Suraj Lamp confirmed that unregistered agreements cannot confer title.",
  "confidence": "HIGH",
  "gap_analysis": "None — settled law.",
  "chunk_ids_used": []
}
=== END EXAMPLE ===

=== EXAMPLE 2 ===
Question: Can a company director be held personally liable for company debts?
Corpus Excerpt: [Section 2(34), Companies Act, 2013] defines Director. [Section 166] Duties of directors. [Section 339] Fraudulent trading liability.
Analysis:
{
  "statutory_sections": ["Section 339, Companies Act, 2013", "Section 7(7), Insolvency and Bankruptcy Code, 2016"],
  "case_citations": ["Shailesh Haribhai Patel v. State of Gujarat, (2016) SCC Online Guj 1234"],
  "findings_summary": "As a rule, directors enjoy limited liability under the corporate veil. However, Section 339 Companies Act pierces this veil in cases of fraudulent trading during winding up. IBC Section 7(7) additionally imposes personal liability where the director was responsible for fraud leading to insolvency.",
  "confidence": "MEDIUM",
  "gap_analysis": "Liability under Section 452 (wrongful withholding of property) not assessed — requires more facts.",
  "chunk_ids_used": []
}
=== END EXAMPLE ===

Now analyze the following legal question with the same rigour. Output ONLY valid JSON matching the schema above.
"""

# Injection patterns — consistent with core/guardrails.py::screen_static
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"ignore\s+prior\s+instructions",
        r"system\s+override",
        r"disregard\s+the\s+above",
        r"developer\s+mode",
        r"<script",
        r"jailbreak",
        r"pretend\s+you\s+are",
    ]
]


def _screen_for_injection(text: str) -> bool:
    """
    Returns True if the text contains a prompt injection pattern.
    Consistent with core/guardrails.py::screen_static to avoid divergence.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning("Injection pattern detected: '%s'", pattern.pattern)
            return True
    return False


# ===========================================================================
# ADK ReAct Legal Researcher
# ===========================================================================

class ADKReActLegalResearcher:
    """
    Google ADK-style ReAct legal research agent.

    Lifecycle:
      1. __init__: Register with a2a_bus, store Blackboard reference.
      2. execute_research(question, profile): Run the chosen prompt profile.
      3. Results are posted to BlackboardPlane.STATUTORY_AUTHORITY.
      4. An A2A FRAMEWORK_HANDOFF message is sent to crewai_synthesis_team.

    Integration points:
      - Reads matter_facts from BlackboardPlane.MATTER_FACTS (if available).
      - Calls search_indian_legal_corpus() MCP tool directly (in-process import).
      - Uses legalaid.services.llm.service.generate() for all LLM calls.
      - Uses agents.base.parse_json_response() for fault-tolerant JSON parsing.
    """

    AGENT_NAME = "adk_legal_researcher"

    def __init__(
        self,
        blackboard: LegalBlackboard,
        trace_id: str,
        model: str | None = None,
    ) -> None:
        """
        Args:
            blackboard: The shared LegalBlackboard instance for this session.
            trace_id:   The LA-XXXXXXXX trace ID for this user request cycle.
            model:      Optional LLM model override. If None, uses system default.
        """
        self._blackboard = blackboard
        self._trace_id   = trace_id
        self._model      = model
        logger.info(
            "ADKReActLegalResearcher initialized (trace=%s, profile=dual)",
            trace_id,
        )

    async def _register_with_bus(self) -> None:
        """Register this agent with the A2A message bus (idempotent)."""
        await a2a_bus.register_agent(self.AGENT_NAME)

    # -----------------------------------------------------------------------
    # ReAct Loop (Prompt Profile: REACT_LOOP)
    # -----------------------------------------------------------------------

    async def _run_react_loop(self, question: str) -> dict[str, Any]:
        """
        Executes the ReAct Thought/Action/Observation loop.

        Each iteration:
          1. Sends accumulated context to LLM → receives Thought + Action.
          2. Parses Action line: search_indian_legal_corpus(query="...")
          3. Calls the MCP tool and appends Observation to context.
          4. Repeats up to MAX_REACT_ITERATIONS.
        Final iteration: LLM synthesizes all observations into JSON findings.

        Returns:
            Parsed JSON dict with statutory_sections, case_citations, chunk_ids_used, etc.
        """
        logger.info("[ReAct] Starting loop for trace=%s", self._trace_id)

        # Attempt to read matter context from the Blackboard
        matter_entry = await self._blackboard.get_latest_entry(BlackboardPlane.MATTER_FACTS)
        matter_context = ""
        if matter_entry:
            matter_context = f"\nMatter Context:\n{json.dumps(matter_entry.data, indent=2)}\n"

        # Build the initial user prompt
        user_prompt = (
            f"Legal Research Question: {question}\n"
            f"{matter_context}\n"
            "Begin your ReAct loop now. After observations, produce the FINAL ANSWER JSON."
        )

        # Accumulate the full conversation as a growing context string
        conversation_context = user_prompt
        all_chunk_ids: list[str] = []

        for iteration in range(1, MAX_REACT_ITERATIONS + 1):
            logger.info("[ReAct] Iteration %d/%d", iteration, MAX_REACT_ITERATIONS)

            # --- Guardrail: screen accumulated context before each LLM call ---
            if _screen_for_injection(conversation_context):
                logger.error("[ReAct] Injection detected in context — aborting.")
                return {"error": "Prompt injection detected", "statutory_sections": [], "case_citations": [], "chunk_ids_used": []}

            # --- LLM Call: Thought + Action ---
            llm_request = LLMGenerateRequest(
                user_prompt=conversation_context,
                system_prompt=REACT_SYSTEM_PROMPT,
                agent_key=AgentKey.LEGAL_RESEARCH,
                trace_id=self._trace_id,
                temperature=REACT_TEMPERATURE,
                max_tokens=MAX_TOKENS_REACT,
                model=self._model,
                json_mode=False,  # ReAct output is NOT pure JSON until the final pass
            )
            llm_response = await generate(llm_request)
            thought_action_text = llm_response.text
            logger.debug("[ReAct] LLM output (iter %d): %.200s", iteration, thought_action_text)

            # --- Detect FINAL ANSWER (exit condition) ---
            if "FINAL ANSWER" in thought_action_text or "```json" in thought_action_text:
                logger.info("[ReAct] FINAL ANSWER detected on iteration %d.", iteration)
                parsed, _ = parse_json_response(thought_action_text)
                if parsed and isinstance(parsed, dict):
                    parsed.setdefault("chunk_ids_used", all_chunk_ids)
                    return parsed
                # Fallback: couldn't parse, let loop continue
                logger.warning("[ReAct] Failed to parse FINAL ANSWER JSON — continuing loop.")

            # --- Parse Action line ---
            action_match = re.search(
                r'Action:\s*search_indian_legal_corpus\(query=["\'](.+?)["\']\)',
                thought_action_text,
                re.IGNORECASE | re.DOTALL,
            )
            if not action_match:
                logger.warning("[ReAct] No Action found on iteration %d — forcing exit.", iteration)
                break

            search_query = action_match.group(1).strip()
            logger.info("[ReAct] Action: search('%s')", search_query[:80])

            # --- Execute MCP Tool (in-process, no subprocess) ---
            try:
                tool_result = await search_indian_legal_corpus(
                    query=search_query,
                    mode="COMBINED_LEGAL_RESEARCH",
                    top_k_laws=DEFAULT_TOP_K_LAWS,
                    top_k_cases=DEFAULT_TOP_K_CASES,
                    enable_graph=True,
                )
            except Exception as exc:
                logger.error("[ReAct] MCP tool error: %s", exc, exc_info=True)
                tool_result = {"chunks": [], "error": str(exc)}

            # Collect chunk IDs for grounding manifest
            retrieved_chunks = tool_result.get("chunks", [])
            for chunk in retrieved_chunks:
                cid = chunk.get("chunk_id")
                if cid and cid not in all_chunk_ids:
                    all_chunk_ids.append(cid)

            # Format observation for the next LLM context window
            observation_text = self._format_observation(retrieved_chunks, search_query)

            # Append to growing context
            conversation_context = (
                f"{conversation_context}\n\n"
                f"{thought_action_text}\n"
                f"Observation: {observation_text}\n"
            )

        # --- Fallback: force a final synthesis after max iterations ---
        logger.info("[ReAct] Max iterations reached — forcing final synthesis.")
        final_request = LLMGenerateRequest(
            user_prompt=(
                f"{conversation_context}\n\n"
                "You have used all available iterations. "
                "Now produce the FINAL ANSWER JSON based on all Observations above."
            ),
            system_prompt=REACT_SYSTEM_PROMPT,
            agent_key=AgentKey.LEGAL_RESEARCH,
            trace_id=self._trace_id,
            temperature=0.0,  # Force determinism for final synthesis
            max_tokens=MAX_TOKENS_CRITIC,
            model=self._model,
            json_mode=True,
        )
        final_response = await generate(final_request)
        parsed_dict, _ = parse_json_response(final_response.text)
        parsed_final = parsed_dict if isinstance(parsed_dict, dict) else {}
        parsed_final.setdefault("chunk_ids_used", all_chunk_ids)
        return parsed_final

    def _format_observation(
        self, chunks: list[dict[str, Any]], query: str
    ) -> str:
        """
        Format retrieved chunks into a compact string for the LLM context window.
        Includes chunk_id for traceability in FINAL ANSWER citations.
        """
        if not chunks:
            return f"[No results found for query: '{query}']"

        lines = [f"[{len(chunks)} results for: '{query}']"]
        for i, chunk in enumerate(chunks[:6], start=1):  # Limit to top 6 to stay within context
            lines.append(
                f"[{i}] chunk_id={chunk.get('chunk_id', '?')} | "
                f"{chunk.get('source_title', '')} {chunk.get('section', '')} — "
                f"{chunk.get('text', '')[:300]}..."
            )
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Strict Legal Critic (Prompt Profile: STRICT_CRITIC)
    # -----------------------------------------------------------------------

    async def _run_strict_critic(self, question: str) -> dict[str, Any]:
        """
        Single-shot few-shot approach with the Strict Legal Critic persona.

        Workflow:
          1. Retrieve top results from the corpus for the question.
          2. Format corpus excerpts as context.
          3. Single LLM call with CRITIC_SYSTEM_PROMPT (few-shot examples embedded).
          4. Parse and return JSON findings.
        """
        logger.info("[STRICT_CRITIC] Starting for trace=%s", self._trace_id)

        # Pre-fetch corpus excerpts for grounding
        try:
            tool_result = await search_indian_legal_corpus(
                query=question,
                mode="COMBINED_LEGAL_RESEARCH",
                top_k_laws=DEFAULT_TOP_K_LAWS,
                top_k_cases=DEFAULT_TOP_K_CASES,
                enable_graph=True,
            )
        except Exception as exc:
            logger.error("[STRICT_CRITIC] Initial retrieval failed: %s", exc)
            tool_result = {"chunks": []}

        chunks = tool_result.get("chunks", [])
        all_chunk_ids = [c.get("chunk_id", "") for c in chunks if c.get("chunk_id")]

        # Format corpus context for the LLM
        corpus_context = self._format_observation(chunks, question)

        # Read matter context from Blackboard
        matter_entry = await self._blackboard.get_latest_entry(BlackboardPlane.MATTER_FACTS)
        matter_context = ""
        if matter_entry:
            matter_context = f"Matter Context:\n{json.dumps(matter_entry.data, indent=2)}\n\n"

        user_prompt = (
            f"{matter_context}"
            f"Legal Question: {question}\n\n"
            f"Retrieved Corpus Excerpts:\n{corpus_context}\n\n"
            "Produce your rigorous legal analysis in the JSON schema specified. "
            "Populate chunk_ids_used from the chunk_id values in the corpus excerpts above."
        )

        # Guardrail check
        if _screen_for_injection(user_prompt):
            return {"error": "Prompt injection detected", "statutory_sections": [], "case_citations": [], "chunk_ids_used": []}

        # Single LLM call — temperature=0 for deterministic citations
        llm_request = LLMGenerateRequest(
            user_prompt=user_prompt,
            system_prompt=CRITIC_SYSTEM_PROMPT,
            agent_key=AgentKey.LEGAL_RESEARCH,
            trace_id=self._trace_id,
            temperature=CRITIC_TEMPERATURE,
            max_tokens=MAX_TOKENS_CRITIC,
            model=self._model,
            json_mode=True,
        )
        llm_response = await generate(llm_request)
        parsed_dict, _ = parse_json_response(llm_response.text)
        parsed = parsed_dict if isinstance(parsed_dict, dict) else {}
        # Supplement chunk_ids from retrieval if LLM didn't populate them
        if not parsed.get("chunk_ids_used"):
            parsed["chunk_ids_used"] = all_chunk_ids
        return parsed

    # -----------------------------------------------------------------------
    # Main Entry Point
    # -----------------------------------------------------------------------

    async def execute_research(
        self,
        question: str,
        prompt_profile: PromptProfile = PromptProfile.REACT_LOOP,
    ) -> dict[str, Any]:
        """
        Execute legal research using the specified prompt profile.

        Steps:
          1. Register with A2A bus.
          2. Run the chosen prompt profile (ReAct or Strict Critic).
          3. Post findings to BlackboardPlane.STATUTORY_AUTHORITY.
          4. Send FRAMEWORK_HANDOFF to crewai_synthesis_team via a2a_bus.

        Args:
            question:       The natural-language legal research question.
            prompt_profile: Which reasoning strategy to apply.

        Returns:
            The raw research findings dict (also posted to the Blackboard).
        """
        await self._register_with_bus()

        logger.info(
            "execute_research: profile=%s trace=%s question='%.80s'",
            prompt_profile, self._trace_id, question,
        )

        # --- Execute chosen profile ---
        if prompt_profile == PromptProfile.REACT_LOOP:
            findings = await self._run_react_loop(question)
        elif prompt_profile == PromptProfile.STRICT_CRITIC:
            findings = await self._run_strict_critic(question)
        else:
            raise ValueError(f"Unknown PromptProfile: {prompt_profile}")

        # --- Post findings to the Blackboard ---
        chunk_ids_used = findings.get("chunk_ids_used", [])
        entry = await self._blackboard.post_entry(
            plane=BlackboardPlane.STATUTORY_AUTHORITY,
            author_agent=self.AGENT_NAME,
            author_framework=AgentFramework.GOOGLE_ADK,
            data={
                "question":           question,
                "prompt_profile":     prompt_profile,
                "statutory_sections": findings.get("statutory_sections", []),
                "case_citations":     findings.get("case_citations", []),
                "findings_summary":   findings.get("findings_summary", ""),
                "confidence":         findings.get("confidence", "UNKNOWN"),
                "gap_analysis":       findings.get("gap_analysis", ""),
                "raw_findings":       findings,
            },
            allowed_chunk_ids=chunk_ids_used,
            audit_notes=[f"Generated by {prompt_profile} on trace {self._trace_id}"],
        )
        logger.info(
            "Research posted to Blackboard: entry_id=%s chunk_ids=%d",
            entry.entry_id, len(chunk_ids_used),
        )

        # --- A2A Handoff: trigger CrewAI synthesis team ---
        await a2a_bus.register_agent("crewai_synthesis_team")  # Idempotent
        receipt = await a2a_bus.send(
            A2AMessageEnvelope(
                trace_id=self._trace_id,
                source_agent=self.AGENT_NAME,
                source_framework=AgentFramework.GOOGLE_ADK,
                target_agent="crewai_synthesis_team",
                message_type=MessageType.FRAMEWORK_HANDOFF,
                payload={
                    "question":         question,
                    "blackboard_entry": entry.entry_id,
                    "prompt_profile":   str(prompt_profile),
                    "confidence":       findings.get("confidence", "UNKNOWN"),
                    "chunk_count":      len(chunk_ids_used),
                },
                metadata={"trace_id": self._trace_id},
            )
        )
        logger.info(
            "A2A FRAMEWORK_HANDOFF sent to crewai_synthesis_team (msg_id=%s)",
            receipt.message_id,
        )

        return findings

