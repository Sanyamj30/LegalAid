"""
eval/generate_charts.py
=======================
Generates visual benchmark charts from real empirical results:
  - Latency Comparison (Average & Median)
  - P95 Latency Comparison
  - Token Usage by Architecture / Pattern
  - Cost per Run
  - Success & Citation Accuracy
  - Quality Score Comparison
  - Prompt Variance Breakdown
  - Architecture vs. Pattern Multi-Criteria Radar / Bar
  - Full E2E Pipeline Latency & Breakdown (LLM vs Retrieval vs Overhead)

Saves all figures to: eval/results/charts/
"""

import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

# Ensure results and charts directories exist
repo_root = Path(__file__).resolve().parent.parent
results_dir = repo_root / "eval" / "results"
charts_dir = results_dir / "charts"
charts_dir.mkdir(parents=True, exist_ok=True)

# Use clean styling
plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
plt.rcParams.update({"font.size": 11, "figure.autolayout": True})

# Load raw benchmark JSON files
msg_file = results_dir / "messaging_architecture_results.json"
pat_file = results_dir / "agentic_pattern_results.json"
e2e_file = results_dir / "e2e_results.json"

with open(msg_file, "r", encoding="utf-8") as f:
    msg_data = json.load(f)["architectures"]

with open(pat_file, "r", encoding="utf-8") as f:
    pat_data = json.load(f)["results"]

e2e_data = None
if e2e_file.exists():
    with open(e2e_file, "r", encoding="utf-8") as f:
        e2e_data = json.load(f)


def generate_latency_comparison():
    fig, ax = plt.subplots(figsize=(9, 5))
    names = list(msg_data.keys())
    short_names = [n.replace(" Architecture", "") for n in names]
    avg_latencies = [msg_data[k]["summary"]["avg_latency_ms"] for k in names]
    med_latencies = [msg_data[k]["summary"]["median_latency_ms"] for k in names]

    x = np.arange(len(short_names))
    width = 0.35

    ax.bar(x - width/2, avg_latencies, width, label="Mean Latency (ms)", color="#1f77b4")
    ax.bar(x + width/2, med_latencies, width, label="Median Latency (ms)", color="#aec7e8")

    ax.set_ylabel("Latency (milliseconds)")
    ax.set_title("Messaging Framework Dispatch Overhead (N=15 runs)")
    ax.set_xticks(x)
    ax.set_xticklabels(short_names, rotation=15, ha="right")
    ax.legend()
    fig.savefig(charts_dir / "latency_comparison.png", dpi=200)
    plt.close(fig)


def generate_p95_latency():
    fig, ax = plt.subplots(figsize=(9, 5))
    if e2e_data:
        variants = list(e2e_data["variant_summaries"].keys())
        short_names = [v.replace("VARIANT_", "") for v in variants]
        p95_values = [e2e_data["variant_summaries"][v]["p95_latency_ms"] for v in variants]
        bars = ax.bar(short_names, p95_values, color="#d62728", width=0.5)
        ax.set_ylabel("P95 Latency (milliseconds)")
        ax.set_title("E2E Full-Pipeline P95 Latency by Prompt Variant (50 Runs)")
        ax.bar_label(bars, fmt="%.0f ms", padding=3)
    else:
        pat_names = list(pat_data.keys())
        p95_values = []
        labels = []
        for p in pat_names:
            all_runs = []
            for v in pat_data[p]:
                all_runs.extend([r["latency_ms"] for r in pat_data[p][v]["runs"]])
            all_runs.sort()
            p95 = all_runs[int(0.95 * len(all_runs)) - 1]
            p95_values.append(p95)
            labels.append(p.replace(" (Production)", "").replace(" (Google ADK Pattern)", ""))

        colors = ["#2ca02c", "#ff7f0e", "#9467bd", "#d62728"]
        bars = ax.bar(labels, p95_values, color=colors, width=0.5)
        ax.set_ylabel("P95 Latency (milliseconds)")
        ax.set_title("Agentic Patterns — P95 Dispatch Overhead")
        ax.bar_label(bars, fmt="%.1f ms", padding=3)

    fig.savefig(charts_dir / "p95_latency_comparison.png", dpi=200)
    plt.close(fig)


