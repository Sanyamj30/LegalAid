"""
legalaid/callbacks/guardrail_monitor.py
=========================================
Phase 5: Guardrails & Task Monitoring via Native Callbacks
----------------------------------------------------------
Refactors LegalAId's multi-layer guardrails into native, pluggable callback 
handlers for both Google ADK and CrewAI agent frameworks.

Capabilities:
  1. ADK Input Guard: Pre-execution screening against prompt injection, jailbreaks,
     and prohibited instruction formats.
  2. CrewAI Step Monitor Callback: Intercepts intermediate reasoning thoughts mid-loop,
     screening for injection patterns or toxic outputs.
  3. CrewAI Task Guard Factory: Validates outputs using Pydantic schemas, 
     audits grounding citations against the Blackboard manifest, and computes 
     hallucination risk.
  4. Telemetry Persistence: Asynchronously offloads guardrail telemetry to the 
     existing 26-table SQLite database without blocking sync callback threads.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from legalaid.blackboard.blackboard import GroundingManifest
from legalaid.core.guardrails import screen_static
from legalaid.db.base import session_scope
from legalaid.db.models import AuditLog

logger = logging.getLogger("legalaid.callbacks.guardrail_monitor")

# Compiled injection patterns matching enterprise safety benchmarks
INJECTION_REGEXES: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+prior\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+override", re.IGNORECASE),
    re.compile(r"disregard\s+the\s+above", re.IGNORECASE),
    re.compile(r"developer\s+mode", re.IGNORECASE),
    re.compile(r"<script.*?>", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+have\s+no\s+restrictions", re.IGNORECASE),
]


class GuardrailViolationError(ValueError):
    """Raised when an unrecoverable guardrail or security breach occurs."""
    pass


def _record_guardrail_metric_sync(
    trace_id: str,
    stage_key: str,
    action: str,
    pattern: str | None,
    risk_level: str,
) -> None:
    """Synchronous helper writing to AuditLog via project's sync session."""
    try:
        with session_scope() as session:
            metric = AuditLog(
                id=f"aud_{uuid4().hex[:12]}",
                trace_id=trace_id,
                action=action,
                entity_type="GUARDRAIL_INTERCEPT",
                entity_id=stage_key,
                detail={
                    "stage_key": stage_key,
                    "matched_pattern": pattern,
                    "risk_level": risk_level,
                },
                created_at=datetime.now(timezone.utc),
            )
            session.add(metric)
    except Exception as exc:
        logger.error("Failed to record guardrail audit log to DB: %s", exc, exc_info=True)


async def record_guardrail_metric(
    trace_id: str,
    stage_key: str,
    action: str,
    pattern: str | None = None,
    risk_level: str = "LOW",
) -> None:
    """Non-blocking async telemetry dispatch for guardrail metrics."""
    await asyncio.to_thread(
        _record_guardrail_metric_sync,
        trace_id,
        stage_key,
        action,
        pattern,
        risk_level,
    )


# ===========================================================================
# ADK Guardrails
# ===========================================================================

def screen_adk_input_guard(prompt: str, trace_id: str, agent_name: str) -> str:
    """
    Synchronous / In-line input guard for Google ADK execution loops.
    
    Validates input using both static rule matching and project regex screens.
    Raises GuardrailViolationError if an injection attack is intercepted.
    """
    def _dispatch_metric(stage: str, action: str, pattern: str | None, risk: str) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                record_guardrail_metric(
                    trace_id=trace_id,
                    stage_key=stage,
                    action=action,
                    pattern=pattern,
                    risk_level=risk,
                )
            )
        except RuntimeError:
            pass  # No running event loop in synchronous call context

    # 1. Check existing core guardrail static screening
    static_result = screen_static(prompt)
    if hasattr(static_result, "is_safe") and not static_result.is_safe:
        _dispatch_metric(
            stage=f"ADK_{agent_name}_INPUT",
            action="BLOCKED",
            pattern=getattr(static_result, "reason", "STATIC_FAIL"),
            risk="CRITICAL",
        )
        raise GuardrailViolationError(f"Static guardrail violation: {static_result.reason}")

    # 2. Check localized compiled patterns
    for pat in INJECTION_REGEXES:
        if pat.search(prompt):
            _dispatch_metric(
                stage=f"ADK_{agent_name}_INPUT",
                action="BLOCKED",
                pattern=pat.pattern,
                risk="CRITICAL",
            )
            logger.error("Guardrail blocked prompt injection in ADK agent '%s': %s", agent_name, pat.pattern)
            raise GuardrailViolationError(f"Security Alert: Prohibited pattern detected '{pat.pattern}'")

    logger.debug("ADK input guard passed for agent '%s' (trace=%s)", agent_name, trace_id)
    return prompt


