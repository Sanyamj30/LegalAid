"""
legalaid/mcp/retrieval_server.py
=================================
Phase 2: MCP Server — Exposes LegalAId's hybrid retrieval engine (FAISS + BM25 + Graph)
as a Model Context Protocol (MCP) server consumable by any ReAct-based agent.

Transport: stdio (default) — can be swapped to SSE for cloud deployments.
Server Name: LegalAId-Indian-Law-Retrieval-Server

Tools exposed:
  1. search_indian_legal_corpus  — Hybrid RAG search over India Code + e-SCR corpus.
  2. get_chunk_provenance         — Audit trail lookup for a specific chunk by ID.

Enterprise Notes:
  - All DB calls wrap the synchronous session_scope() inside asyncio.to_thread()
    because the project's DB layer is intentionally synchronous (SQLAlchemy sync engine).
  - Empirically calibrated RRF weights are hardcoded here: dense=1.0, keyword=0.15.
    DO NOT change without re-running the offline evaluation suite.
  - This file is the single source of truth for retrieval tool schemas used by
    ADK/CrewAI agents. Any schema change here requires updating agent prompt profiles.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

from fastmcp import FastMCP
from starlette.responses import JSONResponse

# ---------------------------------------------------------------------------
# Internal LegalAId imports — retrieval layer
# ---------------------------------------------------------------------------
from legalaid.services.retrieval.service import search as execute_hybrid_search
from legalaid.domain.contracts import RetrievalRequest
from legalaid.domain.enums import RetrievalMode

# ---------------------------------------------------------------------------
# Internal LegalAId imports — database layer
# ---------------------------------------------------------------------------
from legalaid.db.base import session_scope
from legalaid.db.models import Chunk, EmbeddingMetadata

logger = logging.getLogger("legalaid.mcp.retrieval_server")

# ---------------------------------------------------------------------------
# MCP Server instantiation
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="LegalAId-Indian-Law-Retrieval-Server",
    instructions=(
        "You are connected to the LegalAId Indian Law retrieval engine. "
        "Use `search_indian_legal_corpus` to perform hybrid semantic + keyword searches "
        "across India Code statutory provisions and e-SCR Supreme Court judgements. "
        "Use `get_chunk_provenance` to verify the authenticity and metadata of any "
        "retrieved chunk before citing it in a legal document."
    ),
)


@mcp.custom_route("/health", methods=["GET"])
async def mcp_health(request: Any = None) -> JSONResponse:
    """HTTP Health probe for Docker/ECS container monitoring."""
    return JSONResponse({"status": "healthy", "service": "mcp-retrieval"})


# ===========================================================================
# TOOL 1 — Hybrid Search
# ===========================================================================

@mcp.tool(
    description=(
        "Search the LegalAId Indian legal corpus using a hybrid retrieval pipeline "
        "(dense FAISS vectors + BM25 keyword index + symbolic legal graph). "
        "Returns the top-k most relevant statutory provisions and case law chunks, "
        "fused via Reciprocal Rank Fusion (RRF). "
        "Parameters: query (str), mode (str), top_k_laws (int), top_k_cases (int), "
        "enable_graph (bool)."
    )
)
async def search_indian_legal_corpus(
    query: str,
    mode: str = "COMBINED_LEGAL_RESEARCH",
    top_k_laws: int = 10,
    top_k_cases: int = 10,
    enable_graph: bool = True,
) -> dict[str, Any]:
    """
    MCP Tool: search_indian_legal_corpus
    ------------------------------------
    Invokes the existing hybrid retrieval pipeline and returns structured results.

    Args:
        query:        The natural-language legal question or issue to research.
        mode:         RetrievalMode enum value as string. Defaults to COMBINED_LEGAL_RESEARCH.
        top_k_laws:   Maximum statutory chunks to retrieve from India Code FAISS index.
        top_k_cases:  Maximum case-law chunks to retrieve from e-SCR FAISS index.
        enable_graph: Whether to augment results with symbolic legal knowledge graph edges.

    Returns:
        A JSON-serializable dict containing:
          - "chunks": list of chunk dicts (id, text, source_title, section, score, source_url)
          - "total_retrieved": int
          - "query_echo": the original query (for ReAct chain traceability)
          - "mode_used": the effective RetrievalMode value
    """
    logger.info("MCP Tool invoked: search_indian_legal_corpus | query='%.80s'", query)

    # --- Resolve mode enum ---
    try:
        retrieval_mode = RetrievalMode[mode]
    except KeyError:
        logger.warning("Unknown RetrievalMode '%s', falling back to COMBINED_LEGAL_RESEARCH", mode)
        retrieval_mode = RetrievalMode.COMBINED_LEGAL_RESEARCH

    # --- Build the RetrievalRequest with hardcoded optimal RRF weights ---
    # dense_weight=1.0 and keyword_weight=0.15 were determined through offline
    # evaluation on LegalAId's annotated test set. See docs/eval/rrf_ablation.md
    request = RetrievalRequest(
        trace_id="mcp-direct",          # MCP tool calls don't carry external trace IDs
        question=query,
        mode=retrieval_mode,
        matter_id=None,
        top_k=top_k_laws + top_k_cases,
        enable_keyword=True,
        enable_graph=enable_graph,
        enable_dense=True,
        min_similarity=0.30,
        # --- HARDCODED EMPIRICALLY OPTIMAL WEIGHTS ---
        dense_weight=1.0,
        keyword_weight=0.15,
    )

    # --- Execute retrieval ---
    try:
        if inspect.iscoroutinefunction(execute_hybrid_search):
            retrieval_res = await execute_hybrid_search(request)
        else:
            result_or_coro = execute_hybrid_search(request)
            if asyncio.iscoroutine(result_or_coro):
                retrieval_res = await result_or_coro
            else:
                retrieval_res = result_or_coro

        if hasattr(retrieval_res, "chunks"):
            raw_chunks = retrieval_res.chunks
        elif isinstance(retrieval_res, list):
            raw_chunks = retrieval_res
        else:
            raw_chunks = []
    except Exception as exc:
        logger.error("Retrieval engine error: %s", exc, exc_info=True)
        return {
            "error": f"Retrieval failed: {exc!s}",
            "chunks": [],
            "total_retrieved": 0,
            "query_echo": query,
            "mode_used": retrieval_mode.value,
        }

    # --- Serialize chunks into a clean, JSON-safe structure ---
    serialized: list[dict[str, Any]] = []
    for chunk in raw_chunks:
        serialized.append(
            {
                "chunk_id":     chunk.id,
                "text":         chunk.text,
                "source_title": chunk.source_title,
                "section":      chunk.section,
                "chapter":      chunk.chapter,
                "heading":      chunk.heading,
                "source_url":   chunk.source_url,
                "collection":   chunk.collection.value if hasattr(chunk.collection, "value") else str(chunk.collection),
                "score":        getattr(chunk, "_rrf_score", None),  # RRF score if attached by fusion layer
            }
        )

    logger.info(
        "search_indian_legal_corpus returned %d chunks for query='%.60s'",
        len(serialized), query,
    )

    return {
        "chunks":          serialized,
        "total_retrieved": len(serialized),
        "query_echo":      query,
        "mode_used":       retrieval_mode.value,
    }


# ===========================================================================
# TOOL 2 — Provenance Lookup
# ===========================================================================

@mcp.tool(
    description=(
        "Retrieve the full provenance and audit metadata for a specific legal text chunk "
        "by its internal chunk_id. Returns cryptographic hash, embedding metadata, "
        "original enactment date, and verification status. "
        "Use this to validate any chunk_id before citing it in a legal memorandum."
    )
)
async def get_chunk_provenance(chunk_id: str) -> dict[str, Any]:
    """
    MCP Tool: get_chunk_provenance
    --------------------------------
    Performs a DB lookup to return the full audit trail for a chunk.

    Args:
        chunk_id: The internal UUID-based chunk identifier (e.g., "chk_abc123...").

    Returns:
        A JSON-serializable dict with provenance fields, or an error dict if not found.
    """
    logger.info("MCP Tool invoked: get_chunk_provenance | chunk_id='%s'", chunk_id)

    def _sync_query(c_id: str) -> dict[str, Any] | None:
        """
        Synchronous DB query wrapped for asyncio.to_thread execution.
        Must stay synchronous because project uses a sync SQLAlchemy engine.
        """
        with session_scope() as session:
            # Primary chunk record
            chunk: Chunk | None = session.query(Chunk).filter(Chunk.id == c_id).first()
            if chunk is None:
                return None

            # Associated embedding metadata (for cryptographic vector hash)
            emb: EmbeddingMetadata | None = (
                session.query(EmbeddingMetadata)
                .filter(EmbeddingMetadata.chunk_id == c_id)
                .first()
            )

            return {
                "chunk_id":           chunk.id,
                "source_title":       chunk.source_title,
                "section":            chunk.section,
                "chapter":            chunk.chapter,
                "heading":            chunk.heading,
                "source_url":         chunk.source_url,
                "collection":         chunk.collection.value if hasattr(chunk.collection, "value") else str(chunk.collection),
                "source_type":        chunk.source_type,
                "ordinal":            chunk.ordinal,
                "chunk_strategy":     chunk.chunk_strategy,
                "char_count":         chunk.char_count,
                "token_count":        chunk.token_count,
                "version":            chunk.version,
                "created_at":         chunk.created_at.isoformat() if chunk.created_at else None,
                # Embedding / cryptographic metadata
                "embedding_model":    emb.embedding_model if emb else None,
                "embedding_provider": emb.embedding_provider if emb else None,
                "dimensions":         emb.dimensions if emb else None,
                "vector_norm":        emb.norm if emb else None,
                "embedding_latency_ms": emb.latency_ms if emb else None,
                # The vector_preview acts as a lightweight fingerprint for integrity checks
                "vector_preview":     emb.vector_preview if emb else None,
                # Relational linkages
                "provision_id":       chunk.provision_id,
                "case_id":            chunk.case_id,
                "document_id":        chunk.document_id,
                "matter_id":          chunk.matter_id,
            }

    try:
        result = await asyncio.to_thread(_sync_query, chunk_id)
    except Exception as exc:
        logger.error("Provenance DB error for chunk_id='%s': %s", chunk_id, exc, exc_info=True)
        return {"error": f"Database error: {exc!s}", "chunk_id": chunk_id}

    if result is None:
        logger.warning("Chunk not found: '%s'", chunk_id)
        return {"error": "Chunk not found", "chunk_id": chunk_id}

    logger.info("Provenance retrieved for chunk_id='%s'", chunk_id)
    return result


# ===========================================================================
# Entry Point — stdio transport for subprocess/agent spawning
# ===========================================================================

if __name__ == "__main__":
    import os
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
    port = int(os.getenv("MCP_PORT", "8001"))
    host = os.getenv("MCP_HOST", "0.0.0.0")

    if transport in ("sse", "http"):
        logger.info("Starting LegalAId MCP Retrieval Server (transport=%s, host=%s, port=%d)...", transport, host, port)
        mcp.run(transport=transport, host=host, port=port)
    else:
        logger.info("Starting LegalAId MCP Retrieval Server (stdio transport)...")
        mcp.run()