def generate_token_usage():
    fig, ax = plt.subplots(figsize=(9, 5))
    if e2e_data:
        variants = list(e2e_data["variant_summaries"].keys())
        short_names = [v.replace("VARIANT_", "") for v in variants]
        tokens = [e2e_data["variant_summaries"][v]["mean_tokens"] for v in variants]
        bars = ax.bar(short_names, tokens, color="#17becf", width=0.5)
        ax.set_ylabel("Total Tokens per Run")
        ax.set_title("E2E Full-Pipeline Token Consumption by Prompt Variant")
        ax.bar_label(bars, fmt="%.0f", padding=3)
    else:
        pat_names = list(pat_data.keys())
        labels = [p.replace(" (Production)", "").replace(" (Google ADK Pattern)", "") for p in pat_names]
        avg_tokens = [np.mean([pat_data[p][v]["summary"]["avg_tokens"] for v in pat_data[p]]) for p in pat_names]
        bars = ax.bar(labels, avg_tokens, color="#17becf", width=0.5)
        ax.set_ylabel("Total Tokens per Run")
        ax.set_title("Average Token Consumption by Agentic Pattern")
        ax.bar_label(bars, fmt="%.0f", padding=3)

    fig.savefig(charts_dir / "token_usage.png", dpi=200)
    plt.close(fig)


def generate_cost_per_run():
    fig, ax = plt.subplots(figsize=(9, 5))
    if e2e_data:
        variants = list(e2e_data["variant_summaries"].keys())
        short_names = [v.replace("VARIANT_", "") for v in variants]
        costs = [e2e_data["variant_summaries"][v]["mean_cost_usd"] * 1000 for v in variants]
        bars = ax.bar(short_names, costs, color="#bcbd22", width=0.5)
        ax.set_ylabel("Cost ($ per 1,000 queries)")
        ax.set_title("E2E Query Cost across Prompt Variances (AWS Bedrock Rates)")
        ax.bar_label(bars, fmt="$%.2f", padding=3)
    else:
        pat_names = list(pat_data.keys())
        labels = [p.replace(" (Production)", "").replace(" (Google ADK Pattern)", "") for p in pat_names]
        avg_costs = [np.mean([pat_data[p][v]["summary"]["avg_cost_usd"] * 1000 for v in pat_data[p]]) for p in pat_names]
        bars = ax.bar(labels, avg_costs, color="#bcbd22", width=0.5)
        ax.set_ylabel("Cost per Run ($ / 1,000 queries)")
        ax.set_title("Estimated LLM Inference Cost (AWS Bedrock Rates)")
        ax.bar_label(bars, fmt="$%.2f", padding=3)

    fig.savefig(charts_dir / "cost_per_run.png", dpi=200)
    plt.close(fig)


def generate_success_rate():
    fig, ax = plt.subplots(figsize=(9, 5))
    names = list(msg_data.keys())
    short_names = [n.replace(" Architecture", "") for n in names]
    context_rates = [msg_data[k]["summary"]["context_success_rate"] for k in names]
    citation_rates = [msg_data[k]["summary"]["citation_success_rate"] for k in names]

    x = np.arange(len(short_names))
    width = 0.35

    ax.bar(x - width/2, context_rates, width, label="Context Preservation %", color="#2ca02c")
    ax.bar(x + width/2, citation_rates, width, label="Citation Preservation %", color="#8c564b")

    ax.set_ylabel("Success Percentage (%)")
    ax.set_ylim(0, 115)
    ax.set_title("Context & Citation Preservation Across Messaging Modes")
    ax.set_xticks(x)
    ax.set_xticklabels(short_names, rotation=15, ha="right")
    ax.legend(loc="lower right")
    fig.savefig(charts_dir / "success_rate.png", dpi=200)
    plt.close(fig)


