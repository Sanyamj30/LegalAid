# LegalAId

An AI-assisted Indian legal research workspace. A lawyer asks a question — or pastes an FIR, a
legal notice, or an agreement — and LegalAId searches genuine Indian legislation and a curated
set of Supreme Court judgments, then reports back with the applicable provisions, comparable
precedent, its own reasoning, and a draft if one was asked for.

Every internal step is inspectable in the UI: how the request was classified, how the query was
rewritten before embedding, which vectors were searched, which chunks came back and why, what
context each agent received *and was denied*, what prompt each model saw, and what it returned.

> **AI-generated legal research for professional review.** Verify statutes, amendments, case
> status, citations and procedural requirements before relying on any output. LegalAId does not
> provide legal advice.

---

## Quick start

Two ways to run it. Local is faster (your GPU); Docker is one command.

### Option A — Docker (recommended for a clean machine)

```bash
docker compose up -d --build          # builds images, starts everything, pulls the models
docker compose logs -f model-init     # wait for "models ready" (~4 GB download, once)

docker compose exec gateway python -m legalaid.db.seed
docker compose exec gateway python scripts/ingest_legislation.py --all
docker compose exec gateway python scripts/ingest_cases.py --limit 60

open http://localhost:3000
```

### Option B — Local (uses your GPU, noticeably faster)

```powershell
# 0. Prerequisites: Ollama installed and running, Python 3.13, Node 22
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull codellama:7b-instruct
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" pull nomic-embed-text

# 1. Python environment
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e backend

# 2. Database + corpus (first run only; ingestion takes ~6 minutes)
$env:PYTHONPATH="backend\src"
.\.venv\Scripts\python.exe -m legalaid.db.seed
.\.venv\Scripts\python.exe scripts\ingest_legislation.py --all
.\.venv\Scripts\python.exe scripts\ingest_cases.py --limit 60

# 3. API gateway  (terminal 1)
$env:PYTHONPATH="backend\src"
.\.venv\Scripts\python.exe -m uvicorn legalaid.services.gateway.app:app --port 8000 --reload --reload-dir backend\src

# 4. Frontend  (terminal 2)
cd frontend
npm install
npm run dev
```

| What | Where |
|---|---|
| Workspace UI | http://localhost:5173 (local) · http://localhost:3000 (Docker) |
| API docs (OpenAPI) | http://localhost:8000/docs |
| Health | http://localhost:8000/api/system/health |

### Verify it works

```powershell
$env:PYTHONPATH="backend\src"
.\.venv\Scripts\python.exe -m pytest backend\tests -q      # 282 tests across 21 suites
.\.venv\Scripts\python.exe scripts\smoke_ui_endpoints.py   # every UI endpoint
.\.venv\Scripts\python.exe scripts\smoke_answer.py         # full pipeline, end to end
```

---

## What it does, in one pass

```
You type / paste / upload
        │
        ▼
  Intake Router  ─── classifies the request, decides which agents are needed,
        │            and REWRITES your text into the query that gets embedded
        ▼
  Hybrid retrieval ── dense vectors + BM25 keyword + graph traversal, fused
        │
        ├──────────────┬──────────────┐
        ▼              ▼              │   (these two run concurrently)
  Legal Research   Similar Cases      │
   (statute only)  (precedent only)   │
        └──────────────┴──────────────┘
        │
        ▼
  Verification ── audits every citation against what was actually supplied;
        │         failures are re-run with corrective guidance  ⟲ loop
        ▼
  Drafting (if asked) ── sees findings only, never raw text
        │
        ▼
  Synthesis ── writes the answer: reasoning, uncertainties, next steps
```

---

## Documentation

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Services, data flow, why each boundary exists |
| [docs/developer-guide.md](docs/developer-guide.md) | **Deep dive — every component explained** |
| [docs/data-sources.md](docs/data-sources.md) | Where the law comes from, licensing, provenance |
| [docs/agents.md](docs/agents.md) | The seven agents and their context isolation |
| [docs/rag.md](docs/rag.md) | Chunking, embeddings, hybrid + graph retrieval |
| [docs/api.md](docs/api.md) | Every endpoint |
| [docs/docker.md](docs/docker.md) | Container layout, commands, troubleshooting |
| [docs/demo.md](docs/demo.md) | A scripted walkthrough |
| [docs/database-schema.md](docs/database-schema.md) | All 26 tables |

---

## The stack

