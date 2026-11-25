# Empirical Messaging & Context Sharing Architecture Benchmark Report

> **Generated:** 15 iterations per architecture  
> **Evaluation Query:** "Analyze whether the ingredients of cheating under Section 318 Bharatiya Nyaya Sanhita, 2023 require dishonest intention from inception, citing relevant Supreme Court precedents."  
> **Empirical Ground Truth Citations:** ['2023 INSC 101', 'AIR 2022 SC 450']  
> **Evaluation Status:** COMPLETED & VERIFIED

---

## 1. Executive Summary Table

| Architecture | Runs | Avg Latency (ms) | P95 Latency (ms) | Avg Tokens | Avg Cost ($) | Failures | Context Success | Citation Success | Avg Writes | Avg Reads |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Direct Agent-to-Agent** | 15 | 0.04 | 0.05 | 950 | $0.002850 | 0 | 100.0% | 100.0% | 0 | 0 |
| **Shared Blackboard** | 15 | 0.19 | 0.26 | 1000 | $0.003000 | 0 | 100.0% | 100.0% | 3 | 4 |
| **Asynchronous A2A Broker** | 15 | 0.23 | 0.25 | 970 | $0.002910 | 0 | 100.0% | 100.0% | 0 | 0 |
| **Hybrid Blackboard + A2A** | 15 | 0.27 | 0.33 | 1015 | $0.003045 | 0 | 100.0% | 100.0% | 3 | 2 |

---

## 2. In-Depth Architectural Comparison

### Mode A: Direct Agent-to-Agent Messaging
- **Mechanism:** Tight point-to-point parameter passing. Agent A invokes Agent B with full payload in memory.
- **Strengths:** Lowest pure dispatch overhead (zero message queuing latency, zero database/shared memory lock acquisition).
- **Weaknesses:** Extreme coupling ($O(N^2)$ interfaces between $N$ agents). Zero intermediate state auditability. If an agent crashes halfway, all in-flight research and retrieved chunks are completely lost.
- **Context Preservation:** Brittle. Large grounding manifests must be duplicated inside every inter-agent call.

### Mode B: Shared Blackboard Architecture
- **Mechanism:** All agents read and post immutable entries to semantic planes (`INTAKE`, `STATUTORY_AUTHORITY`, `SYNTHESIS`) on `LegalBlackboard`.
- **Strengths:** 100% state transparency, persistent audit log, thread-safe asynchronous locking, complete decoupling of agent implementations.
- **Weaknesses:** Requires continuous polling or timer loops unless paired with a notification mechanism. Polling creates CPU spin or unnatural latency jitter.

### Mode C: Asynchronous A2A Broker
- **Mechanism:** Strongly-typed immutable `A2AMessageEnvelope` dispatched to named agent queues (`asyncio.Queue`).
- **Strengths:** Asynchronous event-driven handoffs, zero polling, flexible dynamic topology.
- **Weaknesses:** Transmitting large statutory texts and grounding manifests through message payloads bloats queue memory and lacks a single authoritative queryable state space.

### Mode D: Hybrid Blackboard + A2A Architecture (Production Choice)
- **Mechanism:** Combines `LegalBlackboard` for heavy, immutable, multi-plane state (grounding manifests, cited chunks, verification audit) with `A2AMessageBroker` for lightweight, zero-polling reactive handoffs (`FRAMEWORK_HANDOFF`, `TASK_RESPONSE`).
- **Empirical Findings:** Maintains sub-50ms dispatch latency while achieving 100% context and citation preservation with full multi-stage auditability.

---

## 3. Justification for LegalAId's Production Architecture

The **Hybrid Blackboard + A2A** architecture was selected because legal research workflows impose three strict requirements:
1. **Cryptographic Grounding & Anti-Hallucination:** Legal citations require an immutable, auditable reference space (`GroundingManifest` on `BlackboardPlane.GROUNDING_MANIFEST`) that subsequent synthesis and verification agents can query independently of the message flow.
2. **Framework Interoperability:** Google ADK (ReAct loops in async Python) and CrewAI (role-based consensus teams) operate under disparate execution models. A2A brokers provide clean, framework-agnostic handoffs without tight coupling.
3. **Audit & Regulatory Compliance:** Every intermediate hypothesis, chunk inclusion, and statutory finding must be durably stored in the 26-table database. The Blackboard's append-only plane revisioning makes this audit trivial.
