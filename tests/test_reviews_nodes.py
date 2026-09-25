"""ExplainItem/AnswerItemQuestion nodes, evidence-block builders, the
undo-inverse helper, and apply_ask_intent's explicit-correction gate."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from versa.llm import StubLLMClient
from versa.models import (
    ApproachAxis,
    Claim,
    ClaimEvidence,
    ClaimSource,
    ClaimWritePolicy,
    EvidenceDirection,
    StatedPreferenceLabel,
    ThinkingStyleCandidate,
    ThinkingStyleStatus,
)
from versa.reviews import (
    AnswerItemQuestion,
    AskAnswer,
    ExplainItem,
    ReviewStore,
    apply_ask_intent,
    build_claim_evidence_block,
    build_thinking_style_evidence_block,
    build_undo_review,
    claim_fingerprint,
    thinking_style_fingerprint,
)


def _candidate(learner_id, session_ids, confirmations=1, status=ThinkingStyleStatus.CANDIDATE):
    return ThinkingStyleCandidate(
        learner_id=learner_id, session_ids=session_ids, path_summary="concrete then abstract",
        path_summary_embedding=[0.0] * 768, confirmation_count=confirmations, status=status,
    )


def _claim(learner_id):
    return Claim(
        learner_id=learner_id, statement="prefers brevity", test="offer terse vs full; picks terse",
        value=StatedPreferenceLabel.PREFERS_BREVITY, confidence=0.6,
        source=ClaimSource.INFERRED, write_policy=ClaimWritePolicy.SLOW_DRIFT,
        statement_embedding=[0.0] * 768,
    )


def _evidence(claim_id, learner_id, n=2):
    return [
        ClaimEvidence(
            claim_id=claim_id, learner_id=learner_id, interaction_id=uuid4(),
            direction=EvidenceDirection.SUPPORTS, topic="derivatives", axis=ApproachAxis.BREVITY_DEPTH,
            session_id=uuid4(), test_fired=True, contradiction_was_possible=True,
        )
        for _ in range(n)
    ]


def test_fingerprints_track_evidence_count():
    assert thinking_style_fingerprint(_candidate(uuid4(), [uuid4()], confirmations=3)) == "3"
    assert claim_fingerprint(_evidence(uuid4(), uuid4(), n=4)) == "4"


def test_thinking_style_evidence_block_cites_only_given_sessions():
    candidate = _candidate(uuid4(), [uuid4(), uuid4()], confirmations=2)
    sessions = [
        {"index": 0, "created_at": "t0", "topic_preview": "what is a derivative?",
         "path_summary": candidate.path_summary, "confirms": None},
        {"index": 1, "created_at": "t1", "topic_preview": "how do circuits work?",
         "path_summary": candidate.path_summary, "confirms": True},
    ]
    block = build_thinking_style_evidence_block(candidate, sessions)
    assert "what is a derivative?" in block
    assert "how do circuits work?" in block
    assert "matched" in block
    assert "2 session(s)" in block


def test_claim_evidence_block_lists_every_episode():
    claim = _claim(uuid4())
    evidence = _evidence(claim.id, claim.learner_id, n=3)
    block = build_claim_evidence_block(claim, claim.statement, evidence)
    assert claim.test in block
    assert block.count("derivatives") == 3


@pytest.mark.asyncio
async def test_explain_item_calls_llm_once_and_returns_its_text():
    llm = StubLLMClient(canned={"EXPLAIN:PATTERN": "because of X and Y"})
    node = ExplainItem(llm)
    result = await node.run("some evidence block")
    assert result == "because of X and Y"
    assert node.last_call_count == 1


@pytest.mark.asyncio
async def test_answer_item_question_defaults_to_no_intent():
    llm = StubLLMClient()  # unconfigured: uses the conservative ASK:PATTERN default
    node = AnswerItemQuestion(llm)
    result = await node.run("evidence", "", "why do you think this?")
    assert isinstance(result, AskAnswer)
    assert result.intent == "none"


@pytest.mark.asyncio
async def test_answer_item_question_parses_an_explicit_edit_intent():
    raw = json.dumps({
        "answer": "Sounds like you actually prefer theory first -- updating.",
        "intent": "edit", "new_statement": "prefers theory before examples",
    })
    llm = StubLLMClient(canned={"ASK:PATTERN": raw})
    node = AnswerItemQuestion(llm)
    result = await node.run("evidence", "", "no, I actually like theory first")
    assert result.intent == "edit"
    assert result.new_statement == "prefers theory before examples"


def test_build_undo_review_inverts_edit_archive_restore_and_refuses_approve():
    from versa.reviews import StudentReview

    edit = StudentReview(id=uuid4(), review_type="edit", revised_statement="new", previous_statement="old")
    inverse = build_undo_review(edit)
    assert inverse == {"review_type": "edit", "revised_statement": "old", "previous_statement": "new"}

    archive = StudentReview(id=uuid4(), review_type="archive")
    assert build_undo_review(archive) == {"review_type": "restore"}

    restore = StudentReview(id=uuid4(), review_type="restore")
    assert build_undo_review(restore) == {"review_type": "archive"}

    approve = StudentReview(id=uuid4(), review_type="approve")
    assert build_undo_review(approve) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_ask_intent_none_never_writes_a_review(review_store, learner_id, claim_store):
    claim = await claim_store.create(_claim(learner_id))
    result = AskAnswer(answer="just an answer", intent="none")
    applied = await apply_ask_intent(
        review_store, claim_id=claim.id, thinking_style_candidate_id=None,
        current_statement="prefers brevity", answer=result, qna_id=uuid4(),
    )
    assert applied is None
    assert await review_store.list_for_claim(claim.id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_ask_intent_edit_writes_a_chat_sourced_review_linked_to_the_qna(
    review_store, learner_id, claim_store
):
    claim = await claim_store.create(_claim(learner_id))
    qna_id = uuid4()
    result = AskAnswer(answer="updating", intent="edit", new_statement="prefers theory first")
    review_id = await apply_ask_intent(
        review_store, claim_id=claim.id, thinking_style_candidate_id=None,
        current_statement="prefers brevity", answer=result, qna_id=qna_id,
    )
    assert review_id is not None
    reviews = await review_store.list_for_claim(claim.id)
    assert len(reviews) == 1
    assert reviews[0].source == "chat"
    assert reviews[0].qna_id == qna_id
    assert reviews[0].revised_statement == "prefers theory first"
    assert reviews[0].previous_statement == "prefers brevity"
