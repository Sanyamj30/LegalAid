"""Comprehensive test suite for AI providers: Ollama preservation, Gemini integration,
independent provider selection, vector index segregation, and re-indexing migration.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from legalaid.agents.base import parse_json_response
from legalaid.config import Settings, get_settings
from legalaid.core.errors import LLMUnavailableError
from legalaid.domain.contracts import LLMGenerateRequest
from legalaid.domain.enums import AgentKey, Collection
from legalaid.services.embedding.service import _get_embedding_client
from legalaid.services.llm.gemini_client import GeminiClient
from legalaid.services.llm.ollama_client import OllamaClient
from legalaid.services.llm.service import _get_llm_client, generate
from legalaid.services.vector.reindex import run_reindex
from legalaid.services.vector.store import VectorStore, get_store


def test_default_provider_configuration_is_ollama() -> None:
    """Requirement 1, 2, 3: Ollama must be the default provider with all defaults intact."""
    settings = get_settings()
    assert settings.llm_provider == "ollama"
    assert settings.embedding_provider == "ollama"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.llm_model == "codellama:7b-instruct"
    assert settings.embedding_model == "nomic-embed-text"
    assert settings.embedding_dimensions == 768


def test_gemini_settings_defaults() -> None:
    """Requirement 4, 8: Gemini configuration defaults exist and are safe."""
    settings = get_settings()
    assert hasattr(settings, "gemini_api_key")
    assert hasattr(settings, "gemini_llm_model")
    assert hasattr(settings, "gemini_embedding_model")
    assert settings.gemini_llm_model == "gemini-2.5-flash"
    assert settings.gemini_embedding_model == "gemini-embedding-001"


def test_independent_provider_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 4: Providers must be independently selectable (Gemini LLM + Ollama Embed, or vice versa)."""
    # Case A: Gemini LLM + Ollama Embeddings
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    get_settings.cache_clear()
    try:
        llm_client = _get_llm_client()
        embed_client = _get_embedding_client()
        assert isinstance(llm_client, GeminiClient)
        assert isinstance(embed_client, OllamaClient)
    finally:
        get_settings.cache_clear()

    # Case B: Ollama LLM + Gemini Embeddings
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    get_settings.cache_clear()
    try:
        llm_client = _get_llm_client()
        embed_client = _get_embedding_client()
        assert isinstance(llm_client, OllamaClient)
        assert isinstance(embed_client, GeminiClient)
    finally:
        get_settings.cache_clear()


def test_ollama_client_preservation() -> None:
    """Requirement 1, 2: OllamaClient implementation is preserved and instantiated cleanly."""
    client = OllamaClient()
    assert hasattr(client, "generate")
    assert hasattr(client, "embed")
    assert hasattr(client, "status")
    assert hasattr(client, "list_models")
    assert client.base_url == "http://localhost:11434"


@pytest.mark.asyncio
async def test_gemini_client_missing_key_behavior() -> None:
    """Requirement 10: When GEMINI_API_KEY is not set, Gemini operations fail gracefully with actionable hints."""
    client = GeminiClient(api_key="")
    status = await client.status()
    assert status.reachable is False
    assert status.provider == "gemini"
    assert "GEMINI_API_KEY is not configured" in (status.error or "")

    with pytest.raises(LLMUnavailableError) as exc_info:
        await client.generate(prompt="Hello")
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)

    with pytest.raises(LLMUnavailableError) as exc_info:
        await client.embed(["test text"])
    assert "GEMINI_API_KEY is not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_gemini_generation_and_structured_json_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 8: Verify Gemini generation and structured JSON compatibility with agent parser."""
    client = GeminiClient(api_key="fake-test-key")

    expected_output = {
        "statutes": ["Section 73 of the Indian Contract Act, 1872"],
        "reasoning": "Breach of contract damages are governed by Section 73.",
    }
    raw_json_str = json.dumps(expected_output)

    mock_response = MagicMock()
    mock_response.text = raw_json_str
    mock_usage = MagicMock()
    mock_usage.prompt_token_count = 42
    mock_usage.candidates_token_count = 28
    mock_response.usage_metadata = mock_usage

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(client, "_get_client", lambda: mock_client)

    result = await client.generate(
        prompt="Identify applicable statutes.",
        system="You are a legal research agent.",
        model="gemini-2.5-flash",
        temperature=0.1,
        max_tokens=1024,
        json_mode=True,
    )

    assert result.text == raw_json_str
    assert result.model == "gemini-2.5-flash"
    assert result.input_tokens == 42
    assert result.output_tokens == 28
    assert result.total_duration_ms >= 0

    # Test compatibility with existing parse_json_response used by agents
    parsed, parse_error = parse_json_response(result.text)
    assert parse_error is None
    assert parsed == expected_output


