"""Student review actions, "why" explanations, and ask-about-it threads for
thinking-style candidates and claims (IDEAS.md Thinking-style page;
migrations 065-067).

Three append-only stores (CLAUDE.md invariants 1/4/6-11's established
pattern — no `delete`/`remove` method, no DELETE SQL, checked by
`test_reviews_append_only.py` the same way every earlier store is):

- `ReviewStore` (`student_reviews`) — approve/edit/archive/restore. "Delete"
  in the UI is `archive`: an overlay computed from the latest review row,
  never a removed row. `apply_reviews_overlay` is the one place that overlay
  is computed, so the server and any future reader agree on what "current
  text" and "archived" mean.
- `ExplanationCacheStore` (`explanation_cache`) — one real LLM call
  (`ExplainItem`, routed through `SessionLoop._call_node`, invariant 2) per
  distinct evidence state; a repeat view is served from here.
- `ItemQnAStore` (`item_qna`) — one row per question+answer pair, so a
  thread reappears exactly as it happened when the detail view reopens.

The ask flow can apply a change on the student's behalf (an explicit
correction, never a plain question) — `apply_ask_intent` below is the one
place that happens, and it always goes through `ReviewStore`, so a
chat-applied edit is indistinguishable in the history from one applied by
hand except for `source='chat'` and the linking `qna_id`.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
from pydantic import BaseModel

from versa.claims import reconcile_candidate
from versa.llm import LLMClient
from versa.models import (
    Claim,
    ClaimCandidate,
    ClaimEvidence,
    ClaimSource,
    ClaimStatus,
    ClaimWritePolicy,
    StatedPreferenceLabel,
    ThinkingStyleCandidate,
)


class ReviewedItem(BaseModel):
    """The overlay `apply_reviews_overlay` computes over a base statement —
    what a reader should actually show, never the raw stored column."""

    statement: str
    edited: bool
    archived: bool
    review_count: int


def apply_reviews_overlay(base_statement: str, reviews: list["StudentReview"]) -> ReviewedItem:
    """Pure function: the base statement plus every review row for one
    subject, chronological, folds down to what should actually be shown.
    The latest 'edit' row's `revised_statement` wins over the base; the
    latest archive/restore row decides `archived`. Never mutates anything —
    callers persist by inserting a new review row, not by touching this."""
    statement = base_statement
    archived = False
    for r in reviews:
        if r.review_type == "edit" and r.revised_statement:
            statement = r.revised_statement
        elif r.review_type == "archive":
            archived = True
        elif r.review_type == "restore":
            archived = False
    return ReviewedItem(
        statement=statement, edited=any(r.review_type == "edit" for r in reviews),
        archived=archived, review_count=len(reviews),
    )


class StudentReview(BaseModel):
    id: UUID
    claim_id: UUID | None = None
    thinking_style_candidate_id: UUID | None = None
    review_type: str
    revised_statement: str | None = None
    previous_statement: str | None = None
    source: str = "direct"
    qna_id: UUID | None = None
    source_session_id: UUID | None = None
    source_turn_index: int | None = None
    created_at: object = None


class ReviewStore:
    """`student_reviews` (migration 065). Append-only: no delete/remove
    method, no DELETE SQL — see this module's own docstring."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(
        self,
        *,
        claim_id: UUID | None = None,
        thinking_style_candidate_id: UUID | None = None,
        review_type: str,
        revised_statement: str | None = None,
        previous_statement: str | None = None,
        source: str = "direct",
        qna_id: UUID | None = None,
        source_session_id: UUID | None = None,
        source_turn_index: int | None = None,
    ) -> StudentReview:
        if (claim_id is None) == (thinking_style_candidate_id is None):
            raise ValueError("exactly one of claim_id/thinking_style_candidate_id required")
        review_id = uuid4()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO student_reviews (
                    id, claim_id, thinking_style_candidate_id, review_type,
                    revised_statement, previous_statement, source, qna_id,
                    source_session_id, source_turn_index
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING *
                """,
                review_id, claim_id, thinking_style_candidate_id, review_type,
                revised_statement, previous_statement, source, qna_id,
                source_session_id, source_turn_index,
            )
        return StudentReview(**dict(row))

    async def list_for_claim(self, claim_id: UUID) -> list[StudentReview]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM student_reviews WHERE claim_id = $1 ORDER BY created_at", claim_id
            )
        return [StudentReview(**dict(r)) for r in rows]

    async def list_for_thinking_style(self, candidate_id: UUID) -> list[StudentReview]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM student_reviews WHERE thinking_style_candidate_id = $1 "
                "ORDER BY created_at",
                candidate_id,
            )
        return [StudentReview(**dict(r)) for r in rows]

    async def get(self, review_id: UUID) -> StudentReview | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM student_reviews WHERE id = $1", review_id)
        return None if row is None else StudentReview(**dict(row))

    async def list_sandbox_chat_reviews_for_turn(
        self, session_id: UUID, turn_index: int
    ) -> list[StudentReview]:
        """Every claim update a sandbox-chat turn caused (migration 068) --
        session_history.py's read side for replaying the inline "Noted:
        ..." note on resume."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM student_reviews WHERE source = 'sandbox_chat' "
                "AND source_session_id = $1 AND source_turn_index = $2 ORDER BY created_at",
                session_id, turn_index,
            )
        return [StudentReview(**dict(r)) for r in rows]


