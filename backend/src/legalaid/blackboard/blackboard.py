"""
legalaid/blackboard/blackboard.py
===================================
Phase 3: Blackboard Architecture — Shared State Space
-------------------------------------------------------
Implements the Blackboard pattern for multi-agent state sharing in LegalAId.

The Blackboard is divided into semantic "planes" — each plane corresponds to a
distinct reasoning stage in the legal research pipeline:

  INTAKE              → Raw user matter (question, documents, context)
  MATTER_FACTS        → Structured facts extracted by the Intake Router
  STATUTORY_AUTHORITY → Statutory provisions found by the ADK Legal Researcher
  JUDICIAL_PRECEDENT  → Case law found by the ADK Similar Cases agent
  VERIFICATION_AUDIT  → Fact-check results from the Verification agent
  DRAFT_DOCUMENTS     → Legal memos / briefs produced by the Drafting agent
  SYNTHESIS           → Final combined answer by the CrewAI Synthesis team

Concurrency Model:
  - A single asyncio.Lock serializes all writes to prevent partial-state reads.
  - Reads (get_latest_entry, get_plane) acquire the lock in shared read mode
    by acquiring and immediately releasing (Python asyncio.Lock is not RWLOCK,
    so we use a lightweight approach: reads hold the lock briefly).
  - Listener callbacks are fired OUTSIDE the lock via asyncio.create_task()
    to prevent slow listeners from blocking the writer.

Usage:
  from legalaid.blackboard.blackboard import LegalBlackboard, BlackboardPlane

  board = LegalBlackboard()

  # Writer (ADK agent)
  await board.post_entry(
      plane=BlackboardPlane.STATUTORY_AUTHORITY,
      author_agent="adk_legal_researcher",
      author_framework=AgentFramework.GOOGLE_ADK,
      data={"statutes": [...]},
      allowed_chunk_ids=["chk_abc", "chk_def"],
  )

  # Reader (CrewAI agent)
  entry = await board.get_latest_entry(BlackboardPlane.STATUTORY_AUTHORITY)
  manifest = await board.get_verified_grounding_manifest()
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Coroutine
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict

from legalaid.a2a.bridge import AgentFramework

logger = logging.getLogger("legalaid.blackboard")


# ===========================================================================
# Enumerations
# ===========================================================================

class BlackboardPlane(StrEnum):
    """
    Semantic planes of the LegalAId Blackboard.
    Each plane is a named partition of the shared state space.
    Agents should only write to planes within their designated responsibility.
    """
    INTAKE              = "INTAKE"
    MATTER_FACTS        = "MATTER_FACTS"
    STATUTORY_AUTHORITY = "STATUTORY_AUTHORITY"
    JUDICIAL_PRECEDENT  = "JUDICIAL_PRECEDENT"
    VERIFICATION_AUDIT  = "VERIFICATION_AUDIT"
    DRAFT_DOCUMENTS     = "DRAFT_DOCUMENTS"
    SYNTHESIS           = "SYNTHESIS"


# ===========================================================================
# Pydantic Models
# ===========================================================================

class BlackboardEntry(BaseModel):
    """
    A single data entry posted to a Blackboard plane.

    Revision tracking:
      When an agent updates an existing entry (e.g., the critic revises findings),
      a new BlackboardEntry is created with revision = prior_revision + 1.
      The full revision history is preserved in LegalBlackboard._planes.

    Verification:
      is_verified is set to True by the Verification agent after fact-checking.
      The GroundingManifest only includes chunk_ids from verified entries.
    """
    model_config = ConfigDict(frozen=False)  # Mutable: critic must be able to mark verified

    entry_id:         str            = Field(default_factory=lambda: f"bb_{uuid4().hex[:12]}")
    plane:            BlackboardPlane
    author_agent:     str            = Field(..., description="Name of the writing agent")
    author_framework: AgentFramework = Field(..., description="Framework of the writing agent")
    revision:         int            = Field(0, ge=0, description="0 = initial write, +1 per update")
    created_at:       datetime       = Field(default_factory=lambda: datetime.now(timezone.utc))
    data:             dict[str, Any] = Field(default_factory=dict, description="Arbitrary plane-specific payload")
    allowed_chunk_ids: list[str]     = Field(
        default_factory=list,
        description="chunk_ids from the retrieval engine that support this entry's claims",
    )
    is_verified:      bool           = Field(False, description="True once the Verification agent has checked this entry")
    audit_notes:      list[str]      = Field(default_factory=list, description="Verification or critic notes")


class GroundingManifest(BaseModel):
    """
    The single source of truth for what chunk_ids CrewAI synthesis agents
    are authorised to cite in their final output.

    Generated by LegalBlackboard.get_verified_grounding_manifest() by aggregating
    all is_verified=True entries from STATUTORY_AUTHORITY and JUDICIAL_PRECEDENT planes.

    The CrewAI task validation callback uses this manifest to strip hallucinated citations.
    """
    model_config = ConfigDict(frozen=True)

    allowed_chunk_ids:  list[str]        = Field(default_factory=list)
    verified_sections:  list[str]        = Field(default_factory=list, description="Statutory section references")
    verified_cases:     list[str]        = Field(default_factory=list, description="Case citation strings")
    source_entry_count: int              = Field(0, description="Number of verified BlackboardEntries contributing to this manifest")
    generated_at:       datetime         = Field(default_factory=lambda: datetime.now(timezone.utc))


# Type alias for listener callbacks
ListenerCallback = Callable[[BlackboardEntry], Coroutine[Any, Any, None]]


# ===========================================================================
# Blackboard Implementation
# ===========================================================================

class LegalBlackboard:
    """
    Thread-safe Blackboard state manager for the LegalAId multi-agent system.

    Internal storage:
      _planes: dict[BlackboardPlane, list[BlackboardEntry]]
        Each plane stores the full revision history. The last element is always
        the most recent entry (get_latest_entry returns _planes[plane][-1]).

      _listeners: dict[BlackboardPlane, list[ListenerCallback]]
        Async callbacks invoked after every write to a plane.
        Useful for agents that need to react to state changes without polling.

      _lock: asyncio.Lock
        Serializes all writes. Reads also briefly acquire the lock to guarantee
        a consistent snapshot of the plane list.
    """

    def __init__(self) -> None:
        self._planes:    dict[BlackboardPlane, list[BlackboardEntry]] = {
            plane: [] for plane in BlackboardPlane
        }
        self._listeners: dict[BlackboardPlane, list[ListenerCallback]] = {
            plane: [] for plane in BlackboardPlane
        }
        self._lock: asyncio.Lock = asyncio.Lock()
        logger.info("LegalBlackboard initialized with %d planes.", len(BlackboardPlane))

    # -----------------------------------------------------------------------
    # Listener Registration
    # -----------------------------------------------------------------------

    async def register_listener(
        self,
        plane: BlackboardPlane,
        callback: ListenerCallback,
    ) -> None:
        """
        Register an async callback to be invoked whenever a new entry is posted
        to the specified plane.

        Args:
            plane:    The Blackboard plane to watch.
            callback: An async function accepting a BlackboardEntry.

        Note:
            Callbacks are fired via asyncio.create_task() OUTSIDE the write lock,
            so slow callbacks will NOT block the writing agent.
            Callback exceptions are logged and swallowed — they must not propagate
            back to the writer.
        """
        async with self._lock:
            self._listeners[plane].append(callback)
        logger.debug("Listener registered on plane '%s'.", plane)

    # -----------------------------------------------------------------------
    # Write
    # -----------------------------------------------------------------------

    async def post_entry(
        self,
        plane: BlackboardPlane,
        author_agent: str,
        author_framework: AgentFramework,
        data: dict[str, Any],
        allowed_chunk_ids: list[str] | None = None,
        audit_notes: list[str] | None = None,
    ) -> BlackboardEntry:
        """
        Atomically write a new entry to the specified plane.
        If a prior entry exists, the revision number is automatically incremented.

        Args:
            plane:             Target Blackboard plane.
            author_agent:      Name of the agent posting this entry.
            author_framework:  AgentFramework of the posting agent.
            data:              Arbitrary dict payload (agent-specific schema).
            allowed_chunk_ids: chunk_ids from retrieval that support this data.
            audit_notes:       Optional provenance or verification notes.

        Returns:
            The newly created BlackboardEntry (also stored internally).
        """
        async with self._lock:
            history = self._planes[plane]
            prior_revision = history[-1].revision if history else -1
            entry = BlackboardEntry(
                plane=plane,
                author_agent=author_agent,
                author_framework=author_framework,
                revision=prior_revision + 1,
                data=data,
                allowed_chunk_ids=allowed_chunk_ids or [],
                audit_notes=audit_notes or [],
            )
            self._planes[plane].append(entry)
            # Snapshot listeners list inside the lock (to avoid concurrent mutation)
            listeners = list(self._listeners[plane])

        logger.info(
            "Blackboard write: plane='%s' agent='%s' revision=%d chunk_ids=%d",
            plane, author_agent, entry.revision, len(entry.allowed_chunk_ids),
        )

        # Fire listener callbacks OUTSIDE the lock — prevents deadlocks if a
        # callback itself tries to post_entry to the same plane.
        for cb in listeners:
            asyncio.create_task(self._safe_fire_listener(cb, entry))

        return entry

    async def _safe_fire_listener(
        self, callback: ListenerCallback, entry: BlackboardEntry
    ) -> None:
        """Wraps listener callback execution with error isolation."""
        try:
            await callback(entry)
        except Exception as exc:
            logger.error(
                "Blackboard listener error on plane '%s': %s",
                entry.plane, exc, exc_info=True,
            )

    # -----------------------------------------------------------------------
    # Verification
    # -----------------------------------------------------------------------

    async def mark_verified(
        self,
        entry_id: str,
        audit_notes: list[str] | None = None,
    ) -> BlackboardEntry | None:
        """
        Mark a specific BlackboardEntry as verified (is_verified=True).
        Called by the Verification agent after fact-checking an entry.

        Args:
            entry_id:    The entry_id of the entry to mark verified.
            audit_notes: Optional notes from the verification process.

        Returns:
            The updated entry, or None if the entry_id was not found.
        """
        async with self._lock:
            for entries in self._planes.values():
                for entry in entries:
                    if entry.entry_id == entry_id:
                        entry.is_verified = True
                        if audit_notes:
                            entry.audit_notes.extend(audit_notes)
                        logger.info("Entry marked verified: '%s'", entry_id)
                        return entry
        logger.warning("mark_verified: entry_id '%s' not found.", entry_id)
        return None

    # -----------------------------------------------------------------------
    # Read
    # -----------------------------------------------------------------------

    async def get_plane(self, plane: BlackboardPlane) -> list[BlackboardEntry]:
        """
        Returns the full revision history for a plane (oldest → newest).
        Returns an empty list if no entries have been posted.
        """
        async with self._lock:
            return list(self._planes[plane])  # Return a copy to prevent external mutation

    async def get_latest_entry(
        self, plane: BlackboardPlane
    ) -> BlackboardEntry | None:
        """
        Returns the most recent entry on a plane, or None if empty.
        This is the primary read method for inter-agent data consumption.
        """
        async with self._lock:
            history = self._planes[plane]
            return history[-1] if history else None

    async def get_entry(self, entry_id: str) -> BlackboardEntry | None:
        """
        Lookup a specific entry by its entry_id across all planes.
        Used by the Verification agent to locate entries to check.
        """
        async with self._lock:
            for entries in self._planes.values():
                for entry in entries:
                    if entry.entry_id == entry_id:
                        return entry
        return None

    # -----------------------------------------------------------------------
    # Grounding Manifest (Critical Integration Point)
    # -----------------------------------------------------------------------

    async def get_verified_grounding_manifest(self) -> GroundingManifest:
        """
        Aggregates ALL verified BlackboardEntries from STATUTORY_AUTHORITY and
        JUDICIAL_PRECEDENT planes into a GroundingManifest.

        The manifest is the ONLY authoritative list of chunk_ids that the
        CrewAI Synthesis agents are permitted to cite in their final output.
        The task validation callback uses this to strip hallucinated references.

        Returns:
            A frozen GroundingManifest with deduplicated allowed_chunk_ids.

        Design note:
            We intentionally include only is_verified=True entries. Unverified
            entries (e.g., newly posted by the researcher but not yet fact-checked)
            are withheld from the synthesis team to prevent unchecked hallucinations
            from reaching the final legal document.
        """
        async with self._lock:
            statutory_entries = self._planes[BlackboardPlane.STATUTORY_AUTHORITY]
            precedent_entries = self._planes[BlackboardPlane.JUDICIAL_PRECEDENT]

        all_chunk_ids:      set[str] = set()
        verified_sections:  list[str] = []
        verified_cases:     list[str] = []
        entry_count:        int = 0

        # Process statutory entries
        for entry in statutory_entries:
            if entry.is_verified:
                all_chunk_ids.update(entry.allowed_chunk_ids)
                # Extract section references if stored in the data payload
                sections = entry.data.get("statutory_sections", [])
                verified_sections.extend(sections if isinstance(sections, list) else [])
                entry_count += 1

        # Process precedent entries
        for entry in precedent_entries:
            if entry.is_verified:
                all_chunk_ids.update(entry.allowed_chunk_ids)
                # Extract case citations if stored in the data payload
                cases = entry.data.get("case_citations", [])
                verified_cases.extend(cases if isinstance(cases, list) else [])
                entry_count += 1

        manifest = GroundingManifest(
            allowed_chunk_ids=sorted(all_chunk_ids),
            verified_sections=list(dict.fromkeys(verified_sections)),  # Deduplicate, preserve order
            verified_cases=list(dict.fromkeys(verified_cases)),
            source_entry_count=entry_count,
        )

        logger.info(
            "GroundingManifest generated: %d chunk_ids from %d verified entries.",
            len(manifest.allowed_chunk_ids), entry_count,
        )
        return manifest

    # -----------------------------------------------------------------------
    # Export / Snapshot
    # -----------------------------------------------------------------------

    async def export_snapshot(self) -> dict[str, Any]:
        """
        Export the current state of ALL planes as a JSON-serializable dict.
        Used by the ObservabilityBridge to persist a full Blackboard snapshot
        to the SQLite telemetry DB at the end of a research session.
        """
        async with self._lock:
            return {
                plane.value: [
                    entry.model_dump(mode="json") for entry in entries
                ]
                for plane, entries in self._planes.items()
            }

    async def export_history(self) -> list[dict[str, Any]]:
        """
        Export ALL entries across all planes in chronological order.
        Useful for post-hoc analysis and debugging multi-agent sessions.
        """
        async with self._lock:
            all_entries: list[BlackboardEntry] = []
            for entries in self._planes.values():
                all_entries.extend(entries)

        # Sort by created_at across planes
        all_entries.sort(key=lambda e: e.created_at)
        return [e.model_dump(mode="json") for e in all_entries]

