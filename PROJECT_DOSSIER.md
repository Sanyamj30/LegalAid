# PROJECT INFORMATION DOSSIER — LegalAId

**Audit basis:** codebase inspected directly on 2026-09-18 (source files read, not just docs).
**Status legend:** ✅ implemented (verified in code/data) · 🟡 partial · 🔵 planned · ❌ absent.
Every factual claim cites a file, module, or recorded evaluation artifact. Numbers are taken
from recorded results only; nothing is invented.

---

## 1. Project Overview

**LegalAId** is an AI-assisted **Indian legal research workspace**. A lawyer asks a question or
pastes/uploads an FIR, legal notice, or agreement; the system searches genuine Indian legislation
and a curated set of Supreme Court judgments and returns the applicable provisions, comparable
precedent, its own reasoning, and (if asked) a draft. Every internal step — classification, query
rewrite, vector search, per-agent context (including what each agent was *denied*), exact prompts,
raw model output — is recorded and inspectable in the UI (`README.md:1-15`).

- **Stack:** React 18 + TypeScript + Vite frontend (13 pages), FastAPI gateway + 4 logical
  services (llm, embedding, retrieval, + in-process handlers), SQLAlchemy + SQLite (26 tables),
  Ollama local LLMs, FAISS + hand-rolled BM25 + symbolic graph retrieval, Docker Compose.
- **Corpus (verified in docs/evaluation.md:42):** **1,783 statutory provisions across 15 Acts**
  fetched section-by-section from India Code (official GoI repository) + **47 Supreme Court
  judgments** from the CC-BY-4.0 e-SCR open dataset = **2,575 chunks**. An earlier 263-provision
  corpus is archived in `eval/results/corpus_263/`.
- **Agents:** 6 LLM agents (Intake Router, Document Understanding, Legal Research, Similar Cases,
  Drafting, Synthesis) + 1 Verification critic (deterministic audit + LLM review), coordinated by
  a **deterministic (non-LLM) orchestrator** (`backend/src/legalaid/agents/orchestrator.py`).
- **Quality gates:** 159 tests in 13 files (`backend/tests/`), evaluation harness in `eval/`
  producing `docs/evaluation.md` from recorded JSON, 40 recorded full-pipeline traces.

**Overall status:** end-to-end pipeline functionally complete, evaluated, and deployed; a set of
course-specific gaps remains (§24). NOTE: `data/` is empty in this repo copy — the live SQLite DB
and FAISS indexes are not shipped; recorded evidence lives in `eval/results/*.json`.

## 2. Problem Statement

Indian legal research is slow, fragmented across official sources (India Code, e-SCR), and generic
LLMs confidently hallucinate statutes. A documented, reproducible failure motivated the project:
asked without retrieval, Code Llama answers that Contract Act s.73 "deals with the sale of goods"
(it is actually compensation for breach) — `.kiro/PROGRESS.md:76-84`. For a lawyer, a fabricated
section or a repealed provision cited as live law is worse than no answer. The problem is therefore
**grounded, citable, provenance-tracked legal research that can refuse when the evidence is
insufficient**, not open-ended chat.

## 3. Objectives

- **Primary:** a multi-agent pipeline that maps a matter to applicable statutes and similar
  precedent, with every citation traceable to a retrieved chunk and an official source URL
  (`docs/architecture.md` design goals 2–5: provenance, genuine multi-agent behaviour, correct
  boundaries, honesty).
- **Secondary (all implemented):** query rewriting before embedding; hybrid retrieval
  (dense + BM25 + graph, RRF-fused); per-agent context isolation with recorded exclusions;
  deterministic grounding audit + refinement loop; input/output guardrails; full observability
  (traces, agent runs, model runs); local-only LLMs for privacy; `evidence_sufficient: false` as a
  first-class outcome; Docker deployment.
- **KPIs actually measured (no invented values):** retrieval recall/MRR; answer accuracy vs
  ground-truth sections; citation precision; hallucination rate (citation-level); refusal
  discipline on unanswerable tasks; schema compliance; latency per agent/stage; tokens &
  throughput; guardrail catch rate / false-positive rate; corpus duplicate rate.

## 4. Target Users / Personas

- **Primary:** practising Indian advocates — solo practitioners and small firms needing grounded
  statutory/precedent research and first drafts. UI splits "For you" (Home, Ask LegalAId, My
  documents, Law library) from "Developer / evaluation" pages (`.kiro/PROGRESS.md:354-392`).
- **Secondary:** evaluators/examiners (Pipeline Trace, Agents, Retrieval Inspector, Guardrails,
  System pages make every claim demonstrable).
- **Firm mode is forward-compatible, not implemented:** schema carries `organizations`, `roles`,
  `permissions`, `memberships`, `matter_members`, `document_permissions`, `audit_logs`; seed
  creates 7 roles / 14 permissions / 68 grants (`legalaid/db/seed.py`, `docs/architecture.md:271-279`).

## 5. Domain and Environment

- **Domain:** Indian law — statute identification, precedent comparison, drafting (Indian Contract
  Act 1872, BNS/BSA 2023, CPC 1908, IT Act 2000, Sale of Goods Act 1930, Arbitration Act, and
  others; 15 Acts total).
- **Operating environment:** single workstation (Ryzen 5 7235HS, RTX 3050 6 GB, 25 GB RAM),
  Ollama on host, Python 3.13, Node 22, Docker optional (`docs/architecture.md:181-209`).
  Full pipeline takes **3–7 minutes** per question at measured ~12–15 tok/s.
- **Inputs:** natural-language question; pasted text; uploaded PDF/TXT/DOCX/MD ≤ 25 MB
  (`config.py:169-172`).
- **Outputs:** synthesised markdown answer (`answer_markdown`, `key_points`, `inference`,
  `uncertainties`, `next_steps`, `confidence`), structured per-agent findings, optional draft, and
  a complete trace (`docs/api.md`).
- **Deployment:** `docker-compose.yml` — 7 services (ollama, model-init, gateway, llm, embedding,
  retrieval, frontend) + 2 volumes; Ollama defaults to host (GPU), containerised via `bundled`
  profile.

## 6. Overall System Workflow

Actual flow as implemented in `orchestrator.py` (`Orchestrator.run`, lines 162-526):

```
User → INPUT_GUARDRAIL (6 deterministic-first layers, core/guardrails.py)
     → INTAKE_CLASSIFICATION + QUERY_REWRITE (Intake Router: classify, decide agents, rewrite
        the text that will be embedded)
     → CONTEXT_CONSTRUCTION: hybrid retrieval — dense FAISS + BM25 + graph, RRF fusion
        (services/retrieval/)
     → DOCUMENT_UNDERSTANDING (only when matter documents present)
     → LEGAL_RESEARCH ∥ SIMILAR_CASES  (concurrent via asyncio.gather, orchestrator.py:388-396)
     → per-agent CITATION_VALIDATION (deterministic audit) → REFINEMENT_LOOP (retry with
        corrective guidance; LLM critic on failure; ≤2 iterations) → strip unsupported citations
     → DRAFTING (router-gated, depends on both research agents)
     → SYNTHESIS (always; writes the lawyer-facing answer)
     → OUTPUT_GUARDRAIL (groundedness scoring; downgrades confident-but-ungrounded answers)
     → User  (all stages streamed live over SSE /api/research/stream)
```

The one-paste version from `README.md:79-104` matches the code.

## 7. Multi-Agent Architecture

- **Pattern:** manager/orchestrator + workflow with one parallel stage. The orchestrator is
  **deterministic code, not a prompt** — "deterministic, inspectable and testable, which is what
  makes the multi-agent claim defensible" (`docs/agents.md:7-9`).
