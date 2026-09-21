"""Pending clickable options are tracked PER SESSION.

One `SessionLoop` serves every chat a server has open. When "which readings are
still unresolved?" was a single value on the loop, chat A's turn could supersede
(or resolve) chat B's options. These tests pin the isolation, through
`build_session_loop` -- the same assembly the server uses.
"""

import json
import re

import pytest

from versa.llm import ModelTierClients, StubLLMClient
from versa.models import OptionStatus
from versa.session_builder import build_session_loop

_TWO_BRANCHES = json.dumps(
    {
        "needs_branches": True,
        "branches": [
            {"statement": "wants the power rule explained"},
            {"statement": "wants a worked numeric example"},
        ],
    }
)
_FACT = json.dumps({"situation": "ambiguous ask", "resolution": "resolved by click"})


def _options_for_branches(prompt: str) -> str:
    ids = re.findall(r"id=([0-9a-f-]{36})", prompt)
    return json.dumps(
        {
            "kind": "subject",
            "axis": None,
            "options": [{"branch_id": bid, "text": f"option {i}"} for i, bid in enumerate(ids)],
        }
    )


def _loop(pool, embedding_client):
    llm = StubLLMClient(
        canned={
            "ASSESS:BRANCH": _TWO_BRANCHES,
            "DISAMBIGUATE:OPTIONS": _options_for_branches,
            "FINAL:ANSWER": "here is the answer",
            "WRITE:FACT": _FACT,
        }
    )
    return build_session_loop(
        pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_a_click_in_one_session_leaves_another_sessions_options_open(
    transcript, clean_pool, learner_id, embedding_client,
):
    loop = _loop(clean_pool, embedding_client)
    a = await transcript.create_session(learner_id)
    b = await transcript.create_session(learner_id)

    await loop.handle_turn(a, 0, "can you help me with derivatives?")
    await loop.handle_turn(b, 0, "can you help me with limits?")
    opts_a = await loop.pending_options(a)
    opts_b = await loop.pending_options(b)
    assert len(opts_a) == 2 and len(opts_b) == 2
    assert {o.id for o in opts_a}.isdisjoint({o.id for o in opts_b})
    assert all(o.status is OptionStatus.OPEN for o in opts_a + opts_b)

    await loop.handle_turn(a, 1, opts_a[0].text, selected_option_id=opts_a[0].id)

    assert await loop.pending_options(a) == [], "A's options are resolved"
    still_b = await loop.pending_options(b)
    assert {o.id for o in still_b} == {o.id for o in opts_b}, "B's options must be untouched"


@pytest.mark.asyncio(loop_scope="session")
async def test_typing_past_options_supersedes_only_that_sessions_options(
    transcript, clean_pool, learner_id, embedding_client, disambiguation_store,
):
    loop = _loop(clean_pool, embedding_client)
    a = await transcript.create_session(learner_id)
    b = await transcript.create_session(learner_id)
    await loop.handle_turn(a, 0, "can you help me with derivatives?")
    await loop.handle_turn(b, 0, "can you help me with limits?")
    opts_a = await loop.pending_options(a)
    opts_b = await loop.pending_options(b)

    # A's student ignores the buttons and types something else.
    await loop.handle_turn(a, 1, "actually, never mind: what is 2 + 2?")

    stored_a = [await disambiguation_store.get_option(o.id) for o in opts_a]
    assert all(o.status is OptionStatus.SUPERSEDED for o in stored_a)
    stored_b = [await disambiguation_store.get_option(o.id) for o in opts_b]
    assert all(o.status is OptionStatus.OPEN for o in stored_b), "B must be untouched"
    assert len(await loop.pending_options(b)) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_a_session_with_no_pending_options_reports_none(
    transcript, clean_pool, learner_id, embedding_client,
):
    loop = _loop(clean_pool, embedding_client)
    a = await transcript.create_session(learner_id)
    assert await loop.pending_options(a) == []