class ExplanationCacheStore:
    """`explanation_cache` (migration 066). Append-only: a new evidence
    state writes a new row under a new fingerprint, never overwrites."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_cached(
        self, *, claim_id: UUID | None, thinking_style_candidate_id: UUID | None,
        evidence_fingerprint: str,
    ) -> str | None:
        col = "claim_id" if claim_id is not None else "thinking_style_candidate_id"
        subject = claim_id if claim_id is not None else thinking_style_candidate_id
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                f"SELECT explanation FROM explanation_cache "
                f"WHERE {col} = $1 AND evidence_fingerprint = $2",
                subject, evidence_fingerprint,
            )

    async def store(
        self, *, claim_id: UUID | None, thinking_style_candidate_id: UUID | None,
        evidence_fingerprint: str, explanation: str,
        node_call_session_id: UUID, node_call_turn_index: int,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO explanation_cache (
                    id, claim_id, thinking_style_candidate_id, evidence_fingerprint,
                    explanation, node_call_session_id, node_call_turn_index
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT DO NOTHING
                """,
                uuid4(), claim_id, thinking_style_candidate_id, evidence_fingerprint,
                explanation, node_call_session_id, node_call_turn_index,
            )


class ItemQnA(BaseModel):
    id: UUID
    claim_id: UUID | None = None
    thinking_style_candidate_id: UUID | None = None
    question: str
    answer: str
    intent: str = "none"
    applied_review_id: UUID | None = None
    created_at: object = None


