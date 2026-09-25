"""The sandbox-chat claim-update flow: an explicit stated preference
(reusing ClassifyStatedPreference, no new foreground call) matches
against existing claims in the background and either revises one
(append-only review, source='sandbox_chat') or creates one through
claims.py's normal path -- pushed to the client as a `claim_update`
websocket event, and replayable via ReviewStore's own read side."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

from tests.test_server import _ANSWER, _FACT, _NOT_AMBIGUOUS, _start, _stop, _turn
from versa.llm import StubLLMClient

_STATED_PREFERENCE = json.dumps({
    "has_preference": True, "stated_preference": "please keep answers short",
    "label": "prefers_brevity",
})
_NO_PREFERENCE = json.dumps({"has_preference": False})


async def _one_turn(clean_pool, embedding_client, canned, text, label="claim-update"):
    llm = StubLLMClient(canned=canned)
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            learner = (await client.post("/api/learners", json={"label": label})).json()
            sid = (await client.post(
                "/api/sessions", json={"learner_id": learner["id"]}
            )).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            events = await _turn(ws, {"type": "message", "text": text})
            await live.loop.wait_for_background_tasks()
            extra = None
            try:
                extra = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            except asyncio.TimeoutError:
                pass
        return live, learner["id"], sid, events, extra
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_neutral_message_never_produces_a_claim_update(clean_pool, embedding_client):
    _, _, _, events, extra = await _one_turn(
        clean_pool, embedding_client,
        {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
         "CLASSIFY:STATED_PREFERENCE": _NO_PREFERENCE},
        "what is a derivative?",
    )
    assert events[-1]["type"] == "done"
    assert extra is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_explicit_preference_with_no_existing_claim_creates_one(
    clean_pool, embedding_client, claim_store,
):
    _, learner_id, sid, events, extra = await _one_turn(
        clean_pool, embedding_client,
        {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
         "CLASSIFY:STATED_PREFERENCE": _STATED_PREFERENCE},
        "please keep answers short from now on",
        label="claim-update-new",
    )
    assert events[-1]["type"] == "done"
    assert extra is not None and extra["type"] == "claim_update"
    assert extra["action"] == "created"
    assert extra["review_id"] is not None

    claims = await claim_store.list_for_learner(learner_id)
    assert len(claims) == 1
    assert str(claims[0].id) == extra["claim_id"]
    assert claims[0].source.value == "stated"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_explicit_preference_that_matches_an_existing_claim_revises_it_via_review(
    clean_pool, embedding_client, claim_store, review_store, transcript, learner_id,
):
    from versa.models import Claim, ClaimSource, ClaimWritePolicy, StatedPreferenceLabel

    existing = await claim_store.create(Claim(
        learner_id=learner_id, statement="prefers long, thorough answers",
        test="offer terse vs full; picks full",
        value=StatedPreferenceLabel.PREFERS_BREVITY, confidence=0.5,
        source=ClaimSource.INFERRED, write_policy=ClaimWritePolicy.SLOW_DRIFT,
        statement_embedding=[0.0] * 768,
    ))

    match_raw = json.dumps({"matched_index": 0, "action": "archive", "new_statement": None})

    canned = {
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT,
        "CLASSIFY:STATED_PREFERENCE": _STATED_PREFERENCE,
        "MATCH:STATED_PREFERENCE": match_raw,
    }
    llm = StubLLMClient(canned=canned)
    live = await _start(clean_pool, llm, embedding_client)
    try:
        sid = await transcript.create_session(learner_id)
        async with websockets.connect(f"{live.ws}/api/sessions/{sid}/chat") as ws:
            events = await _turn(ws, {"type": "message", "text": "actually stop giving me long answers"})
            await live.loop.wait_for_background_tasks()
            extra = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert events[-1]["type"] == "done"
        assert extra["type"] == "claim_update"
        assert extra["action"] == "archive"
        assert extra["claim_id"] == str(existing.id)

        reviews = await review_store.list_for_claim(existing.id)
        assert len(reviews) == 1
        assert reviews[0].review_type == "archive"
        assert reviews[0].source == "sandbox_chat"
        assert reviews[0].source_session_id == sid
    finally:
        await _stop(live)
