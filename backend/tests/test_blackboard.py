"""
backend/tests/test_blackboard.py
=================================
Unit tests for the thread-safe LegalBlackboard (`legalaid.blackboard.blackboard`).
Validates rubric requirement 2 (Blackboard Architecture).
"""

import asyncio
import pytest

from legalaid.a2a.bridge import AgentFramework
from legalaid.blackboard.blackboard import (
    BlackboardEntry,
    BlackboardPlane,
    LegalBlackboard,
)


@pytest.mark.asyncio
async def test_blackboard_atomic_write_and_revision():
    bb = LegalBlackboard()

    # Initial post (revision 0)
    entry_0 = await bb.post_entry(
        plane=BlackboardPlane.STATUTORY_AUTHORITY,
        author_agent="adk_legal_researcher",
        author_framework=AgentFramework.GOOGLE_ADK,
        data={"sections": ["Section 27"]},
        allowed_chunk_ids=["chk_001"],
    )

    assert entry_0.revision == 0
    assert entry_0.plane == BlackboardPlane.STATUTORY_AUTHORITY
    assert entry_0.allowed_chunk_ids == ["chk_001"]
    assert entry_0.is_verified is False

    # Second post to same plane (revision 1)
    entry_1 = await bb.post_entry(
        plane=BlackboardPlane.STATUTORY_AUTHORITY,
        author_agent="adk_legal_researcher",
        author_framework=AgentFramework.GOOGLE_ADK,
        data={"sections": ["Section 27", "Section 28"]},
        allowed_chunk_ids=["chk_001", "chk_002"],
    )

    assert entry_1.revision == 1
    assert len(await bb.get_plane(BlackboardPlane.STATUTORY_AUTHORITY)) == 2

    # Latest entry lookup
    latest = await bb.get_latest_entry(BlackboardPlane.STATUTORY_AUTHORITY)
    assert latest is not None
    assert latest.revision == 1
    assert latest.entry_id == entry_1.entry_id


@pytest.mark.asyncio
async def test_mark_verified_and_grounding_manifest():
    bb = LegalBlackboard()

    # Unverified entry
    entry_unverified = await bb.post_entry(
        plane=BlackboardPlane.STATUTORY_AUTHORITY,
        author_agent="adk_researcher",
        author_framework=AgentFramework.GOOGLE_ADK,
        data={"statutory_sections": ["Section 54"]},
        allowed_chunk_ids=["chk_unverified_1"],
    )

    # Pre-verification manifest must be empty
    manifest_before = await bb.get_verified_grounding_manifest()
    assert len(manifest_before.allowed_chunk_ids) == 0

    # Verification agent audits and marks verified
    updated = await bb.mark_verified(
        entry_unverified.entry_id,
        audit_notes=["Checked against India Code verified dataset."],
    )
    assert updated is not None
    assert updated.is_verified is True
    assert "Checked against India Code verified dataset." in updated.audit_notes

    # Post precedent entry and verify it
    entry_precedent = await bb.post_entry(
        plane=BlackboardPlane.JUDICIAL_PRECEDENT,
        author_agent="adk_similar_cases",
        author_framework=AgentFramework.GOOGLE_ADK,
        data={"case_citations": ["AIR 2023 SC 100"]},
        allowed_chunk_ids=["chk_precedent_1"],
    )
    await bb.mark_verified(entry_precedent.entry_id)

    # Grounding manifest must now aggregate verified chunk_ids
    manifest_after = await bb.get_verified_grounding_manifest()
    assert "chk_unverified_1" in manifest_after.allowed_chunk_ids
    assert "chk_precedent_1" in manifest_after.allowed_chunk_ids
    assert manifest_after.source_entry_count == 2
    assert "Section 54" in manifest_after.verified_sections
    assert "AIR 2023 SC 100" in manifest_after.verified_cases


@pytest.mark.asyncio
async def test_listener_subscription_notification():
    bb = LegalBlackboard()
    notified_events: list[BlackboardEntry] = []

    async def _on_synthesis_post(entry: BlackboardEntry) -> None:
        notified_events.append(entry)

    await bb.register_listener(BlackboardPlane.SYNTHESIS, _on_synthesis_post)

    # Post to an unrelated plane: should NOT notify
    await bb.post_entry(
        plane=BlackboardPlane.INTAKE,
        author_agent="intake_agent",
        author_framework=AgentFramework.SYSTEM,
        data={"query": "test query"},
    )
    await asyncio.sleep(0.05)
    assert len(notified_events) == 0

    # Post to SYNTHESIS plane: should notify listener
    entry_synth = await bb.post_entry(
        plane=BlackboardPlane.SYNTHESIS,
        author_agent="crewai_synthesis_team",
        author_framework=AgentFramework.CREWAI,
        data={"summary": "contract void under sec 27"},
    )
    await asyncio.sleep(0.05)
    assert len(notified_events) == 1
    assert notified_events[0].entry_id == entry_synth.entry_id


@pytest.mark.asyncio
async def test_blackboard_export_snapshot():
    bb = LegalBlackboard()
    await bb.post_entry(
        plane=BlackboardPlane.INTAKE,
        author_agent="user_proxy",
        author_framework=AgentFramework.SYSTEM,
        data={"facts": "facts text"},
    )

    snapshot = await bb.export_snapshot()
    assert BlackboardPlane.INTAKE.value in snapshot
    assert len(snapshot[BlackboardPlane.INTAKE.value]) == 1
    assert snapshot[BlackboardPlane.INTAKE.value][0]["data"]["facts"] == "facts text"

