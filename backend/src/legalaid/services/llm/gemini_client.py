"""Google Gemini AI client for LLM generation and embeddings.

Integrates with Google's official google-genai SDK.
Used when LLM_PROVIDER=gemini or EMBEDDING_PROVIDER=gemini.
Preserves complete contract compatibility with OllamaClient.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from legalaid.config import get_settings
from legalaid.core.errors import LLMUnavailableError
from legalaid.core.logging import get_logger
from legalaid.db.base import utcnow
from legalaid.domain.contracts import LLMStatus, OllamaModelInfo

logger = get_logger(__name__)


class GeminiGeneration:
    """Normalised result of one Gemini generate call matching OllamaGeneration interface."""

    __slots__ = (
        "text",
        "model",
        "input_tokens",
        "output_tokens",
        "total_duration_ms",
        "load_ms",
        "eval_ms",
        "tokens_per_second",
        "created_at",
        "raw",
    )

    def __init__(
        self,
        *,
        text: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        total_duration_ms: float,
        load_ms: float | None,
        eval_ms: float | None,
        tokens_per_second: float | None,
        created_at: datetime,
        raw: dict[str, Any],
    ) -> None:
        self.text = text
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_duration_ms = total_duration_ms
        self.load_ms = load_ms
        self.eval_ms = eval_ms
        self.tokens_per_second = tokens_per_second
        self.created_at = created_at
        self.raw = raw


class GeminiClient:
    """Async client speaking to Google Gemini via the official google-genai SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._settings = settings
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self._client: genai.Client | None = None
        if self.api_key:
            self._client = genai.Client(api_key=self.api_key)

    def _get_client(self) -> genai.Client:
        if not self.api_key:
            raise LLMUnavailableError(
                "GEMINI_API_KEY is not configured",
                {
                    "provider": "gemini",
                    "hint": "Set GEMINI_API_KEY environment variable in Render or your .env file.",
                },
            )
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    # ------------------------------------------------------------------ status
    async def status(self) -> LLMStatus:
        settings = self._settings
        llm_model = settings.gemini_llm_model
        embedding_model = settings.gemini_embedding_model

        if not self.api_key:
            return LLMStatus(
                provider="gemini",
                base_url="https://generativelanguage.googleapis.com",
                reachable=False,
                configured_llm_model=llm_model,
                configured_embedding_model=embedding_model,
                llm_model_available=False,
                embedding_model_available=False,
                installed_models=[],
                error="GEMINI_API_KEY is not configured. Set the GEMINI_API_KEY environment variable.",
                checked_at=utcnow(),
            )

        client = self._get_client()
        try:
            # Query models asynchronously or test lightweight model availability
            installed_models: list[OllamaModelInfo] = []
            async for model_info in await client.aio.models.list():
                m_name = getattr(model_info, "name", "")
                if m_name.startswith("models/"):
                    m_name = m_name[len("models/") :]
                installed_models.append(
                    OllamaModelInfo(
                        name=m_name,
                        family="gemini",
                    )
                )

            installed_names = {m.name for m in installed_models}
            return LLMStatus(
                provider="gemini",
                base_url="https://generativelanguage.googleapis.com",
                reachable=True,
                configured_llm_model=llm_model,
                configured_embedding_model=embedding_model,
                llm_model_available=llm_model in installed_names or not installed_names,
                embedding_model_available=embedding_model in installed_names or not installed_names,
                installed_models=installed_models,
                error=None,
                checked_at=utcnow(),
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            logger.warning("gemini_status_check_failed", error=str(exc))
            return LLMStatus(
                provider="gemini",
                base_url="https://generativelanguage.googleapis.com",
                reachable=False,
                configured_llm_model=llm_model,
                configured_embedding_model=embedding_model,
                llm_model_available=False,
                embedding_model_available=False,
                installed_models=[],
                error=f"Cannot reach Google Gemini API: {exc}",
                checked_at=utcnow(),
            )

    # -------------------------------------------------------------- generation
    async def generate(
        self,
        *,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        stop: list[str] | None = None,
    ) -> GeminiGeneration:
        settings = self._settings
        model_name = model or settings.gemini_llm_model
        client = self._get_client()

        config_kwargs: dict[str, Any] = {
            "temperature": temperature if temperature is not None else settings.llm_temperature,
            "max_output_tokens": max_tokens or settings.llm_max_tokens,
        }
        if system:
            config_kwargs["system_instruction"] = system
        if stop:
            config_kwargs["stop_sequences"] = stop

        if json_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = json_schema
        elif json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        config = types.GenerateContentConfig(**config_kwargs)

        started = time.perf_counter()
        response = None
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                break
            except APIError as exc:
                is_rate_limit = (
                    exc.code == 429
                    or "quota" in str(exc).lower()
                    or "resource_exhausted" in str(exc).lower()
                )
                if is_rate_limit and attempt < max_retries:
                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(exc).lower())
                    delay = float(match.group(1)) + 2.0 if match else min(60.0, 5.0 * (2 ** (attempt - 1)))
                    logger.warning(
                        "gemini_generation_rate_limited",
                        attempt=attempt,
                        max_retries=max_retries,
                        retry_in=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise LLMUnavailableError(
                    f"Google Gemini API error: {exc.message}",
                    {
                        "provider": "gemini",
                        "model": model_name,
                        "code": exc.code,
                        "response": str(exc),
                    },
                ) from exc
            except Exception as exc:
                raise LLMUnavailableError(
                    f"Google Gemini request failed: {exc}",
                    {
                        "provider": "gemini",
                        "model": model_name,
                        "hint": "Check network connection and GEMINI_API_KEY validity.",
                    },
                ) from exc

        wall_ms = (time.perf_counter() - started) * 1000

        text = response.text or ""
        input_tokens = None
        output_tokens = None
        if response.usage_metadata:
            input_tokens = response.usage_metadata.prompt_token_count
            output_tokens = response.usage_metadata.candidates_token_count

        tokens_per_second: float | None = None
        if output_tokens and wall_ms > 0:
            tokens_per_second = output_tokens / (wall_ms / 1000.0)

        return GeminiGeneration(
            text=text,
            model=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_duration_ms=wall_ms,
            load_ms=None,
            eval_ms=wall_ms,
            tokens_per_second=tokens_per_second,
            created_at=utcnow(),
            raw={"model": model_name, "text": text},
        )

    # --------------------------------------------------------------- embedding
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Embed texts using Gemini embeddings with exact 768 dimensions."""
        if not texts:
            return []

        settings = self._settings
        model_name = model or settings.gemini_embedding_model
        client = self._get_client()

        target_dimensions = settings.embedding_dimensions
        config = types.EmbedContentConfig(output_dimensionality=target_dimensions)

        # Batch embed requests in configured batch sizes
        batch_size = max(1, settings.embedding_batch_size)
        vectors: list[list[float]] = []

        for offset in range(0, len(texts), batch_size):
            batch = texts[offset : offset + batch_size]
            response = None
            max_retries = 6
            for attempt in range(1, max_retries + 1):
                try:
                    response = await client.aio.models.embed_content(
                        model=model_name,
                        contents=batch,
                        config=config,
                    )
                    break
                except APIError as exc:
                    is_rate_limit = (
                        exc.code == 429
                        or "quota" in str(exc).lower()
                        or "resource_exhausted" in str(exc).lower()
                    )
                    if is_rate_limit and attempt < max_retries:
                        match = re.search(r"retry in (\d+(?:\.\d+)?)s", str(exc).lower())
                        delay = float(match.group(1)) + 2.0 if match else min(60.0, 5.0 * (2 ** (attempt - 1)))
                        logger.warning(
                            "gemini_embedding_rate_limited",
                            attempt=attempt,
                            max_retries=max_retries,
                            retry_in=delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise LLMUnavailableError(
                        f"Google Gemini embedding API error: {exc.message}",
                        {"model": model_name, "code": exc.code},
                    ) from exc
                except Exception as exc:
                    raise LLMUnavailableError(
                        f"Google Gemini embedding request failed: {exc}",
                        {"model": model_name, "hint": "Check GEMINI_API_KEY validity."},
                    ) from exc

            embeddings = getattr(response, "embeddings", None)
            if not embeddings:
                raise LLMUnavailableError(
                    "Google Gemini returned no embeddings",
                    {"model": model_name},
                )

            for emb in embeddings:
                vals = getattr(emb, "values", None) or []
                vectors.append([float(v) for v in vals])

        return vectors
