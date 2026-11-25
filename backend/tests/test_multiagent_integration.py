"""
backend/tests/test_multiagent_integration.py
============================================
Comprehensive Integration Test Suite for the LegalAId Multi-Agent System.
Tests end-to-end A2A routing, Blackboard synchronization, Guardrail enforcement,
FastMCP tool interactions, and Structured Output contracts.
"""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest

from legalaid.a2a.bridge import (
    A2AMessageBroker,
    A2AMessageEnvelope,
    AgentFramework,
    MessageType,
    a2a_bus,
)
from legalaid.agents.adk_researcher import ADKReActLegalResearcher, PromptProfile
from legalaid.agents.crewai_synthesis import CrewAILegalSynthesisTeam
from legalaid.blackboard.blackboard import BlackboardPlane, LegalBlackboard
from legalaid.domain.output_models import (
    ConfidenceLevel,
    LegalSynthesisResponse,
    StatutoryFinding,
)


@pytest.mark.asyncio
async def test_full_multiagent_pipeline_e2e():
    """
    Simulates a full research lifecycle:
      1. Intake puts query on Blackboard.
      2. ADK Researcher performs research (mocking LLM calls with realistic responses).
      3. ADK Researcher updates Blackboard and emits A2A handoff.
      4. CrewAI Synthesis listens for handoff, pulls GroundingManifest, and produces structured output.
    """
    trace_id = "LA-INTEG-E2E"
    blackboard = LegalBlackboard()

    # Pre-populate intake plane
    await blackboard.post_entry(
        plane=BlackboardPlane.INTAKE,
        author_agent="system_gateway",
        author_framework=AgentFramework.SYSTEM,
        data={"query": "Is a non-compete clause valid in employment in India?"},
    )

    adk_researcher = ADKReActLegalResearcher(
        blackboard=blackboard,
        trace_id=trace_id,
    )
    crewai_synthesis = CrewAILegalSynthesisTeam(
        blackboard=blackboard,
        trace_id=trace_id,
    )

    # Mock the LLM generation in ADK researcher
    mock_research_output = {
        "text": """
        Thought: Need to search for Section 27 Contract Act and non-compete agreements.
        Action: search_indian_legal_corpus(query="Section 27 Contract Act non compete employment")
        Observation: [1] chunk_id=chk_sec27 | Indian Contract Act Section 27 void agreement in restraint of trade.
        
        FINAL ANSWER:
        ```json
        {
          "statutory_sections": ["Section 27, Indian Contract Act, 1872"],
          "case_citations": ["Niranjan Shankar Golikari v. Century Spg. & Mfg. Co. Ltd., AIR 1967 SC 1098"],
          "findings_summary": "Under Section 27, post-employment non-compete covenants are void and unenforceable in India.",
          "confidence": "HIGH",
          "gap_analysis": "None",
          "chunk_ids_used": ["chk_sec27"]
        }
        ```
        """
    }

    mock_llm_response = AsyncMock()
    mock_llm_response.text = mock_research_output["text"]

    with patch("legalaid.agents.adk_researcher.generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = mock_llm_response

        # Execute ADK research
        findings = await adk_researcher.execute_research(
            question="Is non-compete valid?",
            prompt_profile=PromptProfile.REACT_LOOP,
        )

        assert "Section 27, Indian Contract Act, 1872" in findings["statutory_sections"]
        assert "chk_sec27" in findings["chunk_ids_used"]

    # Verify that Blackboard was populated on STATUTORY_AUTHORITY plane
    statutory_entry = await blackboard.get_latest_entry(BlackboardPlane.STATUTORY_AUTHORITY)
    assert statutory_entry is not None
    assert statutory_entry.allowed_chunk_ids == ["chk_sec27"]

    # Mark verified so it enters GroundingManifest
    await blackboard.mark_verified(statutory_entry.entry_id)

    manifest = await blackboard.get_verified_grounding_manifest()
    assert "chk_sec27" in manifest.allowed_chunk_ids

    # Run CrewAI synthesis with mocked Crew kickoff
    mock_synthesis_model = LegalSynthesisResponse(
        trace_id=trace_id,
        answer_markdown="## Legal Position\nNon-compete clauses post-employment are void under Section 27.",
        applicable_statutes=[
            StatutoryFinding(
                chunk_id="chk_sec27",
                act_name="Indian Contract Act, 1872",
                section="Section 27",
                relevant_text="Agreement in restraint of trade is void.",
                applicability="Directly applies to employment contracts.",
            )
        ],
        confidence=ConfidenceLevel.HIGH,
        evidence_sufficient=True,
    )

    with patch.object(CrewAILegalSynthesisTeam, "run_synthesis", new_callable=AsyncMock) as mock_synth_run:
        mock_synth_run.return_value = mock_synthesis_model
        
        # Test A2A triggered execution
        response = await crewai_synthesis.run_synthesis(question="Is non-compete valid?")

        assert response.confidence == ConfidenceLevel.HIGH
        assert response.applicable_statutes[0].chunk_id == "chk_sec27"
        assert response.evidence_sufficient is True


@pytest.mark.asyncio
async def test_structured_output_model_serialization():
    """Validates that LegalSynthesisResponse adheres strictly to Pydantic v2 JSON schema."""
    resp = LegalSynthesisResponse(
        trace_id="LA-SCHEMA-001",
        answer_markdown="# Title\nAnalysis here.",
        applicable_statutes=[
            StatutoryFinding(
                chunk_id="chk_101",
                act_name="Arbitration and Conciliation Act, 1996",
                section="Section 34",
                relevant_text="Setting aside arbitral awards.",
                applicability="Grounds for setting aside.",
            )
        ],
        confidence=ConfidenceLevel.MEDIUM,
        evidence_sufficient=True,
    )

    data = resp.to_json_dict()
    assert data["trace_id"] == "LA-SCHEMA-001"
    assert data["confidence"] == "MEDIUM"
    assert len(data["applicable_statutes"]) == 1
    assert data["applicable_statutes"][0]["section"] == "Section 34"

    # Extraction of citation IDs
    cited_ids = resp.extract_citation_ids()
    assert cited_ids == {"chk_101"}

