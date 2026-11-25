"""
backend/tests/test_messaging_architectures.py
=============================================
Unit and integration tests for the 4 Messaging Architectures:
  1. Direct Agent-to-Agent
  2. Shared Blackboard
  3. Asynchronous A2A Broker
  4. Hybrid Blackboard + A2A
"""

import pytest
import asyncio
from legalaid.a2a.architectures import (
    DirectMessagingArchitecture,
    BlackboardMessagingArchitecture,
    AsyncA2ABrokerArchitecture,
    HybridMessagingArchitecture,
    MessagingExperimentRunner,
)


@pytest.fixture
def mock_facts():
    return {
        "jurisdiction": "Supreme Court of India",
        "case_type": "Criminal Appeal",
        "accused": "Appellant A",
        "allegations": "Inducement and fraudulent delivery of property under Section 420 IPC / Section 318 BNS",
    }


@pytest.fixture
def sample_query():
    return "What are the essential ingredients of cheating under Section 318 BNS compared to Section 420 IPC?"


@pytest.fixture
def ground_truth_citations():
    return ["2023 INSC 101", "AIR 2022 SC 450"]


@pytest.mark.asyncio
async def test_direct_messaging_architecture(sample_query, mock_facts, ground_truth_citations):
    arch = DirectMessagingArchitecture()
    res = await arch.execute_task(sample_query, mock_facts, ground_truth_citations)
    
    assert res.success is True
    assert res.architecture_name == "Direct Agent-to-Agent"
    assert res.context_success is True
    assert res.citation_success is True
    assert res.message_count == 3
    assert res.state_writes == 0
    assert res.state_reads == 0
    assert res.total_latency_ms > 0
    assert len(res.citations_preserved) > 0


@pytest.mark.asyncio
async def test_blackboard_messaging_architecture(sample_query, mock_facts, ground_truth_citations):
    arch = BlackboardMessagingArchitecture()
    res = await arch.execute_task(sample_query, mock_facts, ground_truth_citations)
    
    assert res.success is True
    assert res.architecture_name == "Shared Blackboard"
    assert res.context_success is True
    assert res.citation_success is True
    assert res.message_count == 0  # Decoupled via Blackboard
    assert res.state_writes >= 3
    assert res.state_reads >= 3
    assert res.total_latency_ms > 0
    assert len(res.citations_preserved) > 0


@pytest.mark.asyncio
async def test_a2a_broker_messaging_architecture(sample_query, mock_facts, ground_truth_citations):
    arch = AsyncA2ABrokerArchitecture()
    res = await arch.execute_task(sample_query, mock_facts, ground_truth_citations)
    
    assert res.success is True
    assert res.architecture_name == "Asynchronous A2A Broker"
    assert res.context_success is True
    assert res.citation_success is True
    assert res.message_count == 2
    assert res.state_writes == 0
    assert res.state_reads == 0
    assert res.total_latency_ms > 0
    assert len(res.citations_preserved) > 0


@pytest.mark.asyncio
async def test_hybrid_messaging_architecture(sample_query, mock_facts, ground_truth_citations):
    arch = HybridMessagingArchitecture()
    res = await arch.execute_task(sample_query, mock_facts, ground_truth_citations)
    
    assert res.success is True
    assert res.architecture_name == "Hybrid Blackboard + A2A"
    assert res.context_success is True
    assert res.citation_success is True
    assert res.message_count == 2
    assert res.state_writes >= 3
    assert res.state_reads >= 2
    assert res.total_latency_ms > 0
    assert len(res.citations_preserved) > 0


@pytest.mark.asyncio
async def test_messaging_experiment_runner(sample_query, mock_facts, ground_truth_citations):
    runner = MessagingExperimentRunner()
    results = await runner.run_benchmark(
        query=sample_query,
        matter_facts=mock_facts,
        ground_truth_citations=ground_truth_citations,
        runs_per_architecture=2,
    )
    
    assert len(results) == 4
    for name, runs in results.items():
        assert len(runs) == 2
        for r in runs:
            assert r.success is True
            assert r.context_success is True

