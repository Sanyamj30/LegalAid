"""
legalaid/domain/output_models.py
==================================
Phase 7: Structured Output Models
-----------------------------------
Pydantic v2 models for the final structured outputs produced by the
CrewAI Synthesis team and consumed by the API gateway.

These models are the contract between:
  - CrewAI agents (producers)      → always output one of these models
  - FastAPI gateway (consumer)     → serializes to JSON for the client
  - ObservabilityBridge (consumer) → persists to telemetry DB

Design:
  - All models use model_config = ConfigDict(frozen=False) to allow
    post-processing enrichment (e.g., adding confidence fields after generation).
  - ConfidenceLevel uses StrEnum for JSON-safe serialization.
  - LegalSynthesisResponse is the primary output — it contains both the
    human-readable Markdown answer and the structured JSON evidence arrays.
"""

from __future__ import annotations

from enum import StrEnum
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict


# ===========================================================================
# Enumerations
# ===========================================================================

class ConfidenceLevel(StrEnum):
    """
    Qualitative confidence rating for the synthesized legal answer.
    Determined by the CrewAI Synthesis agent based on evidence coverage.
    """
    HIGH     = "HIGH"      # Multiple corroborating statutes + cases, fully verified
    MEDIUM   = "MEDIUM"    # Partial evidence coverage; some gaps remain
    LOW      = "LOW"       # Insufficient evidence; answer is tentative
    UNKNOWN  = "UNKNOWN"   # Confidence could not be determined (e.g., novel question)


# ===========================================================================
# Evidence Finding Models
# ===========================================================================

class StatutoryFinding(BaseModel):
    """
    A single statutory provision identified as applicable to the legal query.
    Each finding must be traceable to a verified chunk_id.
    """
    model_config = ConfigDict(frozen=False)

    chunk_id:       str          = Field(..., description="Verified chunk_id from the Blackboard GroundingManifest")
    act_name:       str          = Field(..., description="Full name of the Act (e.g., 'Indian Contract Act, 1872')")
    section:        str          = Field(..., description="Section number or reference (e.g., 'Section 23')")
    heading:        str          = Field("", description="Section heading/title")
    relevant_text:  str          = Field(..., description="Verbatim or summarized statutory text relevant to the query")
    applicability:  str          = Field(..., description="Explanation of why this provision applies to the matter")
    source_url:     str | None   = Field(None, description="India Code URL or authoritative source link")


class PrecedentFinding(BaseModel):
    """
    A single judicial precedent identified as applicable to the legal query.
    Each finding must be traceable to a verified chunk_id.
    """
    model_config = ConfigDict(frozen=False)

    chunk_id:       str          = Field(..., description="Verified chunk_id from the Blackboard GroundingManifest")
    case_name:      str          = Field(..., description="Full case citation (e.g., 'AIR 2023 SC 1234')")
    court:          str          = Field("Supreme Court of India", description="Adjudicating court")
    year:           int | None   = Field(None, description="Year of judgment")
    holding:        str          = Field(..., description="Key legal principle established by the case")
    applicability:  str          = Field(..., description="Explanation of why this precedent is relevant")
    distinguishing: str          = Field("", description="How this case differs from the current matter (if applicable)")
    source_url:     str | None   = Field(None, description="e-SCR or SCC Online URL")


# ===========================================================================
# Primary Synthesis Output
# ===========================================================================

class LegalSynthesisResponse(BaseModel):
    """
    The definitive structured output of the LegalAId multi-agent system.

    This is the Pydantic model that:
      1. CrewAI Task completion callback validates via model_validate_json()
      2. The FastAPI gateway serializes to the client
      3. The ObservabilityBridge persists to the SQLite audit log

    All fields are required unless marked with a default value.
    The answer_markdown field is the primary human-readable output;
    the structured arrays (applicable_statutes, applicable_precedents) are
    for programmatic downstream consumption.
    """
    model_config = ConfigDict(frozen=False)

    # --- Identity fields ---
    response_id:      str       = Field(default_factory=lambda: f"syn_{uuid4().hex[:12]}")
    trace_id:         str       = Field("trace_unknown", description="Correlates with the originating user request")
    matter_id:        str | None = Field(None, description="Matter ID if this is case-file specific")
    generated_at:     datetime  = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- Primary Answer ---
    answer_markdown:  str       = Field(
        ...,
        description=(
            "The complete legal analysis in Markdown format. Must include: "
            "## Legal Position, ## Applicable Law, ## Relevant Precedents, "
            "## Conclusion, and ## Caveats sections."
        ),
    )

    # --- Structured Evidence Arrays ---
    applicable_statutes:    list[StatutoryFinding]   = Field(default_factory=list)
    applicable_precedents:  list[PrecedentFinding]   = Field(default_factory=list)
    key_takeaways:          list[str]                = Field(
        default_factory=list,
        description="3–5 bullet points summarizing the most actionable legal insights",
    )

    # --- Quality Metadata ---
    confidence:             ConfidenceLevel          = Field(ConfidenceLevel.UNKNOWN)
    evidence_sufficient:    bool                     = Field(
        False,
        description="True if the retrieved evidence fully supports the conclusion",
    )
    hallucination_risk:     str                      = Field(
        "NOT_ASSESSED",
        description="Guardrail assessment: CLEAN | LOW | MEDIUM | HIGH | BLOCKED",
    )

    # --- Grounding Traceability ---
    grounding_chunk_ids:    list[str]                = Field(
        default_factory=list,
        description="All chunk_ids actually cited in this response (subset of GroundingManifest)",
    )
    unverified_claims:      list[str]                = Field(
        default_factory=list,
        description="Claims made in answer_markdown that could not be grounded to a verified chunk",
    )

    # --- Agent Provenance ---
    synthesis_agent:        str                      = Field("crewai_synthesis_team")
    research_agent:         str                      = Field("adk_legal_researcher")
    model_used:             str | None               = Field(None)

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize to a fully JSON-safe dict for API responses and DB persistence."""
        return self.model_dump(mode="json")

    def extract_citation_ids(self) -> set[str]:
        """Returns the union of all chunk_ids cited across statutes and precedents."""
        ids: set[str] = set()
        for s in self.applicable_statutes:
            ids.add(s.chunk_id)
        for p in self.applicable_precedents:
            ids.add(p.chunk_id)
        return ids