@pytest.mark.asyncio
async def test_gemini_embedding_output_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 7: Verify Gemini embeddings produce exact 768 dimensions matching vector DB."""
    client = GeminiClient(api_key="fake-test-key")

    # Generate dummy 768-dimensional vector
    dummy_vector_768 = [0.01 * (i % 50) for i in range(768)]
    mock_emb = MagicMock()
    mock_emb.values = dummy_vector_768

    mock_response = MagicMock()
    mock_response.embeddings = [mock_emb, mock_emb]

    mock_client = MagicMock()
    mock_client.aio.models.embed_content = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(client, "_get_client", lambda: mock_client)

    vectors = await client.embed(["Text chunk 1", "Text chunk 2"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 768
    assert len(vectors[1]) == 768


def test_vector_index_segregation_by_provider_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 5, 6: Verify indexes are segregated by provider & model and Ollama files are never overwritten."""
    from pathlib import Path
    from legalaid.services.vector.store import CollectionIndex

    # Mock _load to avoid Windows-specific faiss-cpu SWIG DLL initialization failure
    monkeypatch.setattr(CollectionIndex, "_load", lambda self: None)

    base_dir = tmp_path / "indexes"
    base_dir.mkdir(parents=True)

    # 1. Ollama default store uses base_dir directly (backward compatibility)
    ollama_store = VectorStore(
        index_dir=base_dir,
        dimensions=768,
        provider="ollama",
        embedding_model="nomic-embed-text",
    )
    assert ollama_store.index_dir == base_dir

    # 2. Gemini store uses segregated subdirectory
    gemini_store = VectorStore(
        index_dir=base_dir,
        dimensions=768,
        provider="gemini",
        embedding_model="gemini-embedding-001",
    )
    assert gemini_store.index_dir != base_dir
    assert gemini_store.index_dir == base_dir / "gemini_gemini-embedding-001"
    assert gemini_store.index_dir.exists()

    # 3. Verify index file paths are completely isolated
    ollama_acts_idx = ollama_store.index(Collection.LEGAL_ACTS)
    gemini_acts_idx = gemini_store.index(Collection.LEGAL_ACTS)

    assert ollama_acts_idx.index_path != gemini_acts_idx.index_path
    assert ollama_acts_idx.index_path == base_dir / "legal_acts.faiss"
    assert gemini_acts_idx.index_path == base_dir / "gemini_gemini-embedding-001" / "legal_acts.faiss"


