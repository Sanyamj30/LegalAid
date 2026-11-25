# LegalAId: Evaluation & Benchmarking Infrastructure

This directory houses the comprehensive benchmarking suites for LegalAId's multi-agent architectures, agentic design patterns, and prompt variance matrices.

---

## 1. Directory Structure

```
eval/
├── README.md                              # This reproduction & documentation guide
├── messaging_architecture_benchmark.py    # Benchmark runner for 4 messaging modes (dispatch overhead)
├── agentic_pattern_benchmark.py           # Benchmark runner for 4 agentic patterns across 5 variants (dispatch overhead)
├── e2e_llm_pipeline_benchmark.py          # Full-pipeline E2E benchmark (simulated LLM inference + FastMCP + CrewAI)
├── generate_charts.py                     # Visual chart generator (8 output figures)
├── end_to_end_evaluator.py                # Comparative evaluator (ADK ReAct vs Few-Shot with live/fallback LLM)
├── dataset.py                             # Legal research test queries
├── metrics.py                             # Precision, Recall, and citation scorers
└── results/
    ├── messaging_architecture_results.json # Raw telemetry from messaging benchmark
    ├── messaging_architecture_report.md   # Defensible Markdown analysis
    ├── agentic_pattern_results.json       # Raw telemetry from agentic pattern benchmark
    ├── agentic_pattern_report.md          # Multi-criteria evaluation report
    ├── e2e_results.json                   # Raw telemetry from full E2E pipeline benchmark
    ├── e2e_report.md                      # Full pipeline analysis report
    └── charts/                            # 8 Generated empirical PNG figures
        ├── architecture_comparison.png
        ├── cost_per_run.png
        ├── latency_comparison.png
        ├── p95_latency_comparison.png
        ├── prompt_variance.png
        ├── quality_score.png
        ├── success_rate.png
        └── token_usage.png
```

---

## 2. Benchmark Tiers & Methodology

To ensure strict scientific rigor, benchmarks are structured into two distinct operational tiers:

1. **Agentic Framework & Dispatch Overhead Benchmarks** (`messaging_architecture_benchmark.py`, `agentic_pattern_benchmark.py`):
   - Measures pure Python in-memory framework latency (A2A message routing, Blackboard atomic read/writes, schema parsing, guardrail evaluations).
   - Observed Latency: **12.8 ms to 31.3 ms**.
2. **End-to-End (E2E) Full-Pipeline Benchmark** (`e2e_llm_pipeline_benchmark.py`):
   - Measures complete simulated workflow: Gateway $\rightarrow$ Pre-Flight Guardrails $\rightarrow$ ADK ReAct $\rightarrow$ FastMCP Tool Retrieval $\rightarrow$ LLM Reasoning Passes $\rightarrow$ CrewAI Consensus $\rightarrow$ Grounding Verification $\rightarrow$ Pydantic Serialization $\rightarrow$ SQLite Telemetry.
   - Observed Latency: **Mean 5,517 ms, P50 5,563 ms, P95 5,812 ms**.
   - Token & Cost Metrics: **Modeled estimates** based on prompt profile footprints mapped to AWS Bedrock pricing schedule (Llama 3.3 70B: \$0.00072/1k in/out; Claude 3.5 Sonnet: \$0.003/1k in, \$0.015/1k out).

---

## 3. Reproduction Commands

### Step 1: Messaging Architecture Comparison ($N \ge 10$ runs)
Evaluates Direct Agent-to-Agent, Shared Blackboard, Asynchronous A2A Broker, and Hybrid Blackboard + A2A:
```bash
python eval/messaging_architecture_benchmark.py
```
*Outputs: `eval/results/messaging_architecture_results.json` and `eval/results/messaging_architecture_report.md`.*

### Step 2: Agentic Design Patterns Comparison (200 Total Runs)
Evaluates ReAct (Google ADK), Sequential, Reflection/Critic, and Blackboard+ReAct across 5 distinct prompt variants:
```bash
python eval/agentic_pattern_benchmark.py
```
*Outputs: `eval/results/agentic_pattern_results.json` and `eval/results/agentic_pattern_report.md`.*

### Step 3: End-to-End Full Pipeline Benchmark (50 Total Runs)
Evaluates full operational execution across 5 prompt variants ($N=10$ runs per variant):
```bash
python eval/e2e_llm_pipeline_benchmark.py
```
*Outputs: `eval/results/e2e_results.json` and `eval/results/e2e_report.md`.*

### Step 4: Automated Chart Generation
Generates all 8 publication-ready evaluation charts from the empirical JSON datasets:
```bash
python eval/generate_charts.py
```
*Outputs: PNG charts in `eval/results/charts/`.*

---

## 4. Evaluated Dimensions & Metrics

| Metric | Definition | Source |
| :--- | :--- | :--- |
| **Mean / P50 / P95 Latency** | Wall-clock execution time in milliseconds | High-resolution Python monotonic timer (`perf_counter`) |
| **Token Consumption (Estimated)** | Modeled prompt + completion tokens per stage | Stage footprint modeling based on prompt profiles |
| **Cost Estimation ($ Estimated)** | Dollar cost per run mapped to AWS Bedrock pricing schedule | Mathematical mapping to AWS Bedrock rates |
| **Citation Precision** | Ratio of retrieved citations that match ground truth | `set(retrieved).intersection(ground_truth) / len(ground_truth)` |
| **Quality Score (0-1)** | Multi-criteria score combining statutory grounding and reasoning coherence | Grounding manifest validation |
| **State Operations** | Total Blackboard atomic writes and reads executed | `LegalBlackboard` revision logs |
| **Message Envelopes** | Number of typed `A2AMessageEnvelope` objects dispatched | `A2AMessageBroker` queue metrics |

---

## 5. Prompt Variance Matrix

1. **Variant A (Direct Factual):** Low complexity procedural inquiry (Order XXXVII CPC summary suits).
2. **Variant B (Complex Multi-Issue):** High complexity comparison of criminal breach of trust (Section 316 BNS) vs. cheating (Section 318 BNS).
3. **Variant C (Ambiguous Procedural):** Medium-high complexity evaluation of statutory default bail on the 90th day under Section 187 BNSS / Section 167(2) CrPC.
4. **Variant D (Long-Form Constitutional):** High complexity analysis of retrospective criminal legislation under Article 20(1) of the Constitution of India.
5. **Variant E (Adversarial Edge-Case):** Adversarial prompt-injection attempt combined with an inquiry into property attachment under Sections 83-84 CrPC.

