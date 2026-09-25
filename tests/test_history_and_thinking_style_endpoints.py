"""Session-end consolidation (/end + the new-session sweep), the History
page's all-modes listing, and the Thinking-style page's endpoints: the
overview, per-item detail/review/undo, "why", and ask-about-it threads."""

from __future__ import annotations

import httpx
import pytest

from tests.test_server import live, new_chat  # noqa: F401  (fixtures)
from versa.models import (
    ApproachAxis,
    Claim,
    ClaimEvidence,
    ClaimSource,
    ClaimWritePolicy,
    EvidenceDirection,
    StatedPreferenceLabel,
)

MIN_TURNS = 6  # MemoryConfig.min_turns_for_cli_auto_consolidation's default


async def _insert_turns(transcript, session_id, n):
    for i in range(n):
        await transcript.record_turn(session_id, i, f"turn {i}")


# --------------------------------------------------------------- consolidation


@pytest.mark.asyncio(loop_scope="session")
async def test_end_reports_too_short_and_never_claims_the_session(live, new_chat, transcript):
    _, sid = await new_chat()
    await _insert_turns(transcript, sid, MIN_TURNS - 1)
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.post(f"/api/sessions/{sid}/end")
    assert r.json() == {"status": "too_short"}
    # not claimed: a later long-enough sweep could still pick it up
    assert await transcript.claim_for_consolidation(sid) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_end_schedules_once_then_reports_already_consolidated(live, new_chat, transcript):
    _, sid = await new_chat()
    await _insert_turns(transcript, sid, MIN_TURNS)
    async with httpx.AsyncClient(base_url=live.http) as client:
        first = await client.post(f"/api/sessions/{sid}/end")
        await live.loop.wait_for_background_tasks()
        second = await client.post(f"/api/sessions/{sid}/end")
    assert first.json() == {"status": "scheduled"}
    assert second.json() == {"status": "already_consolidated"}


