"""
backend/tests/test_mcp_server.py
=================================
Unit tests for the Model Context Protocol (FastMCP) LegalAId retrieval server
(`legalaid.mcp.retrieval_server`). Validates rubric requirement 3.
"""

from unittest.mock import MagicMock, patch
import pytest

from legalaid.mcp.retrieval_server import get_chunk_provenance, search_indian_legal_corpus


@pytest.mark.asyncio
async def test_search_indian_legal_corpus_tool_mock():
    # Create mock retrieved chunk
    mock_chunk = MagicMock()
    mock_chunk.id = "chk_test_mcp_001"
    mock_chunk.text = "Section 27 of the Indian Contract Act states agreements in restraint of trade are void."
    mock_chunk.source_title = "Indian Contract Act, 1872"
    mock_chunk.section = "Section 27"
    mock_chunk.chapter = "Chapter II"
    mock_chunk.heading = "Agreement in restraint of trade void"
    mock_chunk.source_url = "https://www.indiacode.nic.in/handle/123456789/2187"
    mock_chunk.collection = "STATUTE"
    mock_chunk._rrf_score = 0.88

    with patch("legalaid.mcp.retrieval_server.execute_hybrid_search") as mock_search:
        mock_search.return_value = [mock_chunk]

        result = await search_indian_legal_corpus(
            query="restraint of trade",
            mode="COMBINED_LEGAL_RESEARCH",
            top_k_laws=5,
            top_k_cases=5,
            enable_graph=True,
        )

        assert result["total_retrieved"] == 1
        assert result["query_echo"] == "restraint of trade"
        chunk_data = result["chunks"][0]
        assert chunk_data["chunk_id"] == "chk_test_mcp_001"
        assert chunk_data["section"] == "Section 27"
        assert chunk_data["score"] == 0.88


@pytest.mark.asyncio
async def test_get_chunk_provenance_not_found():
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = None
    mock_scope = MagicMock()
    mock_scope.__enter__.return_value = mock_session

    with patch("legalaid.mcp.retrieval_server.session_scope", return_value=mock_scope):
        res = await get_chunk_provenance("chk_nonexistent_id")
        assert res.get("error") == "Chunk not found"
        assert res.get("chunk_id") == "chk_nonexistent_id"
