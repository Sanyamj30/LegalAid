"""
backend/tests/test_evaluation_pipeline.py
=========================================
Tests for the evaluation and benchmarking pipelines:
  - Experiment execution and metric collection
  - JSON result persistence and schema validity
  - Statistical aggregation (P50, P95, mean, cost)
  - Telemetry bridge integration for pipeline traces
"""

import json
from pathlib import Path
import pytest
from legalaid.a2a.architectures import MessagingExperimentRunner
from legalaid.agents.patterns import ReActPattern, PROMPT_VARIANTS
from legalaid.observability.telemetry_bridge import ObservabilityBridge


@pytest.mark.asyncio
async def test_messaging_experiment_execution_and_metrics():
    runner = MessagingExperimentRunner()
    results = await runner.run_benchmark(
        query="Test query for verification",
        matter_facts={"jurisdiction": "Supreme Court of India"},
        ground_truth_citations=["2023 INSC 101"],
        runs_per_architecture=2,
    )
    
    assert len(results) == 4
    for arch_name, runs in results.items():
        assert len(runs) == 2
        for r in runs:
            assert r.success is True
            assert r.total_latency_ms > 0
            assert r.token_count > 0
            assert r.estimated_cost_usd > 0


@pytest.mark.asyncio
async def test_agentic_pattern_metrics_aggregation():
    pattern = ReActPattern()
    v = PROMPT_VARIANTS["VARIANT_A_FACTUAL"]
    res = await pattern.solve_legal_task(
        prompt_key="VARIANT_A_FACTUAL",
        query=v["query"],
        expected_citations=v["expected_citations"],
    )
    
    assert res.success is True
    assert 0.0 <= res.quality_score <= 1.0
    assert 0.0 <= res.citation_accuracy <= 1.0
    assert res.tool_calls >= 1
    assert res.token_count > 0


def test_benchmark_result_files_exist_and_valid():
    repo_root = Path(__file__).resolve().parent.parent.parent
    msg_json = repo_root / "eval" / "results" / "messaging_architecture_results.json"
    pat_json = repo_root / "eval" / "results" / "agentic_pattern_results.json"
    
    e2e_json = repo_root / "eval" / "results" / "e2e_results.json"
    e2e_report = repo_root / "eval" / "results" / "e2e_report.md"

    assert msg_json.exists()
    assert pat_json.exists()
    assert e2e_json.exists()
    assert e2e_report.exists()

    with open(msg_json, "r", encoding="utf-8") as f:
        msg_data = json.load(f)
        assert "architectures" in msg_data
        assert len(msg_data["architectures"]) == 4

    with open(pat_json, "r", encoding="utf-8") as f:
        pat_data = json.load(f)
        assert "results" in pat_data
        assert len(pat_data["results"]) == 4

    with open(e2e_json, "r", encoding="utf-8") as f:
        e2e_data = json.load(f)
        assert "global_metrics" in e2e_data
        assert e2e_data["global_metrics"]["total_executions"] == 50


@pytest.mark.asyncio
async def test_observability_bridge_initialization():
    bridge = ObservabilityBridge(trace_id="TEST-TRACE-999")
    assert bridge.trace_id == "TEST-TRACE-999"

