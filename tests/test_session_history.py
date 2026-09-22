"""session_history.reconstruct_session_history / latest_open_options --
replaying a real session's turns exactly as SessionLoop actually wrote them
(via build_session_loop + a StubLLMClient, the same assembly a server uses),
never a hand-built fixture: this is the read side of CLAUDE.md invariant 2,
so it must be proven against the real write path, not an approximation of it.
"""

import json
import re

import pytest

from versa.llm import ModelTierClients, StubLLMClient
from versa.models import OptionStatus
from versa.session_builder import build_session_loop
from versa.session_history import (
    OPTIONS_QUESTION,
    latest_open_options,
    reconstruct_session_history,
)

_NOT_AMBIGUOUS = json.dumps({"needs_branches": False, "branches": []})
_TWO_BRANCHES = json.dumps(
    {
        "needs_branches": True,
        "branches": [
            {"statement": "wants the power rule explained"},
            {"statement": "wants a worked numeric example"},
        ],
    }
)
_FACT = json.dumps({"situation": "asked something", "resolution": "answered it"})


def _options_for_branches(prompt: str) -> str:
    ids = re.findall(r"id=([0-9a-f-]{36})", prompt)
    return json.dumps(
        {
            "kind": "subject",
            "axis": None,
            "options": [{"branch_id": bid, "text": f"reading {i}"} for i, bid in enumerate(ids)],
        }
    )


def _loop(pool, canned, embedding_client):
    llm = StubLLMClient(canned=canned)
    return build_session_loop(pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_direct_answer_turn_reconstructs_as_answer(
    transcript, node_calls, disambiguation_store, clean_pool, learner_id, embedding_client,
):
    loop = _loop(
        clean_pool,
        {"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": "the direct answer", "WRITE:FACT": _FACT},
        embedding_client,
    )
    session_id = await transcript.create_session(learner_id)
    await loop.handle_turn(session_id, 0, "what is a derivative?")

    (turn,) = await reconstruct_session_history(transcript, node_calls, disambiguation_store, session_id)
    assert turn.turn_index == 0
    assert turn.student_text == "what is a derivative?"
    assert turn.kind == "answer"
    assert turn.tutor_text == "the direct answer"
    assert turn.options == []
    assert await latest_open_options(transcript, node_calls, disambiguation_store, session_id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unresolved_options_turn_reconstructs_with_both_open(
    transcript, node_calls, disambiguation_store, clean_pool, learner_id, embedding_client,
):
    loop = _loop(
        clean_pool,
        {"ASSESS:BRANCH": _TWO_BRANCHES, "DISAMBIGUATE:OPTIONS": _options_for_branches},
        embedding_client,
    )
    session_id = await transcript.create_session(learner_id)
    await loop.handle_turn(session_id, 0, "can you help me with derivatives?")

    (turn,) = await reconstruct_session_history(transcript, node_calls, disambiguation_store, session_id)
    assert turn.kind == "options"
    assert turn.options_message == OPTIONS_QUESTION == "Which of these did you mean?"
    assert turn.tutor_text is None
    assert len(turn.options) == 2
    assert all(o.status is OptionStatus.OPEN for o in turn.options)

    open_now = await latest_open_options(transcript, node_calls, disambiguation_store, session_id)
    assert {o.id for o in open_now} == {o.id for o in turn.options}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_click_resolution_updates_the_options_turn_and_adds_an_answer_turn(
    transcript, node_calls, disambiguation_store, clean_pool, learner_id, embedding_client,
):
    loop = _loop(
        clean_pool,
        {
            "ASSESS:BRANCH": _TWO_BRANCHES,
            "DISAMBIGUATE:OPTIONS": _options_for_branches,
            "FINAL:ANSWER": "the power rule says ...",
            "WRITE:FACT": _FACT,
        },
        embedding_client,
    )
    session_id = await transcript.create_session(learner_id)
    await loop.handle_turn(session_id, 0, "can you help me with derivatives?")
    options = await loop.pending_options(session_id)
    chosen, other = options[0], options[1]
    await loop.handle_turn(session_id, 1, chosen.text, selected_option_id=chosen.id)

    turns = await reconstruct_session_history(transcript, node_calls, disambiguation_store, session_id)
    assert [t.kind for t in turns] == ["options", "answer"]

    options_turn, answer_turn = turns
    by_id = {o.id: o for o in options_turn.options}
    assert by_id[chosen.id].status is OptionStatus.SELECTED
    assert by_id[other.id].status is OptionStatus.SUPERSEDED
    assert answer_turn.student_text is None, (
        "turns.text is the clicked option's own copy here, never something "
        "the student said -- a resumed chat must show no bubble for it, "
        "same as the live turn does"
    )
    assert answer_turn.tutor_text == "the power rule says ..."

    # resolved: the session's latest turn is an answer, not open options
    assert await latest_open_options(transcript, node_calls, disambiguation_store, session_id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_typing_past_options_leaves_none_selected_and_none_open(
    transcript, node_calls, disambiguation_store, clean_pool, learner_id, embedding_client,
):
    loop = _loop(
        clean_pool,
        {
            "ASSESS:BRANCH": _TWO_BRANCHES,
            "DISAMBIGUATE:OPTIONS": _options_for_branches,
            "WRITE:FACT": _FACT,
        },
        embedding_client,
    )
    session_id = await transcript.create_session(learner_id)
    await loop.handle_turn(session_id, 0, "can you help me with derivatives?")
    first_round = await loop.pending_options(session_id)

    # a fresh (non-click) message: typed past, superseding the open set.
    await loop.handle_turn(session_id, 1, "actually never mind, something else entirely")

    turns = await reconstruct_session_history(transcript, node_calls, disambiguation_store, session_id)
    options_turn = turns[0]
    assert options_turn.kind == "options"
    assert {o.id for o in options_turn.options} == {o.id for o in first_round}
    assert all(o.status is OptionStatus.SUPERSEDED for o in options_turn.options)
    assert not any(o.status is OptionStatus.SELECTED for o in options_turn.options)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_turn_with_no_recorded_node_calls_is_pending(
    transcript, node_calls, disambiguation_store, clean_pool, learner_id,
):
    """A turn can exist in `turns` with nothing in `node_calls` for it (a
    FinalAnswer call that raised before its own record() ran -- see
    session_history.py's module docstring). Simulated directly since
    forcing a real failure through the stub isn't the point of this test."""
    session_id = await transcript.create_session(learner_id)
    await transcript.record_turn(session_id, 0, "a message that got no response")

    (turn,) = await reconstruct_session_history(transcript, node_calls, disambiguation_store, session_id)
    assert turn.kind == "pending"
    assert turn.tutor_text is None
    assert turn.options_message is None
    assert turn.options == []


@pytest.mark.asyncio(loop_scope="session")
async def test_latest_open_options_on_a_session_with_no_turns_yet(
    transcript, node_calls, disambiguation_store, clean_pool, learner_id,
):
    session_id = await transcript.create_session(learner_id)
    assert await latest_open_options(transcript, node_calls, disambiguation_store, session_id) == []