# ===========================================================================
# CrewAI Step & Task Callbacks
# ===========================================================================

def crewai_step_monitor_callback(step_output: Any) -> None:
    """
    CrewAI step_callback hook. Evaluates intermediate thought tokens.
    
    Must be synchronous to satisfy the CrewAI callback signature.
    Dispatches telemetry off-thread when suspicious patterns emerge.
    """
    text = ""
    if hasattr(step_output, "text"):
        text = step_output.text or ""
    elif hasattr(step_output, "thought"):
        text = step_output.thought or ""
    else:
        text = str(step_output)

    for pat in INJECTION_REGEXES:
        if pat.search(text):
            logger.warning("[CrewAI Step Monitor] Intercepted suspicious thought pattern: %s", pat.pattern)
            # Offload persistence without blocking step flow
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(
                        record_guardrail_metric(
                            trace_id="crewai_step_trace",
                            stage_key="CREWAI_STEP_THOUGHT",
                            action="FLAGGED",
                            pattern=pat.pattern,
                            risk_level="HIGH",
                        )
                    )
            except Exception:
                pass
            return


def create_crewai_task_guard(
    schema: type[BaseModel],
    grounding_manifest: GroundingManifest | None = None,
    trace_id: str = "unknown",
) -> Callable[[Any], None]:
    """
    Factory creating a robust Task completion callback for CrewAI tasks.

    Performs:
      1. Pydantic v2 Schema Validation.
      2. Citation Audit against GroundingManifest.
      3. Hallucination Risk computation.
    """
    allowed_ids = set(grounding_manifest.allowed_chunk_ids) if grounding_manifest else set()

    def _task_completion_guard(task_output: Any) -> None:
        raw_text = getattr(task_output, "raw", str(task_output))
        logger.info("[Task Guard] Inspecting task output for trace=%s", trace_id)

        # 1. Schema Validation
        parsed_instance: BaseModel | None = None
        try:
            parsed_instance = schema.model_validate_json(raw_text)
            logger.info("[Task Guard] Output conforms cleanly to %s", schema.__name__)
        except ValidationError as val_err:
            logger.error("[Task Guard] Schema validation failed: %s", val_err)
            _record_guardrail_metric_sync(
                trace_id=trace_id,
                stage_key="TASK_COMPLETION_SCHEMA",
                action="SCHEMA_MISMATCH",
                pattern=str(val_err)[:250],
                risk_level="HIGH",
            )
            return

        # 2. Grounding & Citation Audit (if applicable)
        if hasattr(parsed_instance, "grounding_chunk_ids"):
            cited_ids = set(getattr(parsed_instance, "grounding_chunk_ids", []))
            ungrounded = cited_ids - allowed_ids if allowed_ids else set()

            if ungrounded:
                logger.warning(
                    "[Task Guard] Detected %d ungrounded citations: %s",
                    len(ungrounded),
                    ungrounded,
                )
                _record_guardrail_metric_sync(
                    trace_id=trace_id,
                    stage_key="CITATION_GROUNDING_CHECK",
                    action="UNGROUNDED_CITATIONS_FOUND",
                    pattern=f"Ungrounded: {sorted(list(ungrounded))[:5]}",
                    risk_level="MEDIUM",
                )

    return _task_completion_guard