@pytest.mark.asyncio(loop_scope="session")
async def test_end_for_an_unknown_session_is_404(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.post("/api/sessions/00000000-0000-0000-0000-000000000000/end")
    assert r.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_new_session_sweeps_an_old_unconsolidated_eligible_session(
    live, new_chat, transcript
):
    lid, old_sid = await new_chat("sweep-target")
    await _insert_turns(transcript, old_sid, MIN_TURNS)
    async with httpx.AsyncClient(base_url=live.http) as client:
        await client.post("/api/sessions", json={"learner_id": lid})
        await live.loop.wait_for_background_tasks()
    # already claimed by the sweep -- a direct claim now returns False
    assert await transcript.claim_for_consolidation(old_sid) is False


# --------------------------------------------------------------------- history


@pytest.mark.asyncio(loop_scope="session")
async def test_history_lists_every_mode_newest_first(live, new_chat):
    lid, first_sid = await new_chat("history-user")
    async with httpx.AsyncClient(base_url=live.http) as client:
        second = (await client.post("/api/sessions", json={"learner_id": lid})).json()
        rows = (await client.get(f"/api/learners/{lid}/sessions/all")).json()
    assert [r["session_id"] for r in rows] == [second["session_id"], first_sid]


@pytest.mark.asyncio(loop_scope="session")
async def test_history_for_an_unknown_learner_is_404(live):
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.get(
            "/api/learners/00000000-0000-0000-0000-000000000000/sessions/all"
        )
    assert r.status_code == 404


# --------------------------------------------------------- thinking-style: setup


def _claim(learner_id):
    return Claim(
        learner_id=learner_id, statement="prefers brevity",
        test="offer terse vs full; picks terse",
        value=StatedPreferenceLabel.PREFERS_BREVITY, confidence=0.6,
        source=ClaimSource.INFERRED, write_policy=ClaimWritePolicy.SLOW_DRIFT,
        statement_embedding=[0.0] * 768,
    )


async def _seed_claim_with_evidence(claim_store, learner_id, session_id, interaction_recorder, n=2):
    from versa.models import QuestionAuthor

    claim = await claim_store.create(_claim(learner_id))
    for i in range(n):
        interaction = await interaction_recorder.record(
            learner_id=learner_id, session_id=session_id, turn_number=i,
            question_text="q", question_author=QuestionAuthor.LEARNER,
            originating_question=None, did_branch=False, response_text="a",
        )
        await claim_store.append_evidence(ClaimEvidence(
            claim_id=claim.id, learner_id=learner_id, interaction_id=interaction.id,
            direction=EvidenceDirection.SUPPORTS, topic="derivatives",
            axis=ApproachAxis.BREVITY_DEPTH, session_id=session_id,
            test_fired=True, contradiction_was_possible=True,
        ))
    return claim


# ------------------------------------------------------------- thinking-style


@pytest.mark.asyncio(loop_scope="session")
async def test_overview_shows_every_status_and_hides_archived_by_default(
    live, new_chat, transcript, thinking_style_store, claim_store, interaction_recorder,
):
    lid, sid = await new_chat("ts-overview")
    await _insert_turns(transcript, sid, 1)
    confirmed = await thinking_style_store.create_candidate(lid, sid, "concrete then abstract", [0.0] * 768)
    for _ in range(4):
        confirmed = await thinking_style_store.confirm(confirmed.id, __import__("uuid").uuid4(), promotion_threshold=5)
    retired_c = await thinking_style_store.create_candidate(lid, sid, "stale pattern", [0.0] * 768)
    await thinking_style_store.retire(retired_c.id)
    claim = await _seed_claim_with_evidence(claim_store, lid, sid, interaction_recorder)

    async with httpx.AsyncClient(base_url=live.http) as client:
        out = (await client.get(f"/api/learners/{lid}/thinking-style")).json()
    assert out["promotion_threshold"] == 5
    assert [c["id"] for c in out["confirmed"]] == [str(confirmed.id)]
    assert [c["id"] for c in out["retired"]] == [str(retired_c.id)]
    assert [c["id"] for c in out["claims"]] == [str(claim.id)]

    async with httpx.AsyncClient(base_url=live.http) as client:
        await client.post(f"/api/claims/{claim.id}/review", json={"action": "archive"})
        default_view = (await client.get(f"/api/learners/{lid}/thinking-style")).json()
        with_archived = (
            await client.get(f"/api/learners/{lid}/thinking-style", params={"include_archived": True})
        ).json()
    assert default_view["claims"] == []
    assert [c["id"] for c in with_archived["claims"]] == [str(claim.id)]
    assert with_archived["claims"][0]["archived"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_claim_view_edit_approve_archive_and_undo(
    live, new_chat, transcript, claim_store, interaction_recorder,
):
    lid, sid = await new_chat("claim-review")
    await _insert_turns(transcript, sid, 1)
    claim = await _seed_claim_with_evidence(claim_store, lid, sid, interaction_recorder)

    async with httpx.AsyncClient(base_url=live.http) as client:
        detail = (await client.get(f"/api/claims/{claim.id}")).json()
        assert detail["statement"] == claim.statement
        assert len(detail["evidence"]) == 2

        edited = (await client.post(
            f"/api/claims/{claim.id}/review", json={"action": "edit", "revised_statement": "prefers terse answers"}
        )).json()
        assert edited["statement"] == "prefers terse answers"
        assert edited["edited"] is True
        edit_review_id = edited["reviews"][-1]["id"]

        approved = (await client.post(f"/api/claims/{claim.id}/review", json={"action": "approve"})).json()
        assert approved["statement"] == "prefers terse answers"  # approve never changes text

        archived = (await client.post(f"/api/claims/{claim.id}/review", json={"action": "archive"})).json()
        assert archived["archived"] is True

        restored = (await client.post(
            f"/api/claims/{claim.id}/reviews/{archived['reviews'][-1]['id']}/undo"
        )).json()
        assert restored["archived"] is False

        undone_edit = (await client.post(f"/api/claims/{claim.id}/reviews/{edit_review_id}/undo")).json()
        assert undone_edit["statement"] == claim.statement  # back to the original

        approve_review_id = approved["reviews"][1]["id"] if approved["reviews"][1]["review_type"] == "approve" else approved["reviews"][-1]["id"]
        no_undo = await client.post(f"/api/claims/{claim.id}/reviews/{approve_review_id}/undo")
        assert no_undo.status_code == 400


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_requires_revised_statement(live, new_chat, transcript, claim_store, interaction_recorder):
    lid, sid = await new_chat("edit-validation")
    await _insert_turns(transcript, sid, 1)
    claim = await _seed_claim_with_evidence(claim_store, lid, sid, interaction_recorder)
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await client.post(f"/api/claims/{claim.id}/review", json={"action": "edit"})
    assert r.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_why_is_cached_after_the_first_call(live, new_chat, transcript, claim_store, interaction_recorder):
    lid, sid = await new_chat("why-claim")
    await _insert_turns(transcript, sid, 1)
    claim = await _seed_claim_with_evidence(claim_store, lid, sid, interaction_recorder)
    async with httpx.AsyncClient(base_url=live.http) as client:
        first = (await client.get(f"/api/claims/{claim.id}/why")).json()
        second = (await client.get(f"/api/claims/{claim.id}/why")).json()
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["explanation"] == second["explanation"]


@pytest.mark.asyncio(loop_scope="session")
async def test_why_for_a_thinking_style_candidate(live, new_chat, transcript, thinking_style_store):
    lid, sid = await new_chat("why-style")
    await _insert_turns(transcript, sid, 1)
    candidate = await thinking_style_store.create_candidate(lid, sid, "concrete then abstract", [0.0] * 768)
    async with httpx.AsyncClient(base_url=live.http) as client:
        out = (await client.get(f"/api/thinking-style/candidates/{candidate.id}/why")).json()
    assert out["explanation"]
    assert out["cached"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_ask_a_plain_question_never_changes_anything(
    live, new_chat, transcript, claim_store, interaction_recorder,
):
    lid, sid = await new_chat("ask-plain")
    await _insert_turns(transcript, sid, 1)
    claim = await _seed_claim_with_evidence(claim_store, lid, sid, interaction_recorder)
    async with httpx.AsyncClient(base_url=live.http) as client:
        answer = (await client.post(
            f"/api/claims/{claim.id}/qna", json={"question": "why do you think this?"}
        )).json()
        detail = (await client.get(f"/api/claims/{claim.id}")).json()
        thread = (await client.get(f"/api/claims/{claim.id}/qna")).json()
    assert answer["intent"] == "none"
    assert answer["applied_review_id"] is None
    assert detail["edited"] is False
    assert [t["id"] for t in thread] == [answer["id"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_ask_an_explicit_correction_applies_it_and_can_be_undone(
    live, new_chat, transcript, claim_store, interaction_recorder,
):
    """The stub client only ever returns intent=none (its conservative
    default), so this drives the applying LOGIC directly the same way
    server.py's endpoint does, to prove the endpoint wiring (not just
    apply_ask_intent in isolation, already covered in
    test_reviews_nodes.py) actually persists a chat-sourced edit and that
    the generic undo endpoint reverses it."""
    lid, sid = await new_chat("ask-correction")
    await _insert_turns(transcript, sid, 1)
    claim = await _seed_claim_with_evidence(claim_store, lid, sid, interaction_recorder)

    from versa import server as server_module

    async def fake_call_node(self, node, session_id, turn_index, /, **kwargs):
        from versa.reviews import AskAnswer
        return AskAnswer(answer="got it, updating", intent="edit", new_statement="prefers theory first")

    original = server_module.SessionLoop._call_node
    server_module.SessionLoop._call_node = fake_call_node
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            answer = (await client.post(
                f"/api/claims/{claim.id}/qna", json={"question": "no, I actually like theory first"}
            )).json()
            detail = (await client.get(f"/api/claims/{claim.id}")).json()
    finally:
        server_module.SessionLoop._call_node = original

    assert answer["intent"] == "edit"
    assert answer["applied_review_id"] is not None
    assert "Updated:" in answer["answer"]
    assert detail["statement"] == "prefers theory first"
    assert detail["edited"] is True

    async with httpx.AsyncClient(base_url=live.http) as client:
        undone = (await client.post(
            f"/api/claims/{claim.id}/reviews/{answer['applied_review_id']}/undo"
        )).json()
    assert undone["statement"] == claim.statement