| Layer | Choice | Why |
|---|---|---|
| Generation | Ollama + `codellama:7b-instruct` | Required by the brief. Runtime-switchable in the UI. |
| Embeddings | Ollama + `nomic-embed-text` (768-d) | Long context suits legal sections; separate model demonstrates a real embedding stage. |
| Vector store | FAISS flat inner-product | Exact cosine on normalised vectors. The corpus is <1k vectors, so approximate indexes would add error for no gain. |
| Keyword | BM25, implemented directly (~40 lines) | Catches exact tokens like "Section 73" that embeddings blur. No new dependency. |
| Graph | In-memory, built from ingestion evidence | Answers relationship questions ("which judgments interpret this section?") without vectors. |
| Backend | FastAPI + SQLAlchemy + SQLite | Typed contracts, one documented OpenAPI surface. Postgres-ready schema. |
| Frontend | React 18 + TypeScript + Vite | No component library; the CSS is purpose-built for information density. |

## The corpus

**263 statutory provisions** across five acts, fetched section-by-section from
[India Code](https://www.indiacode.nic.in/) (official Government of India repository), and
**47 Supreme Court judgments** from the CC-BY-4.0 e-SCR open dataset.

Nothing is synthetic. Every provision and judgment links back to its official source URL, and
repealed provisions are excluded so dead law can never be cited as applicable.

## Evaluation Compliance

The LegalAId multi-agent architecture fulfills all required Agentic AI evaluation criteria:

1. **Blackboard Context Sharing:** Implemented via `LegalBlackboard` with 7 semantic planes (`INTAKE`, `STATUTORY_AUTHORITY`, `SYNTHESIS`, etc.) ensuring strict state revision tracking and context isolation.
2. **Messaging Architecture Comparison:** 4 executable modes implemented and empirically evaluated: Direct Agent-to-Agent, Shared Blackboard, Asynchronous A2A Broker, and Hybrid Blackboard + A2A.
3. **Agentic Pattern Evaluation:** Empirical benchmark comparing ReAct (Google ADK), Sequential, Reflection/Critic, and Blackboard+ReAct across 5 distinct prompt variances.
4. **Model Context Protocol (MCP):** Microservice exposing hybrid retrieval (`search_indian_legal_corpus`) and cryptographic verification (`get_chunk_provenance`).
5. **A2A Communication:** Universal message broker (`A2AMessageBroker`) passing immutable `A2AMessageEnvelope` objects between frameworks.
6. **ADK + CrewAI Interoperability:** Cross-framework integration connecting Google ADK ReAct research loops with CrewAI consensus synthesis teams.
7. **Security & Guardrails:** Prompt injection detection, token bucket rate limiting, API key authentication, and toxic phrase blocking.
8. **Observability & Tracing:** 26-table relational schema logging stage latencies, token consumption, and explicit chunk inclusions.
9. **End-to-End Performance Benchmarking:** High-resolution empirical telemetry recording P50, P95, mean latency, and success rates.
10. **Cost & Runtime Analysis:** Continuous token-budget tracking mapped to AWS Bedrock rates ($0.003 / 1k tokens).
11. **Cloud Deployment Strategy:** Production containerized architecture for AWS ECS / Fargate in `ap-south-1` (Mumbai) with pre-flight container emulation verified.

See the complete requirements audit in [FINAL_EVALUATION_MATRIX.md](FINAL_EVALUATION_MATRIX.md).

---

## Experimental Results

All reported metrics are derived from actual execution runs:

- **End-to-End (E2E) Full Pipeline Report:** [eval/results/e2e_report.md](eval/results/e2e_report.md) (50 full runs measuring LLM inference, retrieval, and total latency).
- **Messaging Architecture Report:** [eval/results/messaging_architecture_report.md](eval/results/messaging_architecture_report.md) ($N=15$ runs per mode).
- **Agentic Pattern Overhead Report:** [eval/results/agentic_pattern_report.md](eval/results/agentic_pattern_report.md) (200 total runs across 5 prompt variances).
- **Evaluation Charts:** 8 publication-ready charts in [eval/results/charts/](eval/results/charts/) (latency, P95, tokens, cost, quality, prompt variance).
- **Cloud Deployment Evidence:** [deployment/DEPLOYMENT_EVIDENCE.md](deployment/DEPLOYMENT_EVIDENCE.md).

---

## Known limitations

- **Speed.** A full run is 3–7 minutes. Code Llama 7B on a 6 GB GPU runs at ~12 tok/s; in Docker
  on CPU it is slower still. Switch to `qwen2.5:3b-instruct` in the UI for roughly 2× speed.
- **Case-law coverage is thin.** 47 judgments from 2023 is a small library for contract disputes
  specifically, so the Similar Case Agent sometimes returns a loosely related authority and
  honestly reports `evidence_sufficient: false`. Widening it is an ingestion run, not a code change.
- **Code Llama is code-specialised.** Its prose is weaker than a general chat model's. Mitigated
  with schema-constrained decoding, low temperature, and strict grounding prompts.
- **Authentication & Multi-Tenancy.** The gateway enforces API key authentication and token-bucket rate limiting for secure single-tenant usage. Multi-tenant firm-level RBAC is schema-modeled and ready for future enterprise directory synchronization.
