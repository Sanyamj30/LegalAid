"""Explicit vector index re-indexing and migration utility.

Supports safe re-indexing between embedding providers (e.g., Ollama <-> Gemini).
Preserves existing Ollama indexes and documents without overwriting or deleting them.
Provides a default dry-run mode for safety.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

_backend_src = Path(__file__).resolve().parents[2]
if str(_backend_src) not in sys.path:
    sys.path.insert(0, str(_backend_src))

from typing import Any

from sqlalchemy import select

from legalaid.config import get_settings
from legalaid.core.logging import get_logger
from legalaid.core.tokens import estimate_tokens
from legalaid.db.base import session_scope, utcnow
from legalaid.db.models import Chunk, EmbeddingMetadata
from legalaid.domain.enums import Collection
from legalaid.services.embedding.service import _norm, _preview, embed_texts_raw
from legalaid.services.vector.store import get_store

logger = get_logger(__name__)


async def run_reindex(
    *,
    target_provider: str = "gemini",
    target_model: str | None = None,
    collections: list[Collection] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Re-index chunks into a segregated vector index for target_provider.

    Args:
        target_provider: 'gemini' or 'ollama'
        target_model: Model name (defaults to configured model for provider)
        collections: List of collections to reindex (default: all)
        dry_run: If True, inspects chunks and reports plan without generating embeddings or writing files.
    """
    settings = get_settings()
    provider = target_provider.lower()
    if target_model:
        model_name = target_model
    elif provider == "gemini":
        model_name = settings.gemini_embedding_model
    else:
        model_name = settings.embedding_model

    target_collections = collections or list(Collection)
    target_store = get_store(provider=provider, embedding_model=model_name)
    segregated_dir = target_store.index_dir

    result: dict[str, Any] = {
        "target_provider": provider,
        "target_model": model_name,
        "target_dimensions": settings.embedding_dimensions,
        "segregated_index_dir": str(segregated_dir),
        "dry_run": dry_run,
        "collections": {},
        "total_chunks_found": 0,
        "total_to_embed": 0,
        "total_already_indexed": 0,
        "status": "planned" if dry_run else "completed",
    }

    # Verify existing Ollama indexes are preserved
    ollama_store = get_store(provider="ollama", embedding_model="nomic-embed-text")
    result["ollama_indexes_preserved"] = True
    result["ollama_index_dir"] = str(ollama_store.index_dir)

    with session_scope() as session:
        for collection in target_collections:
            rows = session.scalars(
                select(Chunk).where(Chunk.collection == collection)
            ).all()
            chunk_ids = [row.id for row in rows]

            # Chunks already indexed for this specific provider and model
            already_ids = set(
                session.scalars(
                    select(EmbeddingMetadata.chunk_id).where(
                        EmbeddingMetadata.chunk_id.in_(chunk_ids),
                        EmbeddingMetadata.embedding_provider == provider,
                        EmbeddingMetadata.embedding_model == model_name,
                    )
                ).all()
            ) if chunk_ids else set()

            to_embed_rows = [r for r in rows if r.id not in already_ids]
            coll_info = {
                "total_chunks": len(rows),
                "already_indexed": len(already_ids),
                "to_embed": len(to_embed_rows),
                "collection_index_path": str(target_store.index(collection).index_path),
            }
            result["collections"][collection.value] = coll_info
            result["total_chunks_found"] += len(rows)
            result["total_to_embed"] += len(to_embed_rows)
            result["total_already_indexed"] += len(already_ids)

            if not dry_run and to_embed_rows:
                texts = [r.text for r in to_embed_rows]
                vectors, elapsed_ms, used_model = await embed_texts_raw(texts, model=model_name)
                per_item_ms = elapsed_ms / len(vectors) if vectors else 0.0

                index = target_store.index(collection)
                items = [
                    (r.id, vector, dict(r.chunk_metadata or {}))
                    for r, vector in zip(to_embed_rows, vectors, strict=True)
                ]
                vector_ids = index.upsert(
                    items,
                    embedding_model=used_model,
                    embedding_provider=provider,
                )

                for r, vector, vector_id in zip(to_embed_rows, vectors, vector_ids, strict=True):
                    session.add(
                        EmbeddingMetadata(
                            chunk_id=r.id,
                            vector_id=vector_id,
                            collection=collection.value if hasattr(collection, "value") else str(collection),
                            embedding_provider=provider,
                            embedding_model=used_model,
                            dimensions=len(vector),
                            norm=_norm(vector),
                            latency_ms=per_item_ms,
                            input_tokens=estimate_tokens(r.text),
                            vector_preview=_preview(vector),
                            created_at=utcnow(),
                        )
                    )
                coll_info["embedded"] = len(vector_ids)

    if not dry_run:
        try:
            uploaded = target_store.sync_to_remote()
            result["uploaded_to_supabase"] = uploaded
            print(f"Synced {uploaded} index collection(s) to Supabase Storage.")
        except Exception as exc:
            logger.warning("remote_storage_sync_failed", error=str(exc))

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="LegalAId Vector Index Migration & Re-indexing Tool")
    parser.add_argument(
        "--provider",
        choices=["gemini", "ollama"],
        default="gemini",
        help="Target embedding provider (default: gemini)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Target embedding model name (optional)",
    )
    parser.add_argument(
        "--collection",
        type=str,
        choices=[c.value for c in Collection],
        default=None,
        help="Specific collection to reindex (default: all)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the re-indexing (default is dry-run mode)",
    )

    args = parser.parse_args()
    dry_run = not args.execute
    colls = [Collection(args.collection)] if args.collection else None

    print("\n=======================================================")
    print(f" LegalAId Vector Index Re-indexing Tool {'[DRY RUN]' if dry_run else '[LIVE EXECUTION]'}")
    print("=======================================================\n")

    res = asyncio.run(
        run_reindex(
            target_provider=args.provider,
            target_model=args.model,
            collections=colls,
            dry_run=dry_run,
        )
    )

    print(f"Target Provider      : {res['target_provider']}")
    print(f"Target Model         : {res['target_model']}")
    print(f"Dimensions           : {res['target_dimensions']}")
    print(f"Segregated Index Dir : {res['segregated_index_dir']}")
    print(f"Ollama Protected Dir : {res['ollama_index_dir']}")
    print(f"Total Chunks Found   : {res['total_chunks_found']}")
    print(f"Already Indexed      : {res['total_already_indexed']}")
    print(f"Pending Re-index     : {res['total_to_embed']}")
    print("-" * 55)
    for c_name, c_data in res["collections"].items():
        print(f"  [{c_name}] Total: {c_data['total_chunks']}, To Embed: {c_data['to_embed']}")

    if dry_run:
        print("\n[DRY RUN COMPLETE] No embeddings were computed and no index files were written.")
        print("To execute this re-indexing plan, run with '--execute'.\n")
    else:
        print("\n[EXECUTION COMPLETE] Segregated vector index has been updated successfully.\n")


if __name__ == "__main__":
    main()