- **Why multi-agent instead of single-agent (evidence-backed):**
  1. Each agent has a **different retrieval scope and isolated context window** (statute-only vs
     precedent-only vs findings-only), with exclusions persisted per run
     (`docs/agents.md:103-114`, `agents/base.py:62-136`) — a single mega-prompt cannot have this.
  2. The two independent research agents run **concurrently**, roughly halving the slowest stage
     (`orchestrator.py:22-25, 339-396`).
  3. Role separation enables **per-role auditing**: drafting/synthesis never see raw chunks, so
     they cannot introduce an unretrieved authority (`agents/drafting.py:1-9`, `synthesis.py:1-12`).
  4. Measured: 86.2% of wall time is model generation, so the decomposition adds near-zero
     coordination cost while adding isolation and auditability (`eval/results/latency_breakdown.json`).

## 8. Agent-by-Agent Analysis

### 8.0 Orchestrator (Coordinator/Manager — deterministic, NOT an LLM agent)
- **File:** `backend/src/legalaid/agents/orchestrator.py`.
- **Objective:** plan which agents run, isolate each context, run the refinement loop, assemble
  and refuse/annotate honestly.
- **Input:** question, pasted text, matter id, draft options. **Output:** `OrchestrationResult`
  (per-agent outputs, answer, routing, plan, loop iterations, guardrail verdicts, warnings).
- **Responsibilities:** create trace; input guardrails; dispatch router; retrieval; concurrency;
  audit/loop; synthesis; output guardrail; close trace (`run()` 162-526; `_run_with_loop` 680-843).
- **Status:** ✅. **Bottleneck:** none measurable (bookkeeping stages ~2.4 ms).
- **Metrics:** total pipeline time; loop iterations; warnings; refusal reasons.

### 8.1 Intake Router (Agent 0 — decision/routing specialist)
- **File:** `agents/intake_router.py`; prompt `prompts/intake_router/v1.yaml` (v1.0, temp 0.1,
  max_tokens 420).
- **Objective:** classify input (question / document / both), identify document type, decide which
  agents are needed, gate scope (`is_legal_matter`, **fails closed** — defaults `False`,
  `intake_router.py:46-49`), and **rewrite the input into the text that gets embedded**.
- **Input:** raw user input (fenced `<user_request>`) + document excerpts (fenced `<attachment>`).
- **Output:** `RoutingDecision` — input_kind, document_type, needs_law/cases/drafting/document_facts,
  is_legal_matter, search_query (rewrite), legal_concepts, suggested_draft_type, reasoning.
- **LLM:** any configured generation model (per-request override). **Tools:** none. **RAG:** no.
- **Memory:** none (stateless). **Communication:** returns decision to orchestrator only.
- **Trigger:** always first, before retrieval.
- **Measured:** mean 4.8 s (39.8%→ classification is 6.8% of wall time; latency JSON stage
  INTAKE_CLASSIFICATION count 7, mean 4816 ms).
- **Status:** ✅. Rewrite is measured: recall 0.500 → 1.000 on the affected task subset
  (`eval/results/rewrite_experiment.json`, `docs/evaluation.md:262-277`).
- **Bottleneck:** an LLM call in the critical path; has a deterministic fallback
  (`fallback_decision`) that takes scope from the deterministic guardrail, flagged `used_fallback`.

### 8.2 Document Understanding (Agent 4 — extraction specialist)
- **File:** `agents/document_understanding.py`; prompt `prompts/document_understanding/v1.yaml`.
- **Objective:** extract parties, dates, amounts, obligations from matter documents; its output
  becomes the "verified facts" downstream agents may rely on.
- **Input:** USER_DOCUMENTS chunks only (untrusted → fenced). **Denied:** all statute + case law.
- **Output:** verified facts with chunk tags. **Trigger:** only when retrieval returns user-doc
  chunks AND router set `needs_document_facts` (mode widened so this actually happens —
  `orchestrator.py:592-610`, fixing WORKSHEET bug B11).
- **Measured:** mean 12.5 s, 17.6% of wall time (7 runs). **Status:** ✅ (skipped with recorded
  reason when no documents).
- **Bottleneck:** whole-page OCR text can be long; prompt trimming capped at 700 chars/chunk.

### 8.3 Legal Research (Agent 1 — retrieval+generation worker)
- **File:** `agents/legal_research.py`; prompt `prompts/legal_research/v1.yaml` (v1.3, temp 0.1,
  620 tokens).
- **Objective:** identify applicable Acts/sections from retrieved statute chunks; never invent.
- **Input:** rewritten question, matter summary, **5 law chunks + 2 doc chunks** (700 chars each,
  Illustrations stripped — `base.py:458-497`). **Denied:** all case-law chunks, recorded with
  reason (`legal_research.py:57-64`).
- **Output schema:** legal_issues, applicable_laws, sections (each with chunk_id), reasoning,
  uncertainties, evidence_sufficient. **Validate:** every section must cite a supplied chunk_id
  (`legal_research.py:77-98`).
- **Measured:** mean 12.4 s (7 pipeline runs; 39 recorded agent-run executions in latency JSON
  including eval-harness runs). Model comparison used exactly this agent: accuracy 55.6% best
  (`llama3.2:3b`). **Status:** ✅. **Bottleneck:** generation-bound; recall ceiling of retrieval
  (0.682 raw) propagates.

### 8.4 Similar Case (Agent 2 — retrieval+generation worker)
- **File:** `agents/similar_cases.py`; prompt `prompts/similar_cases/v1.yaml` (temp 0.15 per
  `docs/agents.md:75`).
- **Objective:** find factually/legally similar Indian precedents with honest similarity and
  differences.
- **Input:** case-law chunks (metadata-filtered) + a few law provisions for grounding. **Denied:**
  matter documents.
- **Output:** cases[] (case_title, court, citation, **similarity_score taken from vector search,
  model may not overwrite it**, why_similar, relevant_principle, outcome, differences,
  practical_relevance, chunk_ids), evidence_sufficient.
- **Measured:** **mean 16.4 s — 39.7% of wall time; the single largest stage** (p95 28.4 s).
  **Status:** ✅. **Bottleneck:** largest agent; thin precedent corpus (47 judgments, 2023 slice)
  means it often reports `evidence_sufficient: false` honestly (`README.md:149-152`).

### 8.5 Drafting (Agent 3 — generation specialist)
- **File:** `agents/drafting.py`; prompt `prompts/drafting/v1.yaml` (temp 0.2).
- **Objective:** produce a lawyer-reviewable draft grounded strictly in verified findings.
- **Input:** verified facts + Legal Research output + Similar Case output + user instructions +
  type/tone/length. **Denied: no raw chunks at all** — upstream provenance items carry
  `source_label` so the audit can still check citations (`base.py:49-59`, `orchestrator.py:84-100`).
- **Output:** markdown draft + grounding + unverified_flags; must emit
  `[INFORMATION NOT AVAILABLE: ...]` for gaps; `prose_fields=("draft_markdown",)` is audited.
- **Measured:** 13.1 s mean but only **2 recorded runs** (router rarely requests drafting in the
  recorded traces). **Status:** ✅. **Bottleneck:** depends on both research agents (serial tail).

### 8.6 Verification (Agent 5 — validation agent; two-tier)
- **File:** `agents/verification.py`; prompt `prompts/verification/v1.yaml` (v1.1, **temp 0.0**).
- **Layer 1 — deterministic `audit()` (authoritative, cannot hallucinate):** set membership of
  cited chunk_ids against `allowed_chunk_ids`; section numbers against supplied text; case titles
  against supplied labels; **plus prose scan** of `answer_markdown`/`draft_markdown` for invented
  section numbers and Indian reporter citations (regexes `verification.py:28-38, 161-181`).
- **Layer 2 — LLM critic:** runs only when the deterministic audit found problems; returns
  verdict, unsupported_claims, overconfident_statements, retry_guidance; wrapped so it can never
  break the pipeline (`orchestrator.py:891-934`).
- **Drives the refinement loop:** failing agents are re-run with corrective guidance; after the
  last iteration, unsupported citations are **stripped**, not presented (`_strip_unsupported`,
  `orchestrator.py:936-968`). `enable_verification=false` records `accepted_unverified`, never a
  fake "accepted" (fixes WORKSHEET B3; `orchestrator.py:764-782`).