@pytest.mark.asyncio
async def test_reindex_dry_run_preserves_ollama(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 6, 7: Verify migration dry-run reports plan without writing files or modifying Ollama indexes."""
    from legalaid.services.vector.store import CollectionIndex

    # Mock _load to avoid Windows-specific faiss-cpu SWIG DLL initialization failure
    monkeypatch.setattr(CollectionIndex, "_load", lambda self: None)

    result = await run_reindex(
        target_provider="gemini",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["status"] == "planned"
    assert result["target_provider"] == "gemini"
    assert result["target_model"] == "gemini-embedding-001"
    assert result["target_dimensions"] == 768
    assert result["ollama_indexes_preserved"] is True
    assert "gemini_gemini-embedding-001" in result["segregated_index_dir"]
    assert "LEGAL_ACTS" in result["collections"]
    assert "CASE_LAW" in result["collections"]
    assert "USER_DOCUMENTS" in result["collections"]


@pytest.mark.asyncio
async def test_gateway_health_and_models_with_gemini_does_not_call_ollama(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 3: Gemini health and models endpoints do not accidentally invoke OllamaClient."""
    from starlette.testclient import TestClient
    from legalaid.db.base import utcnow
    from legalaid.domain.contracts import LLMStatus, OllamaModelInfo
    from legalaid.services.gateway.app import app
    from legalaid.services.vector.store import CollectionIndex

    monkeypatch.setattr(CollectionIndex, "_load", lambda self: None)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setenv("API_KEYS", '["test-provider-key"]')
    get_settings.cache_clear()

    # OllamaClient.status should NEVER be called
    mock_ollama_status = AsyncMock(side_effect=AssertionError("OllamaClient.status was unexpectedly called!"))
    monkeypatch.setattr(OllamaClient, "status", mock_ollama_status)

    # GeminiClient.status returns healthy
    mock_gemini_status = AsyncMock(
        return_value=LLMStatus(
            provider="gemini",
            base_url="https://generativelanguage.googleapis.com",
            reachable=True,
            configured_llm_model="gemini-2.5-flash",
            configured_embedding_model="gemini-embedding-001",
            llm_model_available=True,
            embedding_model_available=True,
            installed_models=[OllamaModelInfo(name="gemini-2.5-flash", family="gemini")],
            error=None,
            checked_at=utcnow(),
        )
    )
    monkeypatch.setattr(GeminiClient, "status", mock_gemini_status)

    try:
        client = TestClient(app)

        # 1. Verify /api/system/health
        health_resp = client.get("/api/system/health")
        assert health_resp.status_code == 200
        health_json = health_resp.json()
        assert health_json["status"] == "ok"
        assert health_json["ollama"]["reachable"] is True

        # 2. Verify /api/models (authenticated)
        models_resp = client.get("/api/models", headers={"X-API-Key": "test-provider-key"})
        assert models_resp.status_code == 200
        models_json = models_resp.json()
        assert models_json["provider"] == "gemini"
        assert models_json["default_llm_model"] == "gemini-2.5-flash"
        assert models_json["reachable"] is True
        assert len(models_json["installed"]) == 1
        assert models_json["installed"][0]["name"] == "gemini-2.5-flash"

        # Assert OllamaClient.status was never called
        mock_ollama_status.assert_not_called()
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_screen_corpus_distance_refusal_when_index_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 9: Reproduce the exact refusal reported in production when the index is empty.
    
    When collections have 0 vectors (as on a fresh Render deployment):
    - best similarity is 0.0 (< 0.55 floor).
    - If no legal markers: returns Verdict.BLOCK with blocked_by=corpus_distance.
    - If legal markers present: returns Verdict.WARN (allowed with warning).
    """
    from legalaid.core.guardrails import GuardrailLayer, Verdict, screen_corpus_distance, screen_input
    from legalaid.domain.enums import Collection

    # Mock store with empty collections (count == 0)
    mock_idx = MagicMock()
    mock_idx.count = 0
    mock_store = MagicMock()
    mock_store.all_indexes.return_value = {
        Collection.LEGAL_ACTS: mock_idx,
        Collection.CASE_LAW: mock_idx,
        Collection.USER_DOCUMENTS: mock_idx,
    }
    monkeypatch.setattr("legalaid.services.vector.store.get_store", lambda: mock_store)

    # Mock embed_texts_raw to simulate Gemini embedding API returning HTTP 200
    mock_vector = [0.1] * 768
    monkeypatch.setattr(
        "legalaid.services.embedding.service.embed_texts_raw",
        AsyncMock(return_value=([mock_vector], 10.0, "gemini-embedding-001")),
    )

    # Case 1: Query without legal markers -> Refused (Verdict.BLOCK)
    non_marker_query = "Can my employer terminate me without cause?"
    result = await screen_corpus_distance(non_marker_query)
    assert result.verdict == Verdict.BLOCK
    assert result.evidence["best_similarity"] == 0.0
    assert result.evidence["nearest_collection"] is None
    assert "Nothing in the knowledge base is relevant" in result.reason

    decision = await screen_input(non_marker_query)
    assert decision.allowed is False
    assert decision.blocked_by == GuardrailLayer.CORPUS_DISTANCE

    # Case 2: Query WITH legal markers -> Warned (Verdict.WARN, not blocked)
    marker_query = "What does Section 73 of the Indian Contract Act provide for breach?"
    result_with_marker = await screen_corpus_distance(marker_query)
    assert result_with_marker.verdict == Verdict.WARN
    assert result_with_marker.evidence["best_similarity"] == 0.0

    decision_with_marker = await screen_input(marker_query)
    assert decision_with_marker.allowed is True


@pytest.mark.asyncio
async def test_screen_corpus_distance_allowed_when_index_populated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 9: Validate that when the Gemini index is populated with vectors,
    legal queries match >= floor and are allowed without refusal.
    """
    from legalaid.core.guardrails import Verdict, screen_corpus_distance, screen_input
    from legalaid.domain.enums import Collection

    # Mock store with populated collection
    mock_hit = MagicMock()
    mock_hit.similarity = 0.82
    mock_hit.chunk_id = "chunk-contract-73"

    mock_idx = MagicMock()
    mock_idx.count = 1500
    mock_idx.search.return_value = ([mock_hit], 1500, 0)

    mock_store = MagicMock()
    mock_store.all_indexes.return_value = {
        Collection.LEGAL_ACTS: mock_idx,
        Collection.CASE_LAW: MagicMock(count=0),
        Collection.USER_DOCUMENTS: MagicMock(count=0),
    }
    monkeypatch.setattr("legalaid.services.vector.store.get_store", lambda: mock_store)

    # Query embedding is returned by Gemini
    mock_vector = [0.05] * 768
    monkeypatch.setattr(
        "legalaid.services.embedding.service.embed_texts_raw",
        AsyncMock(return_value=([mock_vector], 12.0, "gemini-embedding-001")),
    )

    query = "Can my employer terminate me without cause?"
    result = await screen_corpus_distance(query)
    assert result.verdict == Verdict.ALLOW
    assert result.evidence["best_similarity"] == 0.82
    assert result.evidence["nearest_collection"] == Collection.LEGAL_ACTS.value

    decision = await screen_input(query)
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_refused_research_query_returns_http_200_payload(monkeypatch: pytest.MonkeyPatch, db: None) -> None:
    """Validate that when a research query is refused by corpus_distance guardrail,
    the orchestrator and gateway return HTTP 200 with result.answer['refused'] == True,
    confirming that the backend does NOT return HTTP 503 on request_refused.
    """
    from legalaid.agents.orchestrator import Orchestrator
    from legalaid.core.guardrails import GuardrailDecision, GuardrailLayer, LayerResult, Verdict
    from legalaid.services.gateway.app import _serialise_result

    guard = GuardrailDecision(
        allowed=False,
        layers=[
            LayerResult(
                GuardrailLayer.CORPUS_DISTANCE,
                Verdict.BLOCK,
                "Nothing in the knowledge base is relevant to this request.",
                {"best_similarity": 0.0, "floor": 0.55},
            )
        ],
        blocked_by=GuardrailLayer.CORPUS_DISTANCE,
        message="Nothing in the knowledge base is relevant to this request.",
    )
    monkeypatch.setattr("legalaid.agents.orchestrator.screen_input", AsyncMock(return_value=guard))

    orchestrator = Orchestrator()
    result = await orchestrator.run(question="Can my employer terminate me without cause?")
    assert result.answer["refused"] is True
    assert result.answer["refused_by"] == "corpus_distance"

    # Serialise as gateway does
    serialized = _serialise_result(result)
    assert serialized["answer"]["refused"] is True
    assert serialized["answer"]["refused_by"] == "corpus_distance"
    assert serialized["guardrail"]["blocked_by"] == "corpus_distance"


def test_database_url_postgresql_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that PostgreSQL URIs (e.g. from Supabase Session Pooler) are correctly
    normalized to use the installed psycopg (v3) driver while preserving SQLite.
    """
    from legalaid.config import Settings

    # Case 1: Supabase / Heroku style postgres://
    s1 = Settings(database_url="postgres://user:pass@aws-0-region.pooler.supabase.com:5432/postgres")
    assert s1.database_url.startswith("postgresql+psycopg://")
    assert "user:pass@aws-0-region.pooler.supabase.com:5432/postgres" in s1.database_url

    # Case 2: Standard postgresql://
    s2 = Settings(database_url="postgresql://user:pass@aws-0-region.pooler.supabase.com:5432/postgres")
    assert s2.database_url.startswith("postgresql+psycopg://")

    # Case 3: Already explicit postgresql+psycopg://
    s3 = Settings(database_url="postgresql+psycopg://user:pass@aws-0-region.pooler.supabase.com:5432/postgres")
    assert s3.database_url.startswith("postgresql+psycopg://")

    # Case 4: Default SQLite preserved when empty
    s4 = Settings(database_url="")
    assert s4.database_url.startswith("sqlite+pysqlite:///")


def test_postgresql_all_models_ddl_compilation() -> None:
    """Verify that all 26 registered database tables compile cleanly to valid PostgreSQL DDL."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    from legalaid.db.base import Base
    import legalaid.db.models  # registers all 26 tables

    assert len(Base.metadata.tables) == 26
    for name, table in Base.metadata.tables.items():
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in ddl
        assert name in ddl


def test_postgresql_engine_pool_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that PostgreSQL engines enable pool_pre_ping and pool_recycle for cloud poolers."""
    from legalaid.db.base import get_engine, reset_engine

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/testdb")
    get_settings.cache_clear()
    reset_engine()

    try:
        engine = get_engine()
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        # Assert pool pre-ping is enabled for cloud session poolers
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == 1800
    finally:
        reset_engine()
        get_settings.cache_clear()