def generate_quality_score():
    fig, ax = plt.subplots(figsize=(9, 5))
    pat_names = list(pat_data.keys())
    labels = [p.replace(" (Production)", "").replace(" (Google ADK Pattern)", "") for p in pat_names]
    qualities = [np.mean([pat_data[p][v]["summary"]["avg_quality_score"] for v in pat_data[p]]) for p in pat_names]
    citations = [np.mean([pat_data[p][v]["summary"]["avg_citation_accuracy"] for v in pat_data[p]]) for p in pat_names]

    x = np.arange(len(labels))
    width = 0.35

    ax.bar(x - width/2, qualities, width, label="Overall Quality (0-1)", color="#3366cc")
    ax.bar(x + width/2, citations, width, label="Citation Accuracy (0-1)", color="#dc3912")

    ax.set_ylabel("Score (0.0 to 1.0)")
    ax.set_ylim(0, 1.15)
    ax.set_title("Synthesis Quality and Citation Precision by Pattern")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend(loc="lower right")
    fig.savefig(charts_dir / "quality_score.png", dpi=200)
    plt.close(fig)


def generate_prompt_variance():
    fig, ax = plt.subplots(figsize=(10, 5))
    if e2e_data:
        variants = list(e2e_data["variant_summaries"].keys())
        short_names = [v.replace("VARIANT_", "") for v in variants]
        mean_latencies = [e2e_data["variant_summaries"][v]["mean_latency_ms"] for v in variants]
        llm_latencies = [e2e_data["variant_summaries"][v]["mean_llm_ms"] for v in variants]
        retrieval_latencies = [e2e_data["variant_summaries"][v]["mean_retrieval_ms"] for v in variants]

        x = np.arange(len(short_names))
        width = 0.55

        ax.bar(x, llm_latencies, width, label="LLM Reasoning Latency", color="#1f77b4")
        ax.bar(x, retrieval_latencies, width, bottom=llm_latencies, label="Retrieval (FastMCP)", color="#ff7f0e")

        ax.set_ylabel("Latency (milliseconds)")
        ax.set_title("E2E Pipeline Latency Breakdown by Prompt Variance")
        ax.set_xticks(x)
        ax.set_xticklabels(short_names)
        ax.legend()
    else:
        variants = list(list(pat_data.values())[0].keys())
        short_variants = ["A: Factual", "B: Complex", "C: Ambiguous", "D: Longform", "E: EdgeCase"]
        patterns = list(pat_data.keys())
        x = np.arange(len(variants))
        width = 0.2
        for i, p in enumerate(patterns):
            latencies = [pat_data[p][v]["summary"]["avg_latency_ms"] for v in variants]
            label = p.replace(" (Production)", "").replace(" (Google ADK Pattern)", "")
            ax.bar(x + (i - 1.5) * width, latencies, width, label=label)
        ax.set_ylabel("Latency (milliseconds)")
        ax.set_title("Dispatch Overhead Across 5 Prompt Variances")
        ax.set_xticks(x)
        ax.set_xticklabels(short_variants)
        ax.legend()

    fig.savefig(charts_dir / "prompt_variance.png", dpi=200)
    plt.close(fig)


def generate_architecture_comparison():
    fig, ax = plt.subplots(figsize=(9, 5))
    names = list(msg_data.keys())
    short_names = [n.replace(" Architecture", "") for n in names]
    writes = [msg_data[k]["summary"]["avg_state_writes"] for k in names]
    reads = [msg_data[k]["summary"]["avg_state_reads"] for k in names]
    msgs = [msg_data[k]["summary"]["avg_messages"] for k in names]

    x = np.arange(len(short_names))
    width = 0.25

    ax.bar(x - width, writes, width, label="State Writes", color="#e377c2")
    ax.bar(x, reads, width, label="State Reads", color="#7f7f7f")
    ax.bar(x + width, msgs, width, label="Message Envelopes", color="#17becf")

    ax.set_ylabel("Operations Count")
    ax.set_title("Messaging vs. Shared State Activity Overhead")
    ax.set_xticks(x)
    ax.set_xticklabels(short_names, rotation=15, ha="right")
    ax.legend()
    fig.savefig(charts_dir / "architecture_comparison.png", dpi=200)
    plt.close(fig)


def main():
    print("Generating updated benchmark charts (Overhead & Full E2E)...")
    generate_latency_comparison()
    generate_p95_latency()
    generate_token_usage()
    generate_cost_per_run()
    generate_success_rate()
    generate_quality_score()
    generate_prompt_variance()
    generate_architecture_comparison()
    print(f"[OK] Successfully generated all 8 charts under: {charts_dir}")


if __name__ == "__main__":
    main()
