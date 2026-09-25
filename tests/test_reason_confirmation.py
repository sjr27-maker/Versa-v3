"""IDEAS.md "ask for confirmation directly" — a REASON-based memory
match (memory.py, migration 056) is offered to the student as a direct
yes/no instead of going through ConfirmFactMatch's LLM judgment.
Raw store CRUD for the underlying disambiguation tables is covered by
test_disambiguation_store.py; this exercises the loop-level wiring and
the resume-view reconstruction.
"""

import json

import pytest

from versa.disambiguate import REASON_CONFIRM_NO_TEXT, REASON_CONFIRM_YES_TEXT
from versa.embeddings import EMBEDDING_DIM
from versa.llm import StubLLMClient
from versa.loop import SessionLoop
from versa.models import DisambiguationTurnKind, LearnerFact, LearnerFactType
from versa.session_history import reconstruct_session_history

_NOT_AMBIGUOUS = json.dumps({"needs_branches": False, "branches": []})


def _vec(x: float = 0.0) -> list[float]:
    return [x] + [0.0] * (EMBEDDING_DIM - 1)


def _make_loop(
    transcript, node_calls, disambiguation_store, llm=None, diagnostics_store=None,
    learner_fact_store=None, embedding_client=None,
):
    return SessionLoop(
        transcript=transcript,
        node_calls=node_calls,
        llm=llm or StubLLMClient(),
        diagnostics_store=diagnostics_store,
        disambiguation_store=disambiguation_store,
        learner_fact_store=learner_fact_store,
        embedding_client=embedding_client,
    )


async def _seed_reason_matching_fact(
    learner_fact_store, transcript, embedding_client, learner_id, message_text: str,
):
    """A fact whose REASON embedding matches `message_text`, but whose
    situation/resolution embedding does not -- the only way
    EmbedAndSearchFacts' dual search can pick the reason path (see
    memory.EmbedAndSearchFacts's own docstring)."""
    seed_session_id = await transcript.create_session(learner_id)
    turn_id = await transcript.record_turn(seed_session_id, 0, "an earlier message")
    shared_vector = _vec(1.0)
    unrelated_vector = [0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 2)
    fact = await learner_fact_store.add(
        LearnerFact(
            learner_id=learner_id, session_id=seed_session_id, turn_index=0,
            fact_type=LearnerFactType.DIRECT_ANSWER,
            situation="a completely unrelated situation",
            resolution="a completely unrelated resolution",
            embedding=unrelated_vector,
            reason="you tend to want the concrete example before the abstract rule",
            reason_embedding=shared_vector,
            source_turn_id=turn_id,
        )
    )
    embedding_client.canned[message_text] = shared_vector
    return fact


