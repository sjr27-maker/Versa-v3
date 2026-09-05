"""SessionLoop wiring for time-sensitive grounding (grounding.py), on
top of SessionMode.MINIMAL_BRANCH.

The load-bearing assertions are the negative ones. This feature's whole
justification depends on two claims that are easy to state and easy to
break silently:

1. Without a WebSearchClient, nothing changes at all — no node, no row,
   no prompt difference. (Every other test file in this suite is an
   implicit test of this, but it deserves an explicit one.)
2. With grounding on, an ordinary question still costs zero searches.
"""

import json

import pytest

from probe.grounding import GroundingConfig
from probe.llm import StubLLMClient
from probe.loop import SessionLoop
from probe.websearch import SearchResult, StubWebSearchClient, WebSearchError

_NOT_AMBIGUOUS = json.dumps({"needs_branches": False, "branches": []})

_HIT = SearchResult(
    url="https://example.org/py",
    title="Python releases",
    publish_date="2026-08-01",
    excerpts=["Python 3.14 is the current stable release."],
)


def _make_loop(
    transcript,
    node_calls,
    disambiguation_store,
    llm=None,
    diagnostics_store=None,
    web_search_client=None,
    grounding_config=None,
):
    return SessionLoop(
        transcript=transcript,
        node_calls=node_calls,
        llm=llm or StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS}),
        diagnostics_store=diagnostics_store,
        disambiguation_store=disambiguation_store,
        web_search_client=web_search_client,
        grounding_config=grounding_config,
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_no_search_client_means_the_node_does_not_exist(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store
):
    """The pre-feature path, byte for byte: no PARALLEL_API_KEY, no
    grounding node, no extra node_calls row on any turn."""
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(transcript, node_calls, disambiguation_store)

    assert loop.ground_time_sensitive is None

    await loop.handle_turn(session_id, 0, "what is the latest version of Python?")

    assert (
        await node_calls.get_call_for_turn(session_id, 0, "GroundTimeSensitive")
    ) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_ordinary_question_costs_zero_searches_when_grounding_is_on(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store,
):
    search = StubWebSearchClient(default=[_HIT])
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store,
        diagnostics_store=diagnostics_store, web_search_client=search,
    )

    await loop.handle_turn(session_id, 0, "what is a derivative?")

    assert search.calls == [], (
        "grounding fired on an ordinary calculus question -- miscalibration"
    )
    diags = await diagnostics_store.list_for_session(session_id)
    assert diags[0].node_call_counts["GroundTimeSensitive"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_the_negative_case_is_still_recorded_to_node_calls(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store
):
    """Invariant 2, and the reason the firing rate is measurable: the
    node runs and records even when it finds nothing."""
    search = StubWebSearchClient(default=[_HIT])
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, web_search_client=search
    )

    await loop.handle_turn(session_id, 0, "explain the chain rule")

    call = await node_calls.get_call_for_turn(session_id, 0, "GroundTimeSensitive")
    assert call is not None, "the negative case must still be on the record"
    assert call.output_json["time_sensitive"] is False
    assert call.output_json["matched_marker"] is None
    assert call.output_json["searched"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_a_time_sensitive_turn_grounds_the_final_answer_prompt(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store
):
    search = StubWebSearchClient(default=[_HIT])
    llm = StubLLMClient(
        canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": "grounded answer"}
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        web_search_client=search,
    )

    message = await loop.handle_turn(
        session_id, 0, "what is the latest version of Python?"
    )

    assert message == "grounded answer"
    assert len(search.calls) == 1
    final_prompts = [p for p in llm.prompts if p.startswith("FINAL:ANSWER")]
    assert len(final_prompts) == 1
    assert "https://example.org/py" in final_prompts[0]
    assert "Python 3.14 is the current stable release." in final_prompts[0]
    assert "cite that source's URL inline" in final_prompts[0]
    # The instruction that a top-ranked source is not automatically the
    # answering one — the prompt half of the Q1 fix.
    assert "not automatically the one that answers it" in final_prompts[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_an_ordinary_turns_final_answer_prompt_has_no_grounding_block(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store
):
    search = StubWebSearchClient(default=[_HIT])
    llm = StubLLMClient(canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS})
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        web_search_client=search,
    )

    await loop.handle_turn(session_id, 0, "what is a derivative?")

    final_prompts = [p for p in llm.prompts if p.startswith("FINAL:ANSWER")]
    assert "live web search" not in final_prompts[0]
    assert "Source:" not in final_prompts[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_search_still_answers_and_warns_loudly(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store,
):
    search = StubWebSearchClient(error=WebSearchError("upstream 503"))
    llm = StubLLMClient(
        canned={"ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": "ungrounded answer"}
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store, web_search_client=search,
    )

    message = await loop.handle_turn(
        session_id, 0, "what is the latest version of Python?"
    )

    assert message == "ungrounded answer", "a search failure must not lose the turn"
    diags = await diagnostics_store.list_for_session(session_id)
    warnings = " ".join(diags[0].warnings)
    assert "GroundTimeSensitive" in warnings
    assert "upstream 503" in warnings
    assert "answering ungrounded" in warnings


@pytest.mark.asyncio(loop_scope="session")
async def test_grounding_config_disabled_turns_the_node_off_entirely(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store
):
    """The control arm of the comparison: a search client is present,
    but the config forces grounding off with no code change."""
    search = StubWebSearchClient(default=[_HIT])
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store,
        web_search_client=search,
        grounding_config=GroundingConfig(enabled=False),
    )

    assert loop.ground_time_sensitive is None

    await loop.handle_turn(session_id, 0, "what is the latest version of Python?")

    assert search.calls == []
    assert (
        await node_calls.get_call_for_turn(session_id, 0, "GroundTimeSensitive")
    ) is None
