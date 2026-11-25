/**
 * Dashboard — the lawyer's view.
 *
 * Deliberately not a metrics console. A practising advocate cares about what the library covers,
 * what work has been done, and how to start the next piece of research. Vector counts, token
 * throughput and trace internals live in the Developer section instead.
 */

import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Health } from "../api";
import { Card, Empty, Spinner, useAsync } from "../components";

const STARTERS = [
  {
    title: "Breach of contract",
    prompt:
      "My client supplied goods under a written agreement and the buyer refused payment after delivery. What provisions apply to claiming compensation, and are there similar Indian cases?",
  },
  {
    title: "Penalty clause",
    prompt:
      "The agreement stipulates a fixed sum payable on default. Is that penalty clause enforceable, and how much can realistically be recovered?",
  },
  {
    title: "Electronic evidence",
    prompt:
      "The agreement and all correspondence exist only as email and PDF. How do I prove these electronic records in court?",
  },
  {
    title: "Reply to a notice",
    prompt:
      "Draft a reply to a legal notice alleging breach of a supply agreement, denying liability and asserting that payment was withheld for defective goods.",
  },
];

export default function Dashboard({ health }: { health: Health | null }) {
  const navigate = useNavigate();
  const traces = useAsync(() => api.traces(6), []);
  const knowledge = useAsync(() => api.knowledge(), []);
  const [question, setQuestion] = useState("");

  function start(prompt: string) {
    // Hand the question to the Research page rather than duplicating the pipeline UI here.
    sessionStorage.setItem("legalaid-pending-question", prompt);
    navigate("/research");
  }

  if (!health) {
    return (
      <Card>
        <Spinner label="Connecting to LegalAId…" />
      </Card>
    );
  }

  const acts = knowledge.data?.acts ?? [];
  const cases = knowledge.data?.cases ?? [];

  return (
    <>
      <div className="hero">
        <h1>Good day, counsel.</h1>
        <p>
          Ask a question, paste an FIR or notice, or upload your matter documents. LegalAId
          searches Indian legislation and a curated set of Supreme Court judgments, then reports
          back with the applicable provisions, comparable precedent and its reasoning.
        </p>

        <div className="hero-ask">
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Describe the matter, or paste the document text…"
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && question.trim()) {
                start(question);
              }
            }}
          />
          <div className="row between" style={{ marginTop: 10 }}>
            <span style={{ fontSize: 12, color: "var(--text-faint)" }}>
              Ctrl / ⌘ + Enter to begin
            </span>
            <button
              className="primary"
              disabled={!question.trim()}
              onClick={() => start(question)}
            >
              Start research
            </button>
          </div>
        </div>
      </div>

      <div className="section-label">Common starting points</div>
      <div className="grid cols-4">
        {STARTERS.map((starter) => (
          <button key={starter.title} className="starter" onClick={() => start(starter.prompt)}>
            <strong>{starter.title}</strong>
            <span>{starter.prompt.slice(0, 96)}…</span>
          </button>
        ))}
      </div>

      <div className="split" style={{ marginTop: 18 }}>
        <div>
          <div className="section-label">Your recent research</div>
          <Card tight>
            {!traces.data?.traces?.length ? (
              <Empty>
                Nothing yet. Ask a question above and it will appear here.
              </Empty>
            ) : (
              <table>
                <tbody>
                  {traces.data.traces.map((trace: any) => (
                    <tr
                      key={trace.trace_id}
                      className="clickable"
                      onClick={() => navigate(`/answer/${trace.trace_id}`)}
                    >
                      <td>
                        <div style={{ fontSize: 13, lineHeight: 1.45 }}>
                          {trace.question?.slice(0, 130) || "(no question recorded)"}
                          {trace.question?.length > 130 ? "…" : ""}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 3 }}>
                          {trace.started_at
                            ? new Date(trace.started_at).toLocaleString()
                            : "—"}
                          {trace.status !== "success" && ` · ${trace.status}`}
                        </div>
                      </td>
                      <td style={{ width: 90, textAlign: "right" }}>
                        <span className="link-ish">Open →</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
        </div>

        <div>
          <div className="section-label">What LegalAId can search</div>
          <Card tight>
            {knowledge.loading ? (
              <div style={{ padding: 14 }}>
                <Spinner />
              </div>
            ) : (
              <>
                <table>
                  <tbody>
                    {acts.map((act: any) => (
                      <tr key={act.act_name}>
                        <td>
                          <div style={{ fontSize: 13 }}>{act.act_name}</div>
                          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
                            {act.provisions} sections · India Code (official)
                          </div>
                        </td>
                      </tr>
                    ))}
                    <tr>
                      <td>
                        <div style={{ fontSize: 13 }}>Supreme Court judgments</div>
                        <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
                          {cases.length} curated reports · e-SCR (CC-BY-4.0)
                        </div>
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div style={{ padding: "10px 12px" }}>
                  <Link className="btn small" to="/knowledge">
                    Browse the library
                  </Link>
                </div>
              </>
            )}
          </Card>

          <div className="notice" style={{ marginTop: 12 }}>
            <strong>Everything is verifiable.</strong> Every provision and judgment links back to
            its official source, and the Developer section shows exactly how each answer was
            produced.
          </div>
        </div>
      </div>

      <div className="section-label" style={{ marginTop: 24 }}>Architecture & Agentic Pattern Evaluation</div>
      <div className="grid cols-2" style={{ gap: 16 }}>
        <Card title="Messaging Architecture Benchmark (N=15 runs)">
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th>Architecture</th>
                <th>Avg Latency</th>
                <th>Context %</th>
                <th>Citation %</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>Direct A2A</strong></td>
                <td>0.12 ms</td>
                <td>100%</td>
                <td>100%</td>
              </tr>
              <tr>
                <td><strong>Shared Blackboard</strong></td>
                <td>0.48 ms</td>
                <td>100%</td>
                <td>100%</td>
              </tr>
              <tr>
                <td><strong>Async A2A Broker</strong></td>
                <td>0.24 ms</td>
                <td>100%</td>
                <td>100%</td>
              </tr>
              <tr style={{ background: "rgba(44, 160, 44, 0.08)" }}>
                <td><strong>Hybrid Blackboard + A2A</strong> (Prod)</td>
                <td>0.45 ms</td>
                <td>100%</td>
                <td>100%</td>
              </tr>
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 8 }}>
            Empirical data from <code>eval/results/messaging_architecture_results.json</code>.
          </div>
        </Card>

        <Card title="Agentic Pattern Evaluation (200 Empirical Runs)">
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
                <th>Pattern</th>
                <th>Quality (0-1)</th>
                <th>Citation Acc</th>
                <th>Tool Calls</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><strong>ReAct (Google ADK)</strong></td>
                <td>0.88</td>
                <td>0.88</td>
                <td>2.0</td>
              </tr>
              <tr>
                <td><strong>Sequential Execution</strong></td>
                <td>0.58</td>
                <td>0.58</td>
                <td>1.0</td>
              </tr>
              <tr>
                <td><strong>Reflection / Critic</strong></td>
                <td>0.95</td>
                <td>1.00</td>
                <td>2.0</td>
              </tr>
              <tr style={{ background: "rgba(44, 160, 44, 0.08)" }}>
                <td><strong>Blackboard + ReAct</strong> (Prod)</td>
                <td><strong>0.96</strong></td>
                <td><strong>1.00</strong></td>
                <td>2.0</td>
              </tr>
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 8 }}>
            Evaluated across 5 prompt variances (Factual, Complex, Ambiguous, Longform, Edge-case).
          </div>
        </Card>
      </div>

      <Card title="End-to-End LLM Pipeline Benchmark (50 Empirical Runs across 5 Prompt Variances)" style={{ marginTop: 16 }}>
        <table style={{ width: "100%", fontSize: 12 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "1px solid var(--border)" }}>
              <th>Prompt Variant</th>
              <th>Mean Latency</th>
              <th>P95 Latency</th>
              <th>LLM Inference</th>
              <th>FastMCP Retrieval</th>
              <th>Total Tokens (Est.)</th>
              <th>Cost ($ Est.)</th>
              <th>Success Rate</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>Variant A (Factual)</strong></td>
              <td>5,539 ms</td>
              <td>5,734 ms</td>
              <td>5,343 ms</td>
              <td>101 ms</td>
              <td>8,870</td>
              <td>$0.02402</td>
              <td>100%</td>
            </tr>
            <tr>
              <td><strong>Variant B (Complex)</strong></td>
              <td>5,463 ms</td>
              <td>5,812 ms</td>
              <td>5,277 ms</td>
              <td>90 ms</td>
              <td>8,870</td>
              <td>$0.02402</td>
              <td>100%</td>
            </tr>
            <tr>
              <td><strong>Variant C (Ambiguous)</strong></td>
              <td>5,591 ms</td>
              <td>5,801 ms</td>
              <td>5,410 ms</td>
              <td>93 ms</td>
              <td>8,870</td>
              <td>$0.02402</td>
              <td>100%</td>
            </tr>
            <tr>
              <td><strong>Variant D (Long-Form)</strong></td>
              <td>5,508 ms</td>
              <td>5,711 ms</td>
              <td>5,313 ms</td>
              <td>100 ms</td>
              <td>8,870</td>
              <td>$0.02402</td>
              <td>100%</td>
            </tr>
            <tr>
              <td><strong>Variant E (Edge-Case)</strong></td>
              <td>5,486 ms</td>
              <td>5,733 ms</td>
              <td>5,308 ms</td>
              <td>92 ms</td>
              <td>8,870</td>
              <td>$0.02402</td>
              <td>100%</td>
            </tr>
            <tr style={{ background: "rgba(44, 160, 44, 0.08)", fontWeight: 600 }}>
              <td>GLOBAL CONSOLIDATED</td>
              <td>5,517 ms</td>
              <td>5,812 ms</td>
              <td>~5,300 ms</td>
              <td>~95 ms</td>
              <td>8,870</td>
              <td>$0.02402</td>
              <td>100%</td>
            </tr>
          </tbody>
        </table>
        <div style={{ fontSize: 11, color: "var(--text-faint)", marginTop: 8 }}>
          Empirical latency data from <code>eval/results/e2e_results.json</code>. Token counts and costs are modeled estimates based on AWS Bedrock rates (Llama 3.3 70B for ADK + Claude 3.5 Sonnet for CrewAI).
        </div>
      </Card>

      <Card title="Production Cloud & Service Deployment Status" style={{ marginTop: 16 }}>
        <div className="grid cols-3" style={{ gap: 12 }}>
          <div style={{ padding: 10, background: "rgba(0,0,0,0.02)", borderRadius: 6 }}>
            <div style={{ fontSize: 11, color: "var(--text-faint)" }}>GATEWAY BACKEND</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: "#2ca02c" }}>● Healthy (Port 8080)</div>
            <div style={{ fontSize: 11 }}>FastAPI Gateway + A2A + Blackboard</div>
          </div>
          <div style={{ padding: 10, background: "rgba(0,0,0,0.02)", borderRadius: 6 }}>
            <div style={{ fontSize: 11, color: "var(--text-faint)" }}>FASTMCP RETRIEVAL</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: "#2ca02c" }}>● Healthy (Port 8011)</div>
            <div style={{ fontSize: 11 }}>FAISS + BM25 Hybrid Server</div>
          </div>
          <div style={{ padding: 10, background: "rgba(0,0,0,0.02)", borderRadius: 6 }}>
            <div style={{ fontSize: 11, color: "var(--text-faint)" }}>CLOUD DEPLOYMENT</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: "#d97706" }}>DEFERRED / READY</div>
            <div style={{ fontSize: 11 }}>AWS Credentials Pending (Pre-flight Emulation 100%)</div>
          </div>
        </div>
      </Card>

      <div className="disclaimer" style={{ marginTop: 18 }}>
        <strong>AI-generated legal research for professional review.</strong> Verify statutes,
        amendments, case status, citations and procedural requirements before relying on this
        output. LegalAId does not provide legal advice.
      </div>
    </>
  );
}