@pytest.mark.asyncio(loop_scope="session")
async def test_a_reason_match_offers_a_direct_confirmation_not_confirm_fact_match(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    message = "can you show me the worked example first?"
    await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, message
    )
    llm = StubLLMClient(
        canned={
            # ConfirmFactMatch is a trap: it must never be called for a
            # reason match -- the whole point is skipping the LLM-judged
            # path. AssessAndBranch runs ALONGSIDE the memory check
            # (IDEAS.md "show options first, remember second"); its
            # reading must be recorded but never offered.
            "ASSESS:BRANCH": json.dumps({"needs_branches": True, "branches": [
                {"statement": "recorded, superseded, never offered"},
            ]}),
            "CONFIRM:FACT_MATCH": json.dumps({"resolves": True}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )

    result = await loop.handle_turn(session_id, 0, message)

    assert "you tend to want the concrete example before the abstract rule" in result
    assert await node_calls.get_call_for_turn(session_id, 0, "ConfirmFactMatch") is None
    assert await node_calls.get_call_for_turn(session_id, 0, "AssessAndBranch") is not None

    disamb_turn = await disambiguation_store.get_latest_turn(session_id)
    assert disamb_turn.kind is DisambiguationTurnKind.REASON_CONFIRMATION
    options = await loop.pending_options(session_id)
    assert {o.text for o in options} == {REASON_CONFIRM_YES_TEXT, REASON_CONFIRM_NO_TEXT}

    diag = await diagnostics_store.get_for_turn(session_id, 0)
    assert diag.memory_match_via == "reason"
    assert diag.reason_confirmation_offered is True
    assert diag.reason_confirmed_by_student is None


@pytest.mark.asyncio(loop_scope="session")
async def test_confirming_yes_answers_with_the_reason_and_writes_a_strong_fact(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    message = "can you show me the worked example first?"
    await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, message
    )
    llm = StubLLMClient(
        canned={
            "FINAL:ANSWER": "here is the worked example",
            "WRITE:FACT": json.dumps({"situation": "s", "resolution": "r"}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )
    await loop.handle_turn(session_id, 0, message)
    options = await loop.pending_options(session_id)
    yes = next(o for o in options if o.text == REASON_CONFIRM_YES_TEXT)

    result = await loop.handle_turn(session_id, 1, yes.text, selected_option_id=yes.id)
    assert result == "here is the worked example"

    final_call = await node_calls.get_call_for_turn(session_id, 1, "FinalAnswer")
    assert final_call.input_json["branch_context"] is None
    assert "concrete example" in final_call.input_json["memory_context"]
    # The FIX from earlier in this session applied consistently here
    # too: student_message must be the real original message, never
    # the clicked option's own "Yes, that's right" copy.
    assert final_call.input_json["student_message"] == message

    diag = await diagnostics_store.get_for_turn(session_id, 1)
    assert diag.reason_confirmed_by_student is True

    facts = await learner_fact_store.list_by_learner(learner_id)
    newest = max(facts, key=lambda f: f.turn_index)
    assert newest.fact_type is LearnerFactType.REASON_CONFIRMED


@pytest.mark.asyncio(loop_scope="session")
async def test_confirming_no_answers_plainly_without_the_reason(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    message = "can you show me the worked example first?"
    await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, message
    )
    llm = StubLLMClient(
        canned={
            "FINAL:ANSWER": "a plain direct answer",
            "WRITE:FACT": json.dumps({"situation": "s", "resolution": "r"}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )
    await loop.handle_turn(session_id, 0, message)
    options = await loop.pending_options(session_id)
    no = next(o for o in options if o.text == REASON_CONFIRM_NO_TEXT)

    result = await loop.handle_turn(session_id, 1, no.text, selected_option_id=no.id)
    assert result == "a plain direct answer"

    final_call = await node_calls.get_call_for_turn(session_id, 1, "FinalAnswer")
    assert final_call.input_json["branch_context"] is None
    assert final_call.input_json["memory_context"] is None
    assert final_call.input_json["student_message"] == message

    diag = await diagnostics_store.get_for_turn(session_id, 1)
    assert diag.reason_confirmed_by_student is False

    facts = await learner_fact_store.list_by_learner(learner_id)
    newest = max(facts, key=lambda f: f.turn_index)
    assert newest.fact_type is LearnerFactType.DIRECT_ANSWER


@pytest.mark.asyncio(loop_scope="session")
async def test_a_message_that_contradicts_the_reason_is_not_offered_as_a_confirmation(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    """A live run found this concretely: pure cosine similarity cannot
    tell "still wants X" apart from "just said to stop doing X" -- an
    explicit reversal is topically near-identical to the reason it
    rejects. ConfirmReasonRelevance is the gate that catches it; here
    it says the message does NOT still apply, so no confirmation is
    offered and the turn answers (and gets classified) normally."""
    message = "actually, stop giving me worked examples, just the rule"
    await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, message
    )
    llm = StubLLMClient(
        canned={
            "ASSESS:BRANCH": _NOT_AMBIGUOUS,
            "FINAL:ANSWER": "here is the abstract rule directly",
            "WRITE:FACT": json.dumps({"situation": "s", "resolution": "r"}),
            "CONFIRM:REASON_RELEVANT": json.dumps({"still_applies": False}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )

    result = await loop.handle_turn(session_id, 0, message)

    assert result == "here is the abstract rule directly"
    call = await node_calls.get_call_for_turn(session_id, 0, "ConfirmReasonRelevance")
    assert call is not None
    assert call.input_json["current_message"] == message
    assert await node_calls.get_call_for_turn(session_id, 0, "AssessAndBranch") is not None

    options = await loop.pending_options(session_id)
    assert options == []

    diag = await diagnostics_store.get_for_turn(session_id, 0)
    assert diag.reason_confirmation_offered is False
    assert diag.memory_match_via == "reason"  # a match WAS found, just suppressed


@pytest.mark.asyncio(loop_scope="session")
async def test_a_previously_declined_reason_is_not_offered_again(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    """A live run found this too: nothing suppressed an already-declined
    reason from being offered again on the very next relevant message.
    declined_fact_ids_for_learner (backed by the existing
    reason_confirmed_by_student=False rows, no new column) is the fix."""
    first_message = "can you show me the worked example first?"
    fact = await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, first_message
    )
    llm = StubLLMClient(
        canned={
            "ASSESS:BRANCH": _NOT_AMBIGUOUS,
            "FINAL:ANSWER": "a plain direct answer",
            "WRITE:FACT": json.dumps({"situation": "s", "resolution": "r"}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )
    await loop.handle_turn(session_id, 0, first_message)
    options = await loop.pending_options(session_id)
    no = next(o for o in options if o.text == REASON_CONFIRM_NO_TEXT)
    await loop.handle_turn(session_id, 1, no.text, selected_option_id=no.id)

    second_message = "walk me through a worked example before the rule, please"
    embedding_client.canned[second_message] = embedding_client.canned[first_message]

    result = await loop.handle_turn(session_id, 2, second_message)

    assert result == "a plain direct answer"
    options_again = await loop.pending_options(session_id)
    assert options_again == []
    diag = await diagnostics_store.get_for_turn(session_id, 2)
    assert diag.reason_confirmation_offered is False
    assert diag.memory_match_via == "reason"  # matched again, just not re-offered

    declined = await diagnostics_store.declined_fact_ids_for_learner(learner_id)
    assert fact.id in declined


@pytest.mark.asyncio(loop_scope="session")
async def test_typing_past_a_reason_confirmation_supersedes_it_like_any_other_offer(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    """Reuses the EXISTING typed-past mechanism (loop.py's "3b") for
    free -- a reason confirmation is just another open option set as
    far as that logic is concerned."""
    message = "can you show me the worked example first?"
    await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, message
    )
    llm = StubLLMClient(
        canned={
            "ASSESS:BRANCH": _NOT_AMBIGUOUS,
            "FINAL:ANSWER": "a fresh answer",
            "WRITE:FACT": json.dumps({"situation": "s", "resolution": "r"}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )
    await loop.handle_turn(session_id, 0, message)

    result = await loop.handle_turn(session_id, 1, "actually, something else entirely")
    assert result == "a fresh answer"

    disamb_turn = await disambiguation_store.get_latest_turn(session_id)
    assert disamb_turn.kind is DisambiguationTurnKind.AMBIGUITY  # a fresh, ordinary turn
    prior_turns_options = await loop.pending_options(session_id)
    assert prior_turns_options == []  # the reason-confirmation set was superseded


@pytest.mark.asyncio(loop_scope="session")
async def test_session_history_reconstructs_offer_and_click_correctly(
    transcript, node_calls, clean_pool, learner_id, disambiguation_store,
    diagnostics_store, learner_fact_store, embedding_client,
):
    message = "can you show me the worked example first?"
    await _seed_reason_matching_fact(
        learner_fact_store, transcript, embedding_client, learner_id, message
    )
    llm = StubLLMClient(
        canned={
            "FINAL:ANSWER": "here is the worked example",
            "WRITE:FACT": json.dumps({"situation": "s", "resolution": "r"}),
        }
    )
    session_id = await transcript.create_session(learner_id)
    loop = _make_loop(
        transcript, node_calls, disambiguation_store, llm=llm,
        diagnostics_store=diagnostics_store,
        learner_fact_store=learner_fact_store, embedding_client=embedding_client,
    )
    await loop.handle_turn(session_id, 0, message)
    options = await loop.pending_options(session_id)
    yes = next(o for o in options if o.text == REASON_CONFIRM_YES_TEXT)
    await loop.handle_turn(session_id, 1, yes.text, selected_option_id=yes.id)

    turns = await reconstruct_session_history(transcript, node_calls, disambiguation_store, session_id)
    offer_turn, answer_turn = turns

    assert offer_turn.kind == "options"
    assert "concrete example before the abstract rule" in offer_turn.options_message
    assert {o.text for o in offer_turn.options} == {REASON_CONFIRM_YES_TEXT, REASON_CONFIRM_NO_TEXT}
    assert offer_turn.student_text == message  # the real original message, shown normally

    assert answer_turn.kind == "answer"
    assert answer_turn.tutor_text == "here is the worked example"
    assert answer_turn.student_text is None, (
        "no echoed bubble for the click, same as an ordinary branch-reading click"
    )
