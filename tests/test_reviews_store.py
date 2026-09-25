"""Round-trip tests for ReviewStore/ExplanationCacheStore/ItemQnAStore
(migrations 065-067) and the pure `apply_reviews_overlay` overlay."""

from __future__ import annotations

from uuid import uuid4

import pytest

from versa.reviews import (
    ReviewedItem,
    StudentReview,
    apply_reviews_overlay,
)


def _review(review_type, revised=None, previous=None):
    return StudentReview(
        id=uuid4(), review_type=review_type, revised_statement=revised,
        previous_statement=previous,
    )


def test_overlay_with_no_reviews_is_the_base_statement():
    overlay = apply_reviews_overlay("base", [])
    assert overlay == ReviewedItem(statement="base", edited=False, archived=False, review_count=0)


def test_overlay_latest_edit_wins():
    reviews = [_review("edit", "first edit"), _review("edit", "second edit")]
    overlay = apply_reviews_overlay("base", reviews)
    assert overlay.statement == "second edit"
    assert overlay.edited is True


def test_overlay_archive_then_restore_then_archive():
    reviews = [_review("archive"), _review("restore"), _review("archive")]
    assert apply_reviews_overlay("base", reviews).archived is True
    assert apply_reviews_overlay("base", reviews[:2]).archived is False


def test_overlay_approve_does_not_change_statement_or_archived():
    overlay = apply_reviews_overlay("base", [_review("approve")])
    assert overlay.statement == "base"
    assert overlay.archived is False
    assert overlay.edited is False


@pytest.mark.asyncio(loop_scope="session")
async def test_review_store_requires_exactly_one_subject(review_store):
    with pytest.raises(ValueError):
        await review_store.add(review_type="approve")
    with pytest.raises(ValueError):
        await review_store.add(
            claim_id=uuid4(), thinking_style_candidate_id=uuid4(), review_type="approve"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_review_store_round_trip_for_a_claim(review_store, claim_store, learner_id):
    from tests.test_claims_store import _claim  # reuse the existing factory

    claim = await claim_store.create(_claim(learner_id))
    added = await review_store.add(claim_id=claim.id, review_type="approve")
    assert added.review_type == "approve"
    listed = await review_store.list_for_claim(claim.id)
    assert [r.id for r in listed] == [added.id]
    fetched = await review_store.get(added.id)
    assert fetched.id == added.id


@pytest.mark.asyncio(loop_scope="session")
async def test_explanation_cache_round_trip_for_a_thinking_style_candidate(
    explanation_cache_store, thinking_style_store, transcript, learner_id
):
    session_id = await transcript.create_session(learner_id)
    candidate = await thinking_style_store.create_candidate(
        learner_id, session_id, "path summary", [0.0] * 768
    )
    assert await explanation_cache_store.get_cached(
        claim_id=None, thinking_style_candidate_id=candidate.id, evidence_fingerprint="1"
    ) is None
    await explanation_cache_store.store(
        claim_id=None, thinking_style_candidate_id=candidate.id, evidence_fingerprint="1",
        explanation="because X", node_call_session_id=session_id, node_call_turn_index=0,
    )
    cached = await explanation_cache_store.get_cached(
        claim_id=None, thinking_style_candidate_id=candidate.id, evidence_fingerprint="1"
    )
    assert cached == "because X"
    # a different fingerprint (more evidence arrived) is a cache miss
    assert await explanation_cache_store.get_cached(
        claim_id=None, thinking_style_candidate_id=candidate.id, evidence_fingerprint="2"
    ) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_qna_store_round_trip_and_pre_generated_id(
    qna_store, thinking_style_store, transcript, learner_id
):
    session_id = await transcript.create_session(learner_id)
    candidate = await thinking_style_store.create_candidate(
        learner_id, session_id, "path summary", [0.0] * 768
    )
    pre_id = uuid4()
    saved = await qna_store.add(
        claim_id=None, thinking_style_candidate_id=candidate.id,
        question="why?", answer="because", intent="none", applied_review_id=None,
        node_call_session_id=session_id, node_call_turn_index=0, qna_id=pre_id,
    )
    assert saved.id == pre_id
    listed = await qna_store.list_for_thinking_style(candidate.id)
    assert [t.id for t in listed] == [pre_id]
