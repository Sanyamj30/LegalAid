"""
backend/tests/test_agentic_patterns.py
======================================
Unit and behavioral tests for the 4 Agentic Design Patterns:
  1. ReAct (Google ADK)
  2. Sequential Execution
  3. Reflection / Critic
  4. Blackboard + ReAct (Production)
"""

import pytest
from legalaid.agents.patterns import (
    ReActPattern,
    SequentialPattern,
    ReflectionCriticPattern,
    BlackboardReActPattern,
    PROMPT_VARIANTS,
)


@pytest.fixture
def sample_variant():
    return PROMPT_VARIANTS["VARIANT_A_FACTUAL"]


@pytest.fixture
def complex_variant():
    return PROMPT_VARIANTS["VARIANT_B_COMPLEX"]


@pytest.mark.asyncio
async def test_react_pattern_execution(sample_variant):
    pattern = ReActPattern()
    res = await pattern.solve_legal_task(
        prompt_key="VARIANT_A_FACTUAL",
        query=sample_variant["query"],
        expected_citations=sample_variant["expected_citations"],
    )
    assert res.success is True
    assert res.pattern_name == "ReAct (Google ADK Pattern)"
    assert res.tool_calls >= 2
    assert res.reasoning_iterations >= 2
    assert res.citation_accuracy > 0.0
    assert res.quality_score >= 0.70
    assert res.structured_output_valid is True


@pytest.mark.asyncio
async def test_sequential_pattern_execution(sample_variant):
    pattern = SequentialPattern()
    res = await pattern.solve_legal_task(
        prompt_key="VARIANT_A_FACTUAL",
        query=sample_variant["query"],
        expected_citations=sample_variant["expected_citations"],
    )
    assert res.success is True
    assert res.pattern_name == "Sequential Execution"
    assert res.tool_calls == 1  # Single static call
    assert res.reasoning_iterations == 1
    assert res.structured_output_valid is True


@pytest.mark.asyncio
async def test_reflection_critic_pattern_execution(complex_variant):
    pattern = ReflectionCriticPattern()
    res = await pattern.solve_legal_task(
        prompt_key="VARIANT_B_COMPLEX",
        query=complex_variant["query"],
        expected_citations=complex_variant["expected_citations"],
    )
    assert res.success is True
    assert res.pattern_name == "Reflection / Critic"
    assert res.tool_calls >= 2
    assert res.reasoning_iterations == 2  # Draft + Critique
    assert res.quality_score >= 0.85
    assert res.citation_accuracy == 1.0


@pytest.mark.asyncio
async def test_blackboard_react_pattern_execution(complex_variant):
    pattern = BlackboardReActPattern()
    res = await pattern.solve_legal_task(
        prompt_key="VARIANT_B_COMPLEX",
        query=complex_variant["query"],
        expected_citations=complex_variant["expected_citations"],
    )
    assert res.success is True
    assert res.pattern_name == "Blackboard + ReAct (Production)"
    assert res.tool_calls >= 2
    assert res.quality_score >= 0.90
    assert res.citation_accuracy == 1.0
    assert "Section 316 BNS" in res.citations_found or "Section 318 BNS" in res.citations_found


@pytest.mark.asyncio
async def test_all_patterns_across_prompt_variants():
    patterns = [ReActPattern(), SequentialPattern(), ReflectionCriticPattern(), BlackboardReActPattern()]
    for p in patterns:
        for pkey, pdata in PROMPT_VARIANTS.items():
            res = await p.solve_legal_task(
                prompt_key=pkey,
                query=pdata["query"],
                expected_citations=pdata["expected_citations"],
            )
            assert res.success is True
            assert res.token_count > 0
            assert res.total_latency_ms > 0