- **Measured:** mean 4.1 s (8 runs). **Status:** ✅ (WORKSHEET bug B1 "Verification never runs" is
  **fixed** — the critic is wired at `orchestrator.py:791-797, 908-926`).
- **This is the project's LLM-as-judge component** — but as an in-loop reviewer, not a scoring
  harness (see §16).

### 8.7 Synthesis (Agent 6 — decision/generation specialist; the answer writer)
- **File:** `agents/synthesis.py`; prompt `prompts/synthesis/v1.yaml`.
- **Objective:** write the colleague-style reply — direct answer, key points, reasoning,
  uncertainties, next steps, confidence.
- **Input:** question + verified facts + both research agents' findings + draft. **Denied:** raw
  chunks; upstream evidence items carry real `source_label`s so the audit can verify sections and
  case names quoted in prose (`synthesis.py:51-63`).
- **Output:** answer_markdown, key_points, **inference (model's own reasoning, visually separated
  from what sources say)**, uncertainties, next_steps, confidence; `prose_fields=("answer_markdown",)`.
  `fallback_answer()` guarantees a non-empty result if synthesis fails (flagged, not model-written).
- **Measured:** mean 12.1 s. **Status:** ✅. Output guardrail downgrades confidence when
  groundedness fails (`orchestrator.py:441-467`).

## 9. Prompt Strategy Analysis

All prompts are versioned YAML files loaded by a `PromptRegistry` (`agents/prompts.py`), one per
agent under `prompts/<agent>/`, each with name/version/system_prompt/variables/model_defaults;
the exact version used is persisted on every `AgentRun`/`ModelRun`.