class ItemQnAStore:
    """`item_qna` (migration 067). Append-only: one row per question+answer,
    never edited afterward."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def add(
        self, *, claim_id: UUID | None, thinking_style_candidate_id: UUID | None,
        question: str, answer: str, intent: str, applied_review_id: UUID | None,
        node_call_session_id: UUID, node_call_turn_index: int,
        qna_id: UUID | None = None,
    ) -> ItemQnA:
        """`qna_id` may be pre-generated by the caller (server.py's ask
        endpoints do this) so a review this Q&A causes can reference the
        Q&A row's id before the row itself is inserted."""
        qna_id = qna_id or uuid4()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO item_qna (
                    id, claim_id, thinking_style_candidate_id, question, answer,
                    intent, applied_review_id, node_call_session_id, node_call_turn_index
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING *
                """,
                qna_id, claim_id, thinking_style_candidate_id, question, answer,
                intent, applied_review_id, node_call_session_id, node_call_turn_index,
            )
        return ItemQnA(**dict(row))

    async def list_for_claim(self, claim_id: UUID) -> list[ItemQnA]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM item_qna WHERE claim_id = $1 ORDER BY created_at", claim_id
            )
        return [ItemQnA(**dict(r)) for r in rows]

    async def list_for_thinking_style(self, candidate_id: UUID) -> list[ItemQnA]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM item_qna WHERE thinking_style_candidate_id = $1 ORDER BY created_at",
                candidate_id,
            )
        return [ItemQnA(**dict(r)) for r in rows]


# --------------------------------------------------------------------- evidence


def thinking_style_fingerprint(candidate: ThinkingStyleCandidate) -> str:
    return str(candidate.confirmation_count)


def claim_fingerprint(evidence: list[ClaimEvidence]) -> str:
    return str(len(evidence))


def build_thinking_style_evidence_block(
    candidate: ThinkingStyleCandidate, sessions: list[dict],
) -> str:
    """Pure, deterministic text block: exactly what is stored about this
    candidate, nothing invented — the ONLY input `ExplainItem` and
    `AnswerItemQuestion` may cite for a thinking-style item.

    `sessions` is one dict per confirming session, in `candidate.session_ids`
    order: {index, created_at, topic_preview, path_summary,
    confirmed_against (existing_path_summary or None), confirms (bool or
    None -- None for the session that created the candidate)}.
    """
    lines = [
        f"Hypothesized pattern: {candidate.path_summary}",
        f"Status: {candidate.status.value}, confirmed independently by "
        f"{candidate.confirmation_count} session(s).",
        "",
        "Sessions behind this pattern:",
    ]
    for s in sessions:
        lines.append(
            f"- Session {s['index'] + 1} ({s['created_at']}), opened with: "
            f"{s['topic_preview']!r}"
        )
        lines.append(f"  This session's own order-structure: {s['path_summary']}")
        if s["confirms"] is not None:
            verdict = "matched" if s["confirms"] else "did not match"
            lines.append(f"  Compared against the pattern so far: {verdict}.")
    return "\n".join(lines)


def build_claim_evidence_block(
    claim: Claim, statement: str, evidence: list[ClaimEvidence],
) -> str:
    """Pure, deterministic text block for a claim — the ONLY input
    `ExplainItem`/`AnswerItemQuestion` may cite for a claim item."""
    lines = [
        f"Claim: {statement}",
        f"Falsifiable test this claim licenses: {claim.test}",
        f"Status: {claim.status.value}, confidence {claim.confidence:.2f}.",
        "",
        f"Evidence episodes ({len(evidence)}):",
    ]
    for e in evidence:
        fired = "the test could fire" if e.contradiction_was_possible else "the test could not fire"
        lines.append(
            f"- {e.created_at}, topic {e.topic!r}"
            + (f", axis {e.axis.value}" if e.axis else "")
            + f": {e.direction.value} ({fired}, test_fired={e.test_fired})"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------- nodes


def _explain_prompt(evidence_block: str) -> str:
    return (
        "EXPLAIN:PATTERN\n"
        f"{evidence_block}\n\n"
        "Write a short, plain-language paragraph (3-5 sentences) explaining "
        "why Versa believes this, for the student to read about themselves. "
        "Cite ONLY the evidence given above -- never invent a session, a "
        "number, or a detail not listed. If the evidence is thin (e.g. a "
        "single session, or few episodes), say so plainly rather than "
        "sounding more certain than the evidence supports."
    )


class ExplainItem:
    """The "why Versa thinks this" node -- one fast-tier call per distinct
    evidence state (see `ExplanationCacheStore`), routed through
    `SessionLoop._call_node` so it is recorded to `node_calls` (invariant
    2) exactly like every other node in this codebase."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, evidence_block: str) -> str:
        self.last_call_count = 0
        text = await self._llm.complete(_explain_prompt(evidence_block))
        self.last_call_count += 1
        return text.strip()


def _ask_prompt(evidence_block: str, qna_history_block: str, question: str) -> str:
    return (
        "ASK:PATTERN\n"
        f"{evidence_block}\n"
        + (f"\nEarlier questions in this conversation:\n{qna_history_block}\n" if qna_history_block else "")
        + f"\nThe student asks: {question!r}\n\n"
        "Answer ONLY from the evidence above. If the evidence doesn't say, "
        "say plainly that you don't know rather than guessing.\n"
        "Separately, judge the student's INTENT: are they only asking a "
        "question (intent=none), or explicitly correcting/refining the "
        "wording (intent=edit, with the corrected statement in "
        "new_statement), explicitly confirming it's right (intent=approve), "
        "or explicitly asking to remove/archive it (intent=archive)? Only "
        "use edit/approve/archive when the student's words are an explicit "
        "instruction, never inferred from a plain question.\n"
        'Respond with JSON: {"answer": "...", "intent": "none|edit|approve|archive", '
        '"new_statement": "..." or null}'
    )


class AskAnswer(BaseModel):
    answer: str
    intent: str = "none"
    new_statement: str | None = None


def _parse_ask_answer(raw: str) -> AskAnswer:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return AskAnswer(answer=raw.strip() or "I couldn't form an answer from what's on record.")
    if not isinstance(parsed, dict):
        return AskAnswer(answer=str(parsed))
    intent = parsed.get("intent") if parsed.get("intent") in ("none", "edit", "approve", "archive") else "none"
    return AskAnswer(
        answer=str(parsed.get("answer", "")).strip() or "I couldn't form an answer from what's on record.",
        intent=intent,
        new_statement=parsed.get("new_statement") if intent == "edit" else None,
    )


class AnswerItemQuestion:
    """The ask-about-it node -- grounded only in the same evidence block
    `ExplainItem` used, plus this thread's own prior turns. Structured
    output lets the server apply an explicit correction through the same
    append-only review path a manual Edit/Approve/Delete uses (see
    `apply_ask_intent`) -- a plain question never changes anything."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, evidence_block: str, qna_history_block: str, question: str) -> AskAnswer:
        self.last_call_count = 0
        raw = await self._llm.complete(_ask_prompt(evidence_block, qna_history_block, question))
        self.last_call_count += 1
        return _parse_ask_answer(raw)


def render_qna_history(turns: list[ItemQnA]) -> str:
    return "\n".join(f"Q: {t.question}\nA: {t.answer}" for t in turns)


async def apply_ask_intent(
    review_store: ReviewStore, *, claim_id: UUID | None, thinking_style_candidate_id: UUID | None,
    current_statement: str, answer: AskAnswer, qna_id: UUID,
) -> UUID | None:
    """The one place an ask-flow answer is allowed to change anything --
    always through `ReviewStore`, always `source='chat'`, always linked to
    the `qna_id` that caused it. Returns the new review's id, or None for
    intent=none. A plain question (`intent='none'`) never reaches here from
    the server (see server.py's `ask_item_question`), but this stays a
    no-op for it regardless, defensively."""
    if answer.intent == "none":
        return None
    if answer.intent == "edit" and answer.new_statement:
        review = await review_store.add(
            claim_id=claim_id, thinking_style_candidate_id=thinking_style_candidate_id,
            review_type="edit", revised_statement=answer.new_statement,
            previous_statement=current_statement, source="chat", qna_id=qna_id,
        )
        return review.id
    if answer.intent in ("approve", "archive"):
        review = await review_store.add(
            claim_id=claim_id, thinking_style_candidate_id=thinking_style_candidate_id,
            review_type=answer.intent, source="chat", qna_id=qna_id,
        )
        return review.id
    return None


def _match_stated_preference_prompt(candidates: list[str], stated_preference: str) -> str:
    listed = "\n".join(f"  [{i}] {c}" for i, c in enumerate(candidates))
    return (
        "MATCH:STATED_PREFERENCE\n"
        f"The student just explicitly said: {stated_preference!r}\n\n"
        f"Existing recorded claims about the same kind of preference:\n{listed}\n\n"
        "Does this statement genuinely refer to the SAME standing preference as one of "
        "these, just now restated (possibly refined or reversed)? If so, which index, "
        "and what should happen to it: 'approve' (it reaffirms the existing wording "
        "as-is), 'edit' (same idea, but the wording should now read like the student's "
        "own new phrasing -- give new_statement), or 'archive' (the student is "
        "explicitly reversing/retracting it)? If none of them are genuinely the same "
        "preference, say so -- a new claim will be created instead; do not force a "
        "match.\n"
        'Respond with JSON: {"matched_index": int or null, '
        '"action": "approve"|"edit"|"archive"|null, "new_statement": "..." or null}'
    )


class MatchJudgement(BaseModel):
    matched_index: int | None = None
    action: str | None = None
    new_statement: str | None = None


def _parse_match_judgement(raw: str, n_candidates: int) -> MatchJudgement:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return MatchJudgement()
    if not isinstance(parsed, dict):
        return MatchJudgement()
    idx = parsed.get("matched_index")
    if not isinstance(idx, int) or not (0 <= idx < n_candidates):
        return MatchJudgement()
    action = parsed.get("action")
    if action not in ("approve", "edit", "archive"):
        return MatchJudgement()
    return MatchJudgement(
        matched_index=idx, action=action,
        new_statement=parsed.get("new_statement") if action == "edit" else None,
    )


class MatchStatedPreferenceToClaim:
    """Background-only (see `apply_stated_preference_to_claims` / loop.py's
    `_run_stated_preference_classification`) -- one fast-tier call, made
    only when `ClassifyStatedPreference` already found an EXPLICIT
    statement, judging whether it restates an existing same-label claim
    (and if so, how) or is genuinely new. Conservative default (no match)
    on any unparseable response, same discipline as every other judge node
    in this codebase -- an uncertain call never silently overwrites a
    claim."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self.last_call_count = 0

    async def run(self, candidates: list[str], stated_preference: str) -> MatchJudgement:
        self.last_call_count = 0
        if not candidates:
            return MatchJudgement()
        raw = await self._llm.complete(_match_stated_preference_prompt(candidates, stated_preference))
        self.last_call_count += 1
        return _parse_match_judgement(raw, len(candidates))


def build_stated_preference_test(label: StatedPreferenceLabel, stated_preference: str) -> str:
    """A generic, templated falsifiable test for a claim founded directly
    on an explicit statement rather than an LLM extraction -- no LLM call
    needed (the statement itself already IS the evidence; the "test" only
    needs to exist so this claim has the same shape as every other one).
    """
    return (
        f"When a future choice turns on this dimension, the learner acts "
        f"consistently with having said: {stated_preference!r}."
    )


async def apply_stated_preference_to_claims(
    *,
    claim_store,
    review_store: ReviewStore,
    run_match_node,
    embedding_client,
    learner_id: UUID,
    session_id: UUID,
    turn_index: int,
    interaction_id: UUID,
    label: StatedPreferenceLabel,
    stated_preference: str,
) -> dict | None:
    """The sandbox-chat claim-update flow (IDEAS.md Thinking-style page,
    "the main chat should be able to update claims too"): an EXPLICIT
    stated preference either revises an existing same-label claim (through
    `ReviewStore`, `source='sandbox_chat'`, linked to this turn -- the
    same append-only path the Thinking-style page's own buttons use) or
    creates a new one through `claims.reconcile_candidate`, the ordinary
    evidence-based creation path, marked `ClaimSource.STATED`.

    `run_match_node` is `(candidates: list[str], stated_preference: str) ->
    Awaitable[MatchJudgement]` -- the caller's own
    `SessionLoop._call_node(self.match_stated_preference_to_claim, ...)`
    closure, so the LLM call is recorded to `node_calls` (invariant 2) by
    the loop, exactly like every other node; this function never calls
    `.run()` directly.

    Returns a plain dict describing what happened, for the caller to push
    as a `claim_update` websocket event -- "created" for a genuinely new
    claim, or "supported" (no review row) when `reconcile_candidate`'s OWN
    similarity check matched an existing claim via the ordinary evidence
    path instead (a different, already-real signal a review row would
    only duplicate) -- told apart by comparing the returned claim's id
    against the candidate list already fetched, since `reconcile_candidate`
    never reports which happened.
    """
    live_excluded = {ClaimStatus.RETRACTED, ClaimStatus.SUPERSEDED}
    candidates = [
        c for c in await claim_store.list_for_learner(learner_id)
        if c.value == label and c.status not in live_excluded
    ]

    if candidates:
        statements = []
        for c in candidates:
            current = await claim_store.get_current_statement(c.id)
            statements.append(current.statement if current else c.statement)
        judgement = await run_match_node(statements, stated_preference)
        if judgement.matched_index is not None:
            target = candidates[judgement.matched_index]
            current = await claim_store.get_current_statement(target.id)
            previous = current.statement if current else target.statement
            review = await review_store.add(
                claim_id=target.id, review_type=judgement.action,
                revised_statement=judgement.new_statement if judgement.action == "edit" else None,
                previous_statement=previous if judgement.action == "edit" else None,
                source="sandbox_chat", source_session_id=session_id, source_turn_index=turn_index,
            )
            statement = (
                judgement.new_statement
                if judgement.action == "edit" and judgement.new_statement else previous
            )
            return {
                "kind": "claim", "action": judgement.action, "claim_id": str(target.id),
                "statement": statement, "review_id": str(review.id),
            }

    test = build_stated_preference_test(label, stated_preference)
    claim = await reconcile_candidate(
        claim_store, embedding_client, learner_id, session_id, interaction_id,
        ClaimCandidate(statement=stated_preference, test=test, value=label, topic="general"),
        contradiction_was_possible=True,
        source=ClaimSource.STATED, write_policy=ClaimWritePolicy.LOCKED,
    )
    was_existing = any(c.id == claim.id for c in candidates)
    if was_existing:
        return {
            "kind": "claim", "action": "supported", "claim_id": str(claim.id),
            "statement": claim.statement, "review_id": None,
        }
    review = await review_store.add(
        claim_id=claim.id, review_type="approve",
        source="sandbox_chat", source_session_id=session_id, source_turn_index=turn_index,
    )
    return {
        "kind": "claim", "action": "created", "claim_id": str(claim.id),
        "statement": claim.statement, "review_id": str(review.id),
    }


def build_undo_review(review: StudentReview) -> dict | None:
    """The inverse of one review row, as kwargs for `ReviewStore.add` (minus
    claim_id/thinking_style_candidate_id, which the caller already has) --
    or None if that review type has nothing meaningful to undo (an
    'approve' is a recorded fact, not a state to reverse)."""
    if review.review_type == "edit":
        return {
            "review_type": "edit",
            "revised_statement": review.previous_statement,
            "previous_statement": review.revised_statement,
        }
    if review.review_type == "archive":
        return {"review_type": "restore"}
    if review.review_type == "restore":
        return {"review_type": "archive"}
    return None
