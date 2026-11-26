"""Supabase Storage client for persistent FAISS vector indexes.

Persists local binary FAISS indexes (*.faiss) and metadata sidecars (*.meta.json)
to Supabase Storage object storage, enabling seamless persistence across Render
Free deployment restarts and rebuilds without modifying the underlying FAISS search engine.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from legalaid.config import get_settings
from legalaid.core.logging import get_logger

logger = get_logger(__name__)


class RemoteVectorStorage:
    """Manages backup and restoration of FAISS index files to/from Supabase Storage."""

    def __init__(
        self,
        supabase_url: str | None = None,
        supabase_key: str | None = None,
        bucket: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.url = (supabase_url if supabase_url is not None else (settings.supabase_url or "")).rstrip("/")
        self.key = supabase_key if supabase_key is not None else (settings.supabase_key or "")
        self.bucket = bucket if bucket is not None else (settings.supabase_storage_bucket or "legalaid-indexes")
        self.enabled = (
            enabled if enabled is not None else settings.vector_storage_sync_enabled
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.enabled and self.url and self.key and self.bucket)

    def _headers(self, content_type: str = "application/octet-stream") -> dict[str, str]:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": content_type,
        }

    def remote_prefix(self, provider: str, embedding_model: str) -> str:
        """Isolated object storage path for the given provider and model."""
        safe_model = embedding_model.replace(":", "_").replace("/", "_").replace(".", "_")
        return f"{provider.lower()}_{safe_model}"

    # --------------------------------------------------------------- REST API
    def upload_file(self, local_path: Path, remote_key: str, *, max_retries: int = 3) -> bool:
        """Upload a single file to Supabase Storage with retry handling."""
        if not self.is_configured:
            return False
        if not local_path.exists():
            logger.warning("remote_storage_upload_missing_file", path=str(local_path))
            return False

        content_type = (
            "application/json"
            if local_path.suffix == ".json"
            else "application/octet-stream"
        )
        url = f"{self.url}/storage/v1/object/{self.bucket}/{remote_key}"
        headers = {**self._headers(content_type), "x-upsert": "true"}

        data = local_path.read_bytes()
        for attempt in range(1, max_retries + 1):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(url, headers=headers, content=data)
                    if resp.status_code in {200, 201}:
                        logger.info(
                            "remote_storage_uploaded",
                            remote_key=remote_key,
                            bytes=len(data),
                            attempt=attempt,
                        )
                        return True
                    logger.warning(
                        "remote_storage_upload_status",
                        remote_key=remote_key,
                        status_code=resp.status_code,
                        response=resp.text[:200],
                        attempt=attempt,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "remote_storage_upload_attempt_failed",
                    remote_key=remote_key,
                    error=str(exc),
                    attempt=attempt,
                )
            if attempt < max_retries:
                time.sleep(0.5 * attempt)

        return False

    def download_file(self, remote_key: str, local_path: Path, *, max_retries: int = 3) -> bool:
        """Download an object from Supabase Storage and write atomically to disk."""
        if not self.is_configured:
            return False

        url = f"{self.url}/storage/v1/object/authenticated/{self.bucket}/{remote_key}"
        headers = self._headers()

        for attempt in range(1, max_retries + 1):
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.get(url, headers=headers)
                    if resp.status_code == 200:
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        tmp_path = local_path.with_suffix(".download.tmp")
                        tmp_path.write_bytes(resp.content)
                        tmp_path.replace(local_path)
                        logger.info(
                            "remote_storage_downloaded",
                            remote_key=remote_key,
                            bytes=len(resp.content),
                            target=str(local_path),
                        )
                        return True
                    if resp.status_code in {404, 400}:
                        # Remote object does not exist yet (normal on fresh deployment)
                        return False
                    logger.warning(
                        "remote_storage_download_status",
                        remote_key=remote_key,
                        status_code=resp.status_code,
                        attempt=attempt,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "remote_storage_download_attempt_failed",
                    remote_key=remote_key,
                    error=str(exc),
                    attempt=attempt,
                )
            if attempt < max_retries:
                time.sleep(0.5 * attempt)

        return False

    def list_objects(self, prefix: str) -> list[str]:
        """List objects under a given prefix."""
        if not self.is_configured:
            return []
        url = f"{self.url}/storage/v1/object/list/{self.bucket}"
        headers = self._headers("application/json")
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    url,
                    headers=headers,
                    json={"prefix": prefix, "limit": 100, "sortBy": {"column": "name", "order": "asc"}},
                )
                if resp.status_code == 200:
                    items = resp.json()
                    return [item["name"] for item in items if isinstance(item, dict) and "name" in item]
        except Exception as exc:  # noqa: BLE001
            logger.warning("remote_storage_list_failed", prefix=prefix, error=str(exc))
        return []

    # ---------------------------------------------------- sync workflows
    def restore_indexes(
        self,
        target_dir: Path,
        provider: str,
        embedding_model: str,
    ) -> int:
        """Download remote index and metadata files for the current provider/model if missing locally."""
        if not self.is_configured:
            return 0

        prefix = self.remote_prefix(provider, embedding_model)
        remote_files = self.list_objects(prefix)
        if not remote_files:
            logger.info("remote_storage_no_remote_indexes", prefix=prefix)
            return 0

        restored = 0
        for name in remote_files:
            if not (name.endswith(".faiss") or name.endswith(".meta.json")):
                continue
            remote_key = f"{prefix}/{name}"
            local_path = target_dir / name
            # If local file already exists and is non-empty, keep local unless forced
            if local_path.exists() and local_path.stat().st_size > 0:
                continue

            if self.download_file(remote_key, local_path):
                restored += 1

        logger.info(
            "remote_storage_restore_completed",
            prefix=prefix,
            files_restored=restored,
            target_dir=str(target_dir),
        )
        return restored

    def persist_indexes(
        self,
        source_dir: Path,
        provider: str,
        embedding_model: str,
        collection_name: str | None = None,
    ) -> int:
        """Upload non-empty local index files to Supabase Storage."""
        if not self.is_configured:
            return 0
        if not source_dir.exists():
            return 0

        prefix = self.remote_prefix(provider, embedding_model)
        uploaded = 0

        targets = (
            [collection_name.lower()]
            if collection_name
            else [p.stem for p in source_dir.glob("*.faiss")]
        )

        for col in targets:
            faiss_path = source_dir / f"{col}.faiss"
            meta_path = source_dir / f"{col}.meta.json"

            # Safety Rule (Req 11): Never overwrite remote with an empty index
            if not (faiss_path.exists() and meta_path.exists()):
                continue

            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                vector_count = len(meta.get("vectors", []))
                if vector_count == 0:
                    logger.info(
                        "remote_storage_skip_empty_index_upload",
                        collection=col,
                        prefix=prefix,
                    )
                    continue
            except Exception:  # noqa: BLE001
                continue

            # Upload .faiss and .meta.json
            faiss_ok = self.upload_file(faiss_path, f"{prefix}/{col}.faiss")
            meta_ok = self.upload_file(meta_path, f"{prefix}/{col}.meta.json")
            if faiss_ok and meta_ok:
                uploaded += 1

        logger.info(
            "remote_storage_persist_completed",
            prefix=prefix,
            indexes_uploaded=uploaded,
        )
        return uploaded