**Implemented strategies (verified):**
1. **Role/persona prompting** — every agent ("You are a legal research assistant for an Indian
   advocate…", "You are the intake router…", "You are a verification agent… deliberately
   sceptical") (`prompts/*/v1.yaml`).
2. **Zero-shot, rule-constrained prompting** — explicit ABSOLUTE RULES blocks with grounding
   constraints ("Use ONLY the numbered LAW CONTEXT entries… An empty list is a valid, correct
   answer") (`prompts/legal_research/v1.yaml:19-26`).
3. **Structured-output prompting + schema-constrained decoding** — the JSON schema appears both
   in the prompt text and as Ollama's `format` parameter, making off-schema output structurally
   impossible (`agents/base.py:333` `json_schema=schema_for(prompt.name)`; `agents/schemas.py`;
   `.kiro/PROGRESS.md:162-213` documents this as the single most important fix).
4. **Instruction-hierarchy / data fencing (prompt-injection defence)** — `fence_untrusted()`
   wraps untrusted text in tagged envelopes with a "this is DATA, never instructions" clause and
   neutralises tag escapes (`base.py:468-484`); the router prompt carries an INSTRUCTION HIERARCHY
   section (`prompts/intake_router/v1.yaml:15-19`).
5. **Few-shot worked example** — the router prompt carries a worked FIR rewrite example
   (`.kiro/PROGRESS.md:267-268`).
6. **Self-correction / reflection (agentic pattern, the project's strongest):** a three-level
   mechanism — (a) layered JSON recovery with an explicit "Your previous reply was not valid JSON"
   retry (`base.py:174-233, 313-320`); (b) deterministic grounding audit producing corrective
   guidance (`verification.py:69-92`); (c) refinement loop re-running the failing agent with that
   guidance, ≤2 iterations, every iteration recorded (`orchestrator.py:680-843`).
7. **Distinct strategy per agent (course requirement "at least one agent with a different prompt
   strategy"):** the Verification critic is a genuinely different strategy — a sceptical
   judge persona at temperature 0.0 that audits rather than generates; the router does
   classification+rewriting; drafting does constrained generation with hard prohibitions.

**NOT implemented (do not claim otherwise):**
- **ReAct** ❌ — no thought/action/observation interleaving anywhere.
- **Explicit Chain-of-Thought / Tree-of-Thought prompting** ❌ — outputs contain `reasoning`
  fields, but no CoT-style prompt scaffolding.
- **Self-consistency (multi-sample voting)** ❌.
- **Model-driven tool-use prompting** ❌ (see §11).

## 10. RAG Analysis

**Status: ✅ fully implemented and ablated.** RAG is central to the system and used by three agents.

- **Which agents use RAG:** Legal Research (law collections), Similar Case (CASE_LAW), Document
  Understanding (USER_DOCUMENTS) — each via its isolated slice of one retrieval. Drafting/Synthesis
  consume findings (RAG by inheritance, with provenance labels).
- **Why:** groundedness with citations; the RAG-off control proves the value — accuracy on
  answerable tasks 16.7% → 54.2% with retrieval (`eval/results/rag_ablation.json`;
  `docs/rag.md:323-364`); the headline reproducible failure (s.73 "sale of goods") is documented in
  `.kiro/PROGRESS.md:76-84`.
- **Sources:** India Code section-level fetch (reverse-engineered `SectionPageContent` JSON
  endpoint, WAF/rate-limit-aware client — `ingestion/india_code.py`, `.kiro/PROGRESS.md:58-71`);
  e-SCR Supreme Court judgments, CC-BY-4.0 (`ingestion/case_law.py`). Every record carries
  `source_url`, licence, `retrieved_at`, `dataset_version`.
- **Preprocessing:** HTML/ligature repair, headnote extraction, repealed-provision exclusion
  (retroactive purge), empty-provision purge, sha256 content versioning, SimHash near-duplicate
  detection (duplicate rate 0.17%) — `ingestion/`, `scripts/check_corpus_quality.py`,
  `docs/evaluation.md:402-418`.
- **Chunking:** three structure-aware strategies (Act→Chapter→Section→Subsection; Judgment
  headnote segments; Document→Page→Heading→Paragraph), subsection packing, overlap only when a
  unit exceeds max (`ingestion/chunking.py`; `docs/rag.md:54-89`). Chunk target 1400 / max 2600 /
  min 120 chars (`config.py:130-134`).
- **Embeddings:** Ollama `nomic-embed-text`, 768-d, L2-normalised; batched `/api/embed`
  (batch 32; 2249 → 91 ms/chunk; verified cosine(single, batched) = 1.000000) —
  `services/embedding/service.py`, `config.py:49-58`.
- **Vector store:** FAISS flat inner-product `IndexIDMap2`, **one index per collection**
  (LEGAL_ACTS, LEGAL_RULES, LEGAL_REGULATIONS, LEGAL_NOTIFICATIONS, CASE_LAW, USER_DOCUMENTS);
  exact cosine, metadata over-fetch before top-k (`services/vector/store.py`; `docs/rag.md:123-136`).
- **Retrieval strategy:** hybrid — dense (FAISS) + BM25 (Okapi k1=1.5, b=0.75, implemented
  directly with citation normalisation `section_73` and a 3× exact-citation boost,
  `retrieval/keyword.py`) + symbolic graph traversal (1,845 nodes / 2,649 edges: contains 1783,
  cross_references 803, cites_section 43, considers_act 20; bidirectional bounded BFS,
  dense-anchored seeding — `retrieval/graph.py`), fused with **Reciprocal Rank Fusion** k=60,
  dense 1.0 / keyword **0.15** (`retrieval/fusion.py`).
- **Measured retrieval quality (corpus 1,783 provisions):** mean recall **0.682**, MRR **0.572**;
  hybrid beats dense alone by **+0.068**; the keyword weight was **measured, not guessed**
  (weight sweep peaked at 0.15; the original 0.6 made hybrid *worse* than dense alone —
  `eval/ablation.json`, `eval/weight_sweep.json`, `docs/rag.md:176-216`). The graph retriever
  contributes **0.000 recall** on this task set — reported honestly (`docs/rag.md:242-258`).
- **Context passing:** rewritten query embedded; per-agent budgets (5 law + 2 doc; 4 cases);
  700-char prompt trimming; Illustrations stripped; untrusted text fenced.
- **Metadata filtering:** collection scope per retrieval mode; court/provision filters on cases;
  `retrieval_candidate_multiplier=4` over-fetch so filters never shrink below top-k.
- **Citations/sources returned:** every chunk carries chunk_id, ready-made citation string,
  source_url; agent outputs must cite chunk_ids, verified by the audit.
- **Current limitations / hallucination risks (measured, stated honestly):** raw-question recall
  0.682 (6/22 tasks zero recall — fixed by the router rewrite in production); RAG **relocates**
  fabrication rather than eliminating it (fabrication rate 4.3% no-RAG → 6.0% with RAG, appearing
  on unanswerable tasks — `docs/evaluation.md:347-364`); all 45 extracted case citations resolve
  outside the corpus; graph adds nothing to recall yet.

## 11. Tools and Function Calling

**Honest finding: there is NO model-driven function calling / tool-use loop.** The models do not
choose or invoke tools. What exists:

- A **deterministic service layer** the orchestrator invokes in code: retrieval search
  (`services/retrieval/service.py`), LLM generation (`services/llm/service.py`), embeddings,
  chunk inspection (`services/gateway/inspect.py`), citation audit (`agents/verification.audit()`).
- A `ToolInvocation` contract and an `agent_runs.tools_used` column exist
  (`domain/contracts.py:507`, `db/models.py:483`), but **nothing populates them**
  (`agents/base.py:442` reads `context.extra.get("tools_used", [])` with no writer).
- **`docs/agents.md` §6 (table of `search_laws` / `search_cases` / `get_chunk` /
  `validate_citation` tools) is stale relative to the code** — treat it as design intent, not
  implementation.

**Gap:** course requirement on tools/function calling is only partially satisfied (typed internal
services + retrieval-as-tool-by-orchestration, but no agent-invocable tools). Priority: Medium-High.
**Why tools would help:** a ReAct-style loop with `search_laws`/`search_cases`/`get_chunk` as
model-invocable functions would let the model decide when to retrieve more, instead of the current
one-shot context assembly.

## 12. MCP / A2A / Messaging Analysis

- **MCP:** ❌ absent. **A2A protocol:** ❌ absent. **Do not claim either.**
- **Blackboard:** ❌ as a formal pattern. The shared SQLite trace store (pipeline_traces,
  trace_stages, agent_runs, model_runs) functions as a shared record of everything, but agents do
  not coordinate by reading/writing a blackboard.
- **Peer-to-peer:** ❌ (agents never talk directly; all coordination is orchestrator-mediated,
  hub-and-spoke).
- **Broadcasting:** 🟡 — `TraceEventBus` (`core/tracing.py:67-100`) publishes every stage event to
  subscribed SSE clients for live UI progress; this is event *streaming to the UI*, not
  agent-to-agent broadcast.
- **What actually exists:** an internal **service transport abstraction**
  (`core/transport.py`) with `inprocess` (default) and `http` implementations sharing **identical
  Pydantic contracts and handlers**; the Pipeline Trace page shows which transport served each hop.
  Communication data: typed contracts (RetrievalRequest/RetrievedChunk, LLMGenerateRequest, agent
  outputs as JSON schemas), plus the trace DB.
- **Coordination & failure handling:** orchestrator plans and sequences; router failure →
  deterministic fallback; parse failure → layered recovery + retry; critic failure → logged and
  ignored (deterministic audit already did the load-bearing work) (`orchestrator.py:928-934`).
- **Measurable dimensions:** transport per hop is recorded; stage/message timings are recorded
  (bookkeeping stages ~2.4 ms). Communication *latency between agents* as a network phenomenon is
  not applicable in the default in-process mode.

**Gap:** MCP/A2A is a clear course-topic miss. Priority: Medium (a minimal MCP server exposing
`search_chunks` / `get_chunk` / `audit_citation` as MCP tools, with one agent consuming them, would
satisfy the topic honestly).

## 13. Architecture Pattern Analysis

| Pattern | Status | Where | Notes |
|---|---|---|---|
| Manager / orchestrator (hierarchical) | ✅ | `orchestrator.py` | Deterministic controller; agents are workers |
| Workflow-based (sequential + parallel) | ✅ | `run()`; `_plan()` records the plan; `asyncio.gather` at :390 | Plan persisted to trace |
| Sequential | ✅ | guardrails → router → retrieval → (doc) → research ∥ cases → draft → synthesis | Dependency-driven |
| Event-driven | 🟡 | `TraceEventBus` + SSE `/api/research/stream` | Event *streaming* for UI; no broker/queue; pipeline itself is orchestrated, not EDA |
| Graph workflows (orchestration) | ❌ | — | A retrieval knowledge graph exists (`retrieval/graph.py`) but orchestration is not a graph engine |

**Overall architecture:** a deterministic manager orchestrating a conditional, partly-parallel
agent workflow over a hybrid-RAG substrate, with DB-backed observability and two guardrail
envelopes (input/output). **Why it fits:** auditability (every hop is a recorded stage), hardware
bound (one GPU → `llm_max_concurrency=1`), and the dependency structure is real (drafting needs
both research agents). **Latency locus:** generation (86.2%), not coordination. **Alternatives:**
an event-broker EDA would add infrastructure without benefit at this scale — the docs argue this
explicitly (`docs/architecture.md:121-123`).

## 14. Observability / Logging / Tracing

**Status: ✅ comprehensive — the project's strongest differentiator.**

- **Trace recorder:** `core/tracing.py` — `TraceRecorder` opens/closes a `pipeline_traces` row;
  every hop is a `trace_stages` row with ordinal, stage key, service, transport, duration, input,
  output, status; `skip_stage` records why something did *not* run.
- **Agent runs:** `agent_runs` rows store both rendered prompts, raw output, parse diagnostics,
  attempts, token counts, latency, status, and the **context manifest with included AND excluded
  chunk ids** (`agents/base.py:409-447`) — "agent context isolation is only provable if you can
  show what each agent was *not* given" (`docs/architecture.md:153-155`).
- **Model runs:** `model_runs` row per LLM call — provider, model, temperature, max tokens, prompt
  name/version, tokens, latency, raw response (`services/llm/service.py:3,51`).
- **Retrieval runs:** `retrieval_runs`/`retrieval_results` persist what was retrieved and why.
- **Logging:** structured key-value logging via `core/logging.py` (e.g. `agent_run`,
  `refinement_loop`, `request_refused` events).
- **Live monitoring:** SSE stream of stage events; Pipeline Trace UI renders the node graph with
  parallel agents side by side; "What did the model see?" per call.
- **Run IDs & timestamps:** `trace_id` (`new_trace_id()`), `agent_run_id`, `started_at`/
  `completed_at`/`duration_ms` everywhere.
- **Token usage & latency:** recorded per agent run and per model run; aggregated in
  `eval/results/latency_breakdown.json`.

## 15. 40-Run Evaluation Requirement

**Status: ✅ evidenced — 40 recorded full-pipeline runs.**

- `eval/results/latency_breakdown.json` begins `{"traces_analysed": 40, "mean_total_ms": 70886.0,
  "median_total_ms": 70100.6, "generation_share": 0.8624, "mean_tokens_per_second": 39.83}` and
  breaks down 16 stage types and 7 agent types over those 40 traces.
- Produced by `eval/latency_breakdown.py`, which reads the 40 most recent `PipelineTrace` rows from
  the DB; the runs were generated by `scripts/generate_traces.py`, which drives the **real**
  `/api/research/query` endpoint (8-question spread across task categories, verification enabled)
  so `trace_stages` contains genuine recorded timings.
- Additionally, **81 single-agent evaluation runs** (3 models × 27 tasks) are recorded in
  `eval/results/model_llama3.2_3b.json`, `model_gemma2_2b.json`, `model_qwen2.5_3b-instruct.json`.
- **Caveats to state in the submission:** (a) the SQLite DB itself is not in this repo copy
  (`data/` is empty) — the JSON exports are the durable evidence; (b) the 40 traces are *pipeline*
  runs — keep this distinct from the 81 model-comparison runs, which used the single Legal Research
  agent; (c) do not extrapolate to "40+ per agent" — per-agent execution counts are: Legal Research
  39, Similar Case 11, Verification 8, Doc Understanding 7, Synthesis 7, Intake 7, Drafting 2
  (latency JSON `agents[]`).

## 16. Agent-Level Evaluation Metrics

| Metric | Status | Evidence / Value |
|---|---|---|
| Latency per agent | ✅ measured | `latency_breakdown.json` agents[]: Legal Research 11.99 s mean (39 runs), Similar Case 17.92 s (11), Doc Understanding 12.48 s (7), Synthesis 12.12 s (7), Intake 4.80 s (7), Verification 4.11 s (8), Drafting 13.07 s (2) |
| Tool-call latency | ❌ n/a | no model-driven tools (§11) |
| Retrieval latency | ✅ | mean 2,823 ms (`docs/evaluation.md:114`) |
| Accuracy per agent | 🟡 | model-comparison accuracy (55.6% best) measured on the Legal Research Agent only; no per-agent accuracy across the full pipeline (E8 proposed) |
| Relevance (citation precision) | ✅ | 40.3% best model (`docs/evaluation.md:117-121`) |
| Retrieval recall / MRR | ✅ | 0.682 / 0.572; per-config ablation |
| Hallucination rate | ✅ (citation-level) | fraction of tasks citing ≥1 chunk_id never supplied; 0.0% (llama3.2:3b) / 11.1% (gemma2:2b) |
| Claim-level hallucination | ❌ | E7 proposed in `docs/WORKSHEET.md:218-222`; not implemented |
| Task success / refusal discipline | ✅ | 3 deliberate unanswerable tasks; qwen 3/3 correct refusals, gemma 0/3 |
| Error rate | ✅ | parse failures, attempts, warnings recorded per run |
| Schema compliance | ✅ | 100% (llama/qwen) / 88.9% (gemma) |
| **LLM-as-a-Judge** | 🟡 | the Verification critic *is* an LLM judge in-loop (verdict, unsupported_claims, overconfident_statements — `verification.py`), but there is **no judge-scoring harness** producing per-run judge scores of answer quality |

**Realistically addable without claiming prior existence:** judge-scored answer quality over the
recorded 40 traces; claim-level unsupported-claim rate (deterministic entity/section overlap
first); full-pipeline vs single-agent accuracy comparison (E8); per-agent accuracy on the recorded
traces by replaying audits.

## 17. MAS-Level Evaluation Metrics

- **Total execution time:** ✅ mean 70.9 s, median 70.1 s over 40 runs; p95 per stage recorded.
- **Overall task completion:** ✅ trace status (`success` / `partial` / `refused`), warnings list,
  loop iteration outcomes recorded per run.
- **Overall accuracy:** 🟡 measured at model level on one agent (55.6%); **full-pipeline accuracy
  not yet measured** (E8 gap).
- **Agent collaboration:** ✅ measured — concurrency of the two research agents; refinement-loop
  iterations with audits recorded per iteration (including when a retry fails to fix the problem).
- **Communication/coordination overhead:** ✅ measurable and small — input guardrails 0.93 s mean
  (1.5%), context construction 2.25 s (3.2%), orchestration bookkeeping stages ~2.4 ms each;
  86.24% of wall time is generation.
- **Overall bottleneck:** ✅ identified (§18).
- **Combining agent-level into MAS-level:** the trace DB already stores per-stage and per-agent
  durations within each trace, so MAS metrics are aggregations of agent metrics by `trace_id` —
  `eval/latency_breakdown.py` demonstrates exactly this (stage view + agent view over the same 40
  traces).

## 18. Bottleneck Analysis

- **MAS level (measured, 40 runs):** 86.24% of wall time inside model generation
  (427.9 s of 496.2 s). Largest single stage: **Similar Case Agent — 39.7%** (mean 16.4 s,
  p95 28.4 s). Orchestration overhead is *not* the bottleneck — the argument for optimising models
  and contexts rather than coordination is in `docs/architecture.md:57-76`.
- **Agent level:** Similar Case (slowest + thin corpus → honest `evidence_sufficient:false`);
  Drafting is a serial tail when requested (13.1 s, only 2 runs); Verification adds 4.1 s only on
  audit failures; the graph retriever costs ~190 ms and adds 0.000 recall (measured neutral —
  `docs/rag.md:242-258`).
- **Hardware:** single GPU with `llm_max_concurrency=1` serialises generation; `num_ctx=3072` is a
  measured VRAM compromise (`config.py:45-47`).
- **Documented levers:** faster/smaller model, fewer agent calls, tighter contexts —
  `docs/evaluation.md` threat-to-validity section; `qwen2.5:3b` ≈2× speed noted in README.

## 19. LLM Analysis

- **Models (all local via Ollama — privacy-preserving by construction, CO2):**
  `codellama:7b-instruct` (brief-required default in `config.py:41`, 7B params, 3.8 GB, runs
  22%/78% CPU/GPU on the 6 GB card); `llama3.2:3b` (Meta, 3B — **measured best**: 55.6% accuracy,
  11.6 s mean, 0.0% hallucination; `docs/evaluation.md:117-129`); `qwen2.5:3b-instruct` (Alibaba,
  3B — best refusal discipline 3/3 and highest throughput 36.4 tok/s); `gemma2:2b` (Google, 2B —
  weakest, 11.1% hallucination, 0/3 refusals); `nomic-embed-text` for embeddings (768-d).
- **Parameter counts:** taken from public model cards and recorded in `docs/evaluation.md:46-55`
  (2B/3B/3B/7B). Models are **inference-only locally; there are no trainable parameters** — say
  this explicitly rather than inventing training analysis.
- **Which LLM is better for which agent:** measured at the application level — llama3.2:3b for
  research generation; qwen2.5:3b recommended for scope classification / refusal-critical checks
  (`docs/WORKSHEET.md:260-267`, T5.1). **Per-agent model routing is NOT wired** — the plumbing
  exists (`Orchestrator(model=...)` → every agent; per-request UI switch), but all agents share one
  model per run. 🔵 planned.
- **Model comparison already implemented:** ✅ `eval/runner.py` — 3 models × 27 tasks with
  retrieval replayed byte-identical per question; resources measured per model
  (`eval/resources.py`, `resources.json`: cold load, resident size, CPU/GPU split, VRAM delta).
- **Entropy:** ❌ **not measured anywhere in the project.** If the course requires entropy, it
  must be added as new measurement work (e.g. token-distribution entropy over recorded
  generations), not claimed from existing data.
- **Generation config:** temperature 0.0–0.2 per prompt; `num_ctx` 3072; schema-constrained
  decoding; max tokens derived from the output schema (`agents/schemas.py:required_max_tokens`).

## 20. Course Outcome Alignment

| Course Requirement | Present? | Evidence | Status | Gap / Recommendation |
|---|---|---|---|---|
| CO1 — foundational principles of intelligent agents & LLMs → multi-agent app | Yes | 6 LLM agents + deterministic coordinator; `docs/agents.md` | ✅ | — |
| CO2 — agentic design patterns + **local LLMs**, secure & privacy-preserving | Mostly | Ollama-only; guardrails; auth; rate limit; PII trade-off documented | ✅/🟡 | PII redaction deliberately not implemented (documented trade-off, `docs/WORKSHEET.md` T2.6) |
| CO3 — multi-agent, **multi-modal** applications for multi-step tasks | Mostly | multi-agent ✅; multi-step ✅; **multimodal ❌** (uploads are OCR'd to text; no vision model) | 🟡 | State honestly; optional: accept image inputs via an OCR pipeline is already the workaround |
| Intelligent Agents / Single- vs Multi-Agent | Yes | §7-8 | ✅ | — |
| Transformers & LLMs | Yes (usage) | model comparison, constrained decoding | 🟡 | no internal model analysis (entropy ❌) |
| ReAct | **No** | §9 | ❌ | implement in one agent or argue the reflection loop as the alternative strategy |
| CoT / ToT / Self-consistency | No (CoT-lite fields only) | §9 | ❌ | do not claim |
| Reflection | Yes | refinement loop + critic | ✅ | strongest pattern claim |
| Tool use / function calling | Partial | §11 — orchestrator-invoked services; no model-driven tools | 🟡 | add MCP-style tools for one agent |
| Planning | Partial | orchestrator `_plan()` (code-planned, not LLM-planned) | 🟡 | — |
| Multi-agent workflows / orchestration / coordinator / sub-agents / delegation | Yes | §7, §13 | ✅ | — |
| RAG | Yes, measured | §10 | ✅ | — |
| CrewAI / Flows / Google ADK | No | custom framework instead | ❌ | justify in report: framework-free for auditability |
| Agent lifecycle / state / sessions / memory | Partial | seed→ingest→serve→trace lifecycle ✅; per-run state ✅; **no cross-run conversational memory** (by design: research workspace, not chat) | 🟡 | — |
| Event-driven architecture | Partial | SSE event bus; no broker | 🟡 | — |
| Graph workflows | Partial | knowledge graph retrieval; not orchestration graphs | 🟡 | — |
| Callbacks / guardrails | Yes | stage handles; 6-layer input + output guardrails, measured | ✅ | — |
| Agent evaluation / monitoring / observability | Yes, measured | §14-17; 40 traces | ✅ | add judge scores + per-agent accuracy |
| **MCP** | **No** | §12 | ❌ | minimal MCP server recommended |
| **A2A** | **No** | §12 | ❌ | same |
| Data security & privacy | Partial | authn on all routes, rate limiting, per-key budgets, local LLMs, licence-clean corpus; PII persisted by design and documented | 🟡 | — |
| Local LLMs | Yes | Ollama only; no hosted fallback | ✅ | — |
| Multimodal agents | No | OCR→text only | ❌ | — |

## 21. Project Plan

Actual workstreams present in the repo (others omitted deliberately): agent development; RAG & data
engineering; prompt engineering; backend services; frontend; guardrails/security; observability;
evaluation; documentation; deployment.

Development phases actually evidenced (from `.kiro/PROGRESS.md`, `docs/WORKSHEET.md` audit dated
30 Aug 2026, and current code):

1. **Design & substrate** (done): architecture docs, config, domain contracts, DB schema, tracing,
   LLM/embedding services, chunking, India Code ingestion.
2. **RAG completion** (done): FAISS/BM25/graph + RRF, retrieval service, why-retrieved explanations,
   case-law ingestion, corpus hygiene (repealed purge, dedup, sha256 versioning).
3. **Agents & orchestration** (done): 7 agents, context isolation, refinement loop, synthesis,
   constrained decoding, token-budget engineering.
4. **Platform** (done): gateway (~27 endpoints incl. inspect/uploads routers), 13-page frontend,
   Docker Compose.
5. **Integrity pass** (done): all P0 WORKSHEET items verified fixed (B1-B12, B14, B15; B13
   temporal versioning still open).
6. **Guardrails & security** (done): 6-layer input guardrails, output groundedness gate, API-key
   auth middleware, token-bucket rate limiting, injection fencing, guardrail probe suite.
7. **Evaluation** (done): 27-task dataset, 3-model comparison, retriever ablation, weight sweep,
   rewrite A/B, RAG on/off, latency breakdown over 40 traces, guardrail suite, corpus analytics;
   `eval/report.py` regenerates `docs/evaluation.md` from recorded JSON.
8. **Remaining (planned):** see §24/§25 — LLM-as-judge harness, claim-level hallucination,
   full-pipeline accuracy study, MCP/tools, per-agent model routing, precedent corpus expansion.

## 22. Division of Work for 3 Members

Names are NOT present in the repo; placeholders used. Percentages are indicative of the actual
codebase split, not timesheets.

**Member 1 — Multi-Agent Core & Reasoning (~35%)**
- Responsibilities: orchestrator; all 7 agents; per-agent context isolation; refinement loop;
  prompt registry & YAML prompts; verification two-tier design.
- Technical tasks: `agents/orchestrator.py`, `agents/*.py`, `agents/base.py` (JSON recovery,
  fencing), `agents/schemas.py` (schema-constrained decoding, token budgets), `prompts/**`.
- Deliverables: working end-to-end pipeline; agent roster + context-isolation UI evidence;
  refinement-loop trace visualisation.
- Dependencies: consumes Member 2's retrieval service and Member 3's gateway/transport.
- Contribution: the system's "genuine multi-agent" claim (CO1/CO3).
- Evaluation duties: agent-level latency review; schema-compliance tracking; refinement-loop
  effectiveness (retry success rate).

**Member 2 — RAG, Retrieval & Legal Data (~35%)**
- Responsibilities: India Code + e-SCR ingestion; chunking; embeddings; FAISS/BM25/graph; RRF
  fusion; corpus hygiene; retrieval experiments.
- Technical tasks: `ingestion/*`, `services/embedding/*`, `services/vector/store.py`,
  `services/retrieval/*` (service, keyword, graph, fusion, explain), `scripts/ingest_*.py`,
  `eval/ablation.py`, `eval/weight_sweep.py`, `eval/rewrite_experiment.py`, `eval/rag_ablation.py`.
- Deliverables: verified corpus (1,783 provisions / 47 judgments / 2,575 chunks); measured
  retrieval quality (recall 0.682, MRR 0.572); the measured keyword weight 0.15; query-rewrite
  experiment (+0.500 recall).
- Dependencies: provides `services.retrieval.search` to Member 1; corpus stats to Member 3's eval.
- Contribution: grounding quality — the thing that separates the system from a raw LLM.
- Evaluation duties: retrieval recall/MRR; ablation matrix; corpus reconciliation.

**Member 3 — Platform, Security, Observability & Evaluation (~30%)**
- Responsibilities: gateway/API; auth & rate limiting; guardrails suite; tracing/SSE; frontend;
  Docker; the evaluation harness and its reporting.
- Technical tasks: `services/gateway/*`, `core/security.py`, `core/guardrails.py`,
  `core/guardrail_suite.py`, `core/tracing.py`, `core/transport.py`, `frontend/src/**` (13 pages),
  `docker-compose.yml`, `eval/runner.py`, `eval/metrics.py`, `eval/latency_breakdown.py`,
  `eval/report.py`, `backend/tests/*` (159 tests).
- Deliverables: inspectable UI (Pipeline Trace, Agents, Retrieval Inspector, Guardrails pages);
  measured guardrail effectiveness (19/19 adversarial, 0/10 false positives); the 40-trace latency
  breakdown; the reproducible evaluation report.
- Dependencies: depends on Member 1's agent APIs and Member 2's retrieval service.
- Contribution: demonstrability, safety and the evidence base everything else is claimed with.
- Evaluation duties: 40-run requirement; guardrail suite; security checks; report generation.

## 23. Section 3 — Contribution and Overall Project Impact (raw material for the student's "In Your Understanding")

- **What will be done (remaining work):** close the course-facing gaps — add an LLM-as-a-Judge
  scoring pass over recorded runs; claim-level hallucination measurement; a full-pipeline accuracy
  study (8+ tasks end-to-end vs single-agent); model-invocable tools via a minimal MCP server for
  one agent (with a ReAct-style loop); measured per-agent model routing (qwen for scope/critic,
  llama3.2 for research); variance/temperature study; precedent corpus expansion; temporal
  versioning of statutes.
- **Why required:** each item converts an existing *capability* into *measured evidence* (judge
  scores, claim-level grounding, MAS-vs-single-agent quantification) or closes an explicit course
  topic (MCP/tools/ReAct) — the project's stated standard is "a claim that cannot be demonstrated
  is indistinguishable from one that is not there" (`docs/guardrails.md:1-3`).
- **Member → component → MAS → user outcome chains (examples):**
  - Member 1 adds the judge harness → Verification Agent produces per-run quality scores →
    refinement loop becomes quantitatively evaluable → the lawyer sees a groundedness score on the
    answer they read.
  - Member 2 wires the MCP retrieval tool into one agent → the model can request more evidence →
    recall ceiling on hard questions rises → fewer "insufficient evidence" dead-ends.
  - Member 3 runs the full-pipeline accuracy study → MAS-level value quantified against the
    single-agent baseline → the division of work itself becomes an evaluated design decision.
- **Technical improvements required:** listed with priorities in §24/§25.

## 24. Current Gaps Against Project Guidelines

| # | Requirement | Status | Evidence | What is missing | Suggested action | Priority |
|---|---|---|---|---|---|---|
| G1 | At least one different prompt strategy / ReAct / COTS | 🟡 | §9: distinct strategies exist (critic@0.0, router, drafting); reflection loop ✅ | ReAct not implemented | Implement ReAct loop in Legal Research OR frame critic+reflection as the required "different strategy" explicitly | **High** |
| G2 | RAG in ≥1 agent | ✅ | §10; three agents | — | — | — |
| G3 | Tools / function calling | 🟡 | §11; `docs/agents.md` §6 stale | No model-invocable tools; `tools_used` never populated | MCP-style tool server + tool-use loop for one agent; fix the doc | **High** |
| G4 | MCP / A2A / messaging protocol | ❌ | §12 | None implemented | Minimal MCP server exposing search/get_chunk/audit; discuss transport abstraction as internal protocol | **High** |
| G5 | Logs/traces per run | ✅ | §14; trace DB + UI | — (DB not in repo copy) | Export/regenerate before submission | Medium |
| G6 | ≥40 runs recorded | ✅ | `latency_breakdown.json` traces_analysed=40 | Keep evidence bundled | Archive JSONs + regenerate traces | Medium |
| G7 | Agent-level latency | ✅ | §16 | — | — | — |
| G8 | Agent-level accuracy | 🟡 | single-agent accuracy only | No per-agent accuracy across pipeline | E8 full-pipeline study; per-trace audits | **High** |
| G9 | LLM-as-a-Judge | 🟡 | critic exists in-loop | No judge scoring harness/scores | Add judge scoring over the 40 traces (build on critic prompt) | **High** |
| G10 | Architecture evaluation | ✅ | §13 + measured stage shares | — | — | — |
| G11 | Communication protocol evaluation | 🟡 | transport recorded per hop | No MCP/A2A to compare against | G4 first, then compare inprocess vs http hops | Medium |
| G12 | MAS-level bottleneck analysis | ✅ | §18 | — | — | — |
| G13 | Agent-level bottleneck analysis | ✅ | §16/§18 | — | — | — |
| G14 | LLM parameter analysis | 🟡 | public param counts recorded (2B-7B); inference-only | No entropy measurement | Add token-entropy measurement or state not-measured | Medium |
| G15 | Entropy | ❌ | §19 | Not measured | New measurement or explicit statement | Medium |
| G16 | User/persona linkage | ✅ | §4; practitioner-shaped UI | — | — | — |
| G17 | Guardrails/security/privacy | ✅/🟡 | 6-layer guardrails measured; auth; rate limit | PII redaction not implemented (documented) | Keep documented trade-off | Low |
| G18 | Multimodal | ❌ | OCR→text only | No vision | Optional; state honestly | Low |
| G19 | Temporal versioning (B13) | ❌ | grep: no valid_from/effective_date in `db/models.py` | Cannot answer "law as of date" | Optional differentiator (T5.4) | Low |
| G20 | Claim-level hallucination (E7) | ❌ | WORKSHEET P3 list | Not implemented | Deterministic entity/section overlap scorer | Medium |

Worksheet bug reconciliation (B1-B15 from `docs/WORKSHEET.md`, audited 30 Aug 2026): B1 fixed
(critic wired), B2 fixed (audit covers prose via `prose_fields` + `source_label`), B3 fixed
(`accepted_unverified`), B4 fixed (fail-closed), B5 fixed (compare endpoint guardrailed,
`gateway/app.py:524-545`), B6 fixed (auth middleware `core/security.py`, `services/common.py:82`),
B7 fixed (token bucket + per-key limits + GPU semaphore), B8 fixed (fencing + instruction
hierarchy), B9 documented trade-off (auth on traces; deliberate persistence), B10 fixed (upload
dedup `uploads.py:133-165`), B11 fixed (`_mode_for` widening), B12 fixed (sha256 versioning
`knowledge/ingest.py:128-148`), **B13 NOT fixed** (no temporal columns), B14 fixed (per-request
overrides via `RetrievalRequest`), B15 fixed (dead code removed — no hits).

## 25. Recommended Next Steps (priority order)

1. **LLM-as-a-Judge scoring harness** over the 40 recorded traces (reuse the critic prompt;
   produce per-run scores: grounding, completeness, overclaiming). Cheapest path to G9.
2. **E8 full-pipeline accuracy study** (8 representative tasks end-to-end, best model) → quantifies
   the multi-agent architecture's value and gives per-agent accuracy. Closes G8.
3. **Claim-level hallucination rate** (deterministic section/entity overlap on `answer_markdown`).
   Closes G20.
4. **MCP tool server + one ReAct-style agent** (expose `search_chunks`, `get_chunk`,
   `validate_citation`; let the model decide when to retrieve). Closes G1/G3/G4 together.
5. **Per-agent model routing, measured** (qwen → router/critic; llama3.2 → research; plumbing
   already exists). Strengthens G14.
6. **Variance/temperature study** (3× at temp 0.0/0.1 on 10 tasks) — converts a stated
   threat-to-validity into a number.
7. **Precedent corpus expansion** (47 → several hundred judgments) — fixes the Similar Case
   Agent's honest weakness.
8. **Archive/regenerate run evidence** before submission (DB absent from this copy).

## 26. Evidence From Codebase

| Claim | Evidence |
|---|---|
| 7 agents + deterministic orchestrator | `backend/src/legalaid/agents/{orchestrator,intake_router,document_understanding,legal_research,similar_cases,drafting,verification,synthesis}.py` |
| Concurrent research agents | `orchestrator.py:339-396` (`asyncio.gather`) |
| Refinement loop + critic wiring | `orchestrator.py:680-843, 891-934`; `verification.py` |
| Deterministic grounding audit incl. prose scan | `verification.py:106-183`; `prose_fields` in `drafting.py`, `synthesis.py` |
| Context isolation incl. exclusions | `agents/base.py:62-136`; `legal_research.py:36-65`; recorded in `base.py:439-440` |
| Schema-constrained decoding | `agents/base.py:333`; `agents/schemas.py`; `services/llm/*` |
| Prompt registry / versioned YAML | `agents/prompts.py`; `prompts/<agent>/v1.yaml` (legal_research v1.3, verification v1.1, others v1.0) |
| 6-layer input guardrails + output gate | `core/guardrails.py`; `orchestrator.py:209-232, 441-467`; `docs/guardrails.md` |
| Auth + rate limiting | `core/security.py` (`require_principal`, `TokenBucket`); `services/common.py:82`; `gateway/app.py` (`Depends(enforce_rate_limit)` on research/retrieval/compare) |
| Guardrail measurements | `eval/results/guardrail_suite.json`; `core/guardrail_suite.py` (29 probes) |
| Hybrid retrieval + RRF + measured weights | `services/retrieval/{keyword,graph,fusion,service,explain}.py`; `config.py:73-94`; `eval/ablation.json`, `eval/weight_sweep.json` |
| Chunking strategies | `ingestion/chunking.py`; `tests/test_chunk_packing.py` |
| Embeddings batched | `services/embedding/service.py`; `config.py:49-58` |
| FAISS per-collection indexes | `services/vector/store.py` |
| Corpus provenance & hygiene | `ingestion/{india_code,india_code_api,case_law,citations,dedup,corpus}.py`; `services/knowledge/ingest.py:128-148` (sha256); `scripts/check_corpus_quality.py` |
| Observability schema | `core/tracing.py`; `db/models.py` (26 tables incl. `pipeline_traces`, `trace_stages`, `agent_runs`, `model_runs`, `retrieval_runs`) |
| 40 recorded runs | `eval/results/latency_breakdown.json` (`traces_analysed: 40`); produced by `eval/latency_breakdown.py` over `PipelineTrace` rows; runs driven by `scripts/generate_traces.py` |
| Model comparison (3×27, retrieval replay) | `eval/runner.py`; `eval/dataset.py` (27 tasks); `eval/metrics.py`; results `model_*.json` |
| RAG on/off control | `eval/rag_ablation.py`; `eval/results/rag_ablation.json` |
| Rewrite experiment | `eval/rewrite_experiment.py`; `eval/results/rewrite_experiment.json` |
| Report generation (no drift) | `eval/report.py` → `docs/evaluation.md` |
| Upload dedup | `services/gateway/uploads.py:101-165` |
| Fallback synthesis | `agents/synthesis.py:fallback_answer`; `orchestrator.py:468-476` |
| Fail-closed scope | `intake_router.py:46-49,168,178-213`; `guardrails.py` layer 6 |
| Transport abstraction | `core/transport.py`; `docs/architecture.md:127-138` |
| Docker deployment | `docker-compose.yml` (7 services); per-service Dockerfiles; `docs/docker.md` |
| Tests | `backend/tests/` — 13 files, 159 `def test_` |
| Frontend 13 pages | `frontend/src/pages/` (Research, Pipeline, Agents, Retrieval, Chunks, Embeddings, Knowledge, Documents, Compare, Guardrails, Dashboard, System, Answer) |

---

## Final Requirement Table

| Requirement | Implemented | Evidence | Missing Work |
|---|---|---|---|
| Multi-agent system (≥2 agents) | ✅ | 6 LLM agents + critic + deterministic coordinator | — |
| Coordinator / orchestration | ✅ | `orchestrator.py` (deterministic, plan recorded) | — |
| Context isolation per agent | ✅ | `base.py` manifest incl. exclusions; Agents UI | — |
| RAG in ≥1 agent | ✅ | 3 agents; recall 0.682 measured; RAG on/off study | — |
| Vector DB + embeddings | ✅ | FAISS IndexIDMap2 + nomic-embed-text 768-d | — |
| Hybrid retrieval (dense/keyword/graph) | ✅ | RRF fusion; ablated; weight measured | graph adds 0 recall (honest) |
| Different prompt strategy | 🟡 | critic persona @0.0 + reflection loop; router rewrite | ReAct/CoT absent — implement or frame explicitly |
| Structured output | ✅ | schema-constrained decoding + JSON recovery | — |
| Tools / function calling | 🟡 | orchestrator-invoked services | model-invocable tools; `tools_used` unused |
| MCP / A2A | ❌ | §12 | minimal MCP server |
| Logs/traces per run | ✅ | trace DB + Pipeline Trace UI + SSE | archive DB/JSON evidence |
| ≥40 runs recorded | ✅ | `latency_breakdown.json` (40 traces) + 81 eval runs | keep evidence with submission |
| Agent-level latency | ✅ | per-agent stats over 40 traces | — |
| Agent-level accuracy | 🟡 | single-agent accuracy only | E8 full-pipeline study |
| LLM-as-a-Judge | 🟡 | in-loop critic (verdict/claims) | judge scoring harness |
| Hallucination rate | ✅ | citation-level, automatic | claim-level (E7) |
| MAS + agent bottleneck analysis | ✅ | 86.2% generation; Similar Case 39.7% | — |
| Architecture comparison | ✅ | manager/workflow vs EDA argued + measured stages | — |
| Communication comparison | 🟡 | transport per hop recorded | MCP/A2A absent to compare |
| LLM parameters | ✅ | public counts (2B-7B), inference-only | — |
| Entropy | ❌ | — | new measurement or explicit statement |
| Guardrails / security | ✅ | 19/19 catch, 0/10 FP; auth; rate limit; GPU semaphore | PII redaction (documented trade-off) |
| Local LLMs | ✅ | Ollama-only, no hosted fallback | — |
| Multimodal | ❌ | OCR→text pipeline only | optional |
| Tests / evaluation / reproducibility | ✅ | 159 tests; eval harness regenerates the report | — |

---

## Claude-ready Project Facts (verified)

**Verified facts — safe to state in the submission:**
1. LegalAId: Indian legal research workspace; 6 LLM agents + deterministic orchestrator + 2-tier
   verification (deterministic audit + LLM critic); full local stack (Ollama; no hosted API).
2. Corpus: 1,783 provisions / 15 Acts (India Code, official) + 47 SC judgments (e-SCR, CC-BY-4.0);
   2,575 chunks; provenance (source URL, licence, retrieved_at) on every record; repealed
   provisions excluded; duplicate rate 0.17%.
3. RAG: FAISS (exact cosine, per-collection) + hand-implemented BM25 (citation-normalised,
   3× citation boost) + symbolic graph (1,845 nodes/2,649 edges), RRF-fused (k=60; keyword weight
   **0.15 measured**, was 0.6 and hurt); query rewriting before embedding (recall 0.500→1.000 on
   affected subset); measured recall 0.682 / MRR 0.572.
4. RAG value measured: answerable accuracy 16.7% → 54.2% with retrieval; fabrication relocates
   (4.3%→6.0%) rather than disappearing.
5. Models compared under identical conditions (3×27, retrieval replayed): llama3.2:3b 55.6% acc /
   11.6 s / 0% hallucination; qwen2.5:3b 3/3 correct refusals; gemma2:2b weakest (11.1%
   hallucination, 0/3 refusals). codellama:7b-instruct is the brief-required baseline
   (config default), 22/78 CPU/GPU split. All params from public cards (2B-7B); inference-only.
6. Observability: every hop/agent run/model call recorded (26-table schema); context manifest
   records what each agent was **denied**; 13-page UI includes Pipeline Trace, Agents, Retrieval
   Inspector, Guardrails.
7. **40 recorded full-pipeline runs**: mean 70.9 s; 86.24% of time in generation; per-stage and
   per-agent latency breakdowns exist (Similar Case Agent largest at 39.7% / 16.4 s mean;
   guardrails 1.5%; citation validation ~0%).
8. Guardrails measured: 19/19 adversarial caught, 0/10 false positives (full stack); deterministic
   layers alone 15/19; corpus-distance floor 0.55 measured. Input fencing + instruction hierarchy
   implemented. API-key auth on all routes (health excepted), token-bucket rate limiting, GPU
   concurrency 1.
9. Quality infrastructure: 159 tests / 13 files; eval harness regenerates `docs/evaluation.md`
   from `eval/results/*.json`; 27-task dataset with 3 deliberate unanswerable tasks;
   7-configuration retriever ablation; weight sweep; rewrite A/B; RAG on/off control; corpus
   analytics (SimHash dedup, citation extraction with treatment classification).
10. Deployment: Docker Compose, 7 services + 2 volumes; host-Ollama default for GPU; in-process
    transport default with an http mode behind identical contracts.

**Planned / NOT implemented — do not claim:**
- ReAct, CoT, ToT, self-consistency prompting ❌ (reflection loop + distinct critic strategy are
  the implemented patterns).
- Model-driven tool calling ❌ (`agent_runs.tools_used` never populated; `docs/agents.md` §6
  tool table is stale design intent).
- MCP ❌, A2A ❌, blackboard ❌, peer-to-peer ❌ (orchestrator-mediated in-process messaging; SSE
  event bus to UI only).
- LLM-as-a-Judge **scores** ❌ (critic exists in-loop; no scoring harness).
- Claim-level hallucination rate ❌; full-pipeline accuracy study ❌; per-agent accuracy across
  the pipeline ❌.
- Entropy measurement ❌; temporal versioning (valid_from/effective_date) ❌.
- Multimodal understanding ❌ (uploads OCR'd to text).
- Cross-run conversational memory ❌ (stateless per request, by design).
- Firm/RBAC mode ❌ (schema-forward-compatible only).
