"""sessions.app_mode (migration 055) and TranscriptStore.list_session_summaries
-- the chat-history sidebar's own read path. A separate concern from
list_sessions_for_learner (test_audit_topic_and_sessions.py): that one is
mode-agnostic and feeds the CLI's resume view; this one is per-mode, ordered
by activity not creation, and carries a preview -- see ChatSummary's own
docstring for why they aren't merged.
"""

from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.asyncio(loop_scope="session")
async def test_a_session_defaults_to_sandbox(transcript, clean_pool, learner_id):
    session_id = await transcript.create_session(learner_id)
    summaries = await transcript.list_session_summaries(learner_id, "sandbox")
    assert session_id in {s.session_id for s in summaries}


@pytest.mark.asyncio(loop_scope="session")
async def test_list_session_summaries_only_returns_the_requested_mode(
    transcript, clean_pool, learner_id
):
    sandbox_session = await transcript.create_session(learner_id, app_mode="sandbox")
    learn_session = await transcript.create_session(learner_id, app_mode="learn")

    sandbox_summaries = await transcript.list_session_summaries(learner_id, "sandbox")
    learn_summaries = await transcript.list_session_summaries(learner_id, "learn")

    assert {s.session_id for s in sandbox_summaries} == {sandbox_session}
    assert {s.session_id for s in learn_summaries} == {learn_session}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_turnless_chat_appears_with_no_preview_and_zero_turns(
    transcript, clean_pool, learner_id
):
    session_id = await transcript.create_session(learner_id)
    (summary,) = await transcript.list_session_summaries(learner_id, "sandbox")
    assert summary.session_id == session_id
    assert summary.turn_count == 0
    assert summary.preview is None


@pytest.mark.asyncio(loop_scope="session")
async def test_preview_is_the_first_turns_text_not_the_last(
    transcript, clean_pool, learner_id
):
    session_id = await transcript.create_session(learner_id)
    await transcript.record_turn(session_id, 0, "what is a derivative?")
    await transcript.record_turn(session_id, 1, "and an integral?")

    (summary,) = await transcript.list_session_summaries(learner_id, "sandbox")
    assert summary.turn_count == 2
    assert summary.preview == "what is a derivative?"


@pytest.mark.asyncio(loop_scope="session")
async def test_ordered_by_last_activity_not_creation(transcript, clean_pool, learner_id):
    """A chat created earlier but used more recently sorts first -- the
    sidebar tracks use, not creation order."""
    older_created = await transcript.create_session(learner_id)
    newer_created = await transcript.create_session(learner_id)
    # newer_created has no turns; older_created gets one just now, so its
    # last_activity_at is newer than newer_created's (turn-less) created_at.
    await transcript.record_turn(older_created, 0, "revived after a while")

    summaries = await transcript.list_session_summaries(learner_id, "sandbox")
    assert [s.session_id for s in summaries] == [older_created, newer_created]


@pytest.mark.asyncio(loop_scope="session")
async def test_does_not_include_another_learners_chats(
    transcript, clean_pool, learner_store
):
    a = await learner_store.create(label="app-mode-a")
    b = await learner_store.create(label="app-mode-b")
    session_a = await transcript.create_session(a.id)
    await transcript.create_session(b.id)

    summaries = await transcript.list_session_summaries(a.id, "sandbox")
    assert {s.session_id for s in summaries} == {session_a}


@pytest.mark.asyncio(loop_scope="session")
async def test_turn_count_and_created_at_round_trip(transcript, clean_pool, learner_id):
    before = datetime.now(UTC) - timedelta(seconds=1)
    session_id = await transcript.create_session(learner_id)
    await transcript.record_turn(session_id, 0, "hello")

    (summary,) = [
        s for s in await transcript.list_session_summaries(learner_id, "sandbox")
        if s.session_id == session_id
    ]
    assert summary.turn_count == 1
    assert summary.app_mode == "sandbox"
    assert summary.created_at >= before
    assert summary.last_activity_at >= summary.created_at
