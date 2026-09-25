"""HTTP + WebSocket API over `SessionLoop` — what the Versa app talks to.

The loop is built through `session_builder.build_session_loop` (the one
shared assembly point), so a chat here gets exactly the personalization,
memory, options and audit trail `versa chat` does. This module adds only
transport:

    GET  /api/health                       liveness + whether the LLM is live or a stub
    POST /api/learners      {label}        get-or-create a learner by name (no passwords yet)
    POST /api/sessions      {learner_id}   start a chat
    GET  /api/learners/{id}/sessions       this learner's chats within one app mode
                                            (the chat-history sidebar), newest-active-first
    GET  /api/learners/{id}/sessions/all   every mode's chats together (the History page)
    GET  /api/learners/{id}/thinking-style confirmed / emerging thinking styles + observed claims
    GET  /api/sessions/{id}/history         one chat's turn-by-turn record, to resume it
    POST /api/sessions/{id}/end            consolidate a finished chat in the background
    Learn a topic (topics.py): /api/topic-explorations[/from-link|/from-pdf],
    /api/topic-nodes/{id}/expand, /api/topics, /api/learners/{id}/topics,
    /api/topics/{id}, /api/lessons/{id}[/start]
    WS   /api/sessions/{id}/chat           one chat, one turn at a time

Chat protocol (JSON text frames).

  client -> server
    {"type": "message", "text": "..."}            a typed message
    {"type": "select_option", "option_id": "..."}  a click on an offered reading
    either may add "stage": true -- the app's stage (the slime) is showing,
    so act this turn out on it (see "stage" below)

  server -> client, per turn, in this order
    {"type": "turn_start", "turn_index": N}
    {"type": "delta", "text": "..."}      0..n pieces of the answer as it is written
    {"type": "options", "message": "...", "options": [{"id", "text"}]}   ambiguous turns
    {"type": "done", "turn_index": N, "kind": "answer" | "options", "text": "...",
     "timing": {"first_output_ms": ..., "total_ms": ...}}
    {"type": "error", "message": "..."}   the turn failed; the socket stays usable

  IDEAS.md "oh wait...": memory is checked alongside the ambiguity check, so
    {"type": "options", ...}   may arrive BEFORE the turn is over (still
                               not clickable until `done`), and then
    {"type": "recalled", "retracted": true|false}   memory knew what the
                               student meant: any options shown are taken
                               back and the answer's deltas follow; `done`
                               then has kind "answer". retracted=false: the
                               options were never shown (memory won first).

  server -> client, only when the turn asked for "stage" and is an ANSWER
  (an options turn has no performance: the slime asks the options instead)
    {"type": "stage_start", "turn_index": N}   at the answer's first word
                                  (generation began when the message arrived)
    {"type": "stage", "turn_index": N, "action": {...}}   0..n, as generated
    {"type": "stage_end", "turn_index": N}
  These run alongside the answer's deltas (see stage.py) and may finish
  after `done`.

  server -> client, lesson chats only (topics.py), when a task was judged
  complete -- usually just after `done`, from the turn's background tail
    {"type": "progress", "lesson_id", "task_id", "lesson_percent",
     "chapter_percent", "topic_percent", "lesson_status"}

`done.text` is authoritative: on a failed answer it replaces whatever
partial deltas were shown. `timing.first_output_ms` is when the student first
saw anything (the first delta, or the options themselves), measured on the
server from the moment the message arrived.

One turn per session at a time. Frames on one socket are handled in order (a
message sent mid-turn simply waits its turn); a SECOND connection messaging the
same session while a turn is running (say, another tab) gets an `error`. A
client that disconnects mid-answer does not lose the turn: the
answer is still produced and persisted (SessionLoop guards its stream sink).
Streaming plus `defer_tail` means the reply is returned as soon as the answer
is ready; the memory write / interaction record / diagnostics finish in the
background, and the session's next turn waits for them.

No authentication: this is a local-development server, bound to 127.0.0.1 by
default. Do not expose it as is.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from versa.answer_versions import AnswerVersionStore
from versa.audit import NodeCallStore, TranscriptStore
from versa.claims import ClaimStore
from versa.disambiguate import DisambiguationStore
from versa.domain_config import DomainConfig
from versa.embeddings import EmbeddingClient
from versa.feed import build_feed_router
from versa.topics import build_topics_router
from versa.learner import LearnerStore
from versa.llm import ModelTierClients
from versa.loop import SessionLoop
from versa.memory import ThinkingStyleStore
from versa.models import (
    ChatSummary,
    ClaimStatus,
    HistoryTurn,
    OptionStatus,
    ThinkingStyleStatus,
)
from versa.reviews import (
    AnswerItemQuestion,
    ExplainItem,
    ExplanationCacheStore,
    ItemQnAStore,
    ReviewStore,
    apply_ask_intent,
    apply_reviews_overlay,
    build_claim_evidence_block,
    build_thinking_style_evidence_block,
    build_undo_review,
    claim_fingerprint,
    render_qna_history,
    thinking_style_fingerprint,
)
from versa.session_builder import build_session_loop
from versa.session_history import reconstruct_session_history
from versa.session_knobs import SessionKnobs
from versa.stage import StageDirector, stage_sink

logger = logging.getLogger(__name__)

# Local dev only: the Flutter web dev server (`flutter run -d edge/chrome`)
# picks a random localhost port, so allow any localhost origin.
LOCAL_ORIGIN_REGEX = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"


class LearnerIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)


class LearnerOut(BaseModel):
    id: UUID
    label: str


class SessionIn(BaseModel):
    learner_id: UUID
    # Only Sandbox is live; the other modes are placeholders in the app.
    mode: Literal["sandbox"] = "sandbox"


class SessionKnobsPatch(BaseModel):
    answer_length: int | None = Field(None, ge=0, le=100)
    depth: int | None = Field(None, ge=0, le=100)


class SessionOut(BaseModel):
    session_id: UUID
    learner_id: UUID
    mode: str
    knobs: SessionKnobs = SessionKnobs()


class EndOut(BaseModel):
    status: Literal["scheduled", "already_consolidated", "too_short"]


class ThinkingStyleItem(BaseModel):
    id: UUID
    summary: str
    status: str
    confirmations: int
    sessions: int
    edited: bool
    archived: bool


class ClaimItem(BaseModel):
    id: UUID
    statement: str
    test: str
    status: str
    confidence: float
    evidence_count: int
    sessions: int
    edited: bool
    archived: bool


class ThinkingStyleOut(BaseModel):
    """Only what is stored, no derived metrics. `promotion_threshold` is how
    many independent sessions must confirm a pattern before it counts."""

    promotion_threshold: int
    confirmed: list[ThinkingStyleItem]
    emerging: list[ThinkingStyleItem]
    retired: list[ThinkingStyleItem]
    claims: list[ClaimItem]


class ReviewIn(BaseModel):
    action: Literal["approve", "edit", "archive", "restore"]
    revised_statement: str | None = None


class ReviewOut(BaseModel):
    id: UUID
    review_type: str
    revised_statement: str | None
    source: str
    created_at: object


class EvidenceItemOut(BaseModel):
    created_at: object
    topic: str
    axis: str | None
    direction: str
    test_fired: bool
    contradiction_was_possible: bool
    session_id: UUID


class ClaimDetailOut(BaseModel):
    id: UUID
    statement: str
    edited: bool
    archived: bool
    test: str
    status: str
    confidence: float
    evidence: list[EvidenceItemOut]
    reviews: list[ReviewOut]


class ThinkingStyleSessionOut(BaseModel):
    index: int
    session_id: UUID
    created_at: object
    topic_preview: str
    confirms: bool | None


class ThinkingStyleDetailOut(BaseModel):
    id: UUID
    summary: str
    edited: bool
    archived: bool
    status: str
    confirmations: int
    sessions: list[ThinkingStyleSessionOut]
    reviews: list[ReviewOut]


class WhyOut(BaseModel):
    explanation: str
    cached: bool


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class AskOut(BaseModel):
    id: UUID
    question: str
    answer: str
    intent: str
    applied_review_id: UUID | None


class QnAOut(BaseModel):
    id: UUID
    question: str
    answer: str
    intent: str
    applied_review_id: UUID | None
    created_at: object


def _describe_applied_change(result, previous_statement: str, applied_review_id: UUID | None) -> str:
    """The confirmation line appended to a chat answer that changed
    something -- built from the SERVER's own before/after values, never the
    model's phrasing of them, so what the student reads is guaranteed to
    match what was actually written to `student_reviews`."""
    if applied_review_id is None:
        return result.answer
    if result.intent == "edit" and result.new_statement:
        return f"{result.answer}\n\nUpdated: {previous_statement!r} -> {result.new_statement!r}"
    if result.intent == "approve":
        return f"{result.answer}\n\nMarked as approved."
    if result.intent == "archive":
        return f"{result.answer}\n\nArchived."
    return result.answer


def create_app(
    pool: asyncpg.Pool,
    tiers: ModelTierClients,
    embedding_client: EmbeddingClient,
    *,
    domain_config: DomainConfig | None = None,
    web_dir: Path | str | None = None,
    llm_mode: Literal["live", "stub"] = "live",
    cors_origin_regex: str = LOCAL_ORIGIN_REGEX,
) -> FastAPI:
    # A session's live websocket `send`, while one is connected -- the
    # sandbox-chat claim-update flow (loop.py's stated-preference
    # matching) uses this to push a `claim_update` event the moment its
    # background step finishes, the same "forward it to whoever's
    # connected, best-effort" spirit `on_node_start` already has a
    # precedent for.
    active_sends: dict[UUID, object] = {}

    def on_claim_update(session_id: UUID, update: dict) -> None:
        send_fn = active_sends.get(session_id)
        if send_fn is not None:
            asyncio.create_task(send_fn({"type": "claim_update", **update}))

    def on_lesson_progress(session_id: UUID, progress: dict) -> None:
        send_fn = active_sends.get(session_id)
        if send_fn is not None:
            asyncio.create_task(send_fn({"type": "progress", **progress}))

    loop: SessionLoop = build_session_loop(
        pool, tiers, embedding_client, domain_config=domain_config,
        on_claim_update=on_claim_update, on_lesson_progress=on_lesson_progress,
    )
    learners = LearnerStore(pool)
    transcript = TranscriptStore(pool)
    disambiguation = DisambiguationStore(pool)
    node_calls = NodeCallStore(pool)
    thinking_styles = ThinkingStyleStore(pool)
    claim_store = ClaimStore(pool)
    review_store = ReviewStore(pool)
    answer_versions = AnswerVersionStore(pool)
    explanation_cache = ExplanationCacheStore(pool)
    qna_store = ItemQnAStore(pool)
    explain_item = ExplainItem(tiers.fast)
    answer_item_question = AnswerItemQuestion(tiers.fast)
    stage_director = StageDirector(tiers.fast)
    # Performances run alongside (and may outlive) their turn; hold a
    # reference so a running one isn't garbage-collected.
    stage_tasks: set[asyncio.Task] = set()
    session_locks: dict[UUID, asyncio.Lock] = {}

    app = FastAPI(title="Versa", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.loop = loop
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api = APIRouter(prefix="/api")

    @api.get("/health")
    async def health() -> dict:
        return {"status": "ok", "llm": llm_mode}

    @api.post("/learners", response_model=LearnerOut)
    async def upsert_learner(body: LearnerIn) -> LearnerOut:
        label = body.label.strip()
        if not label:
            raise HTTPException(status_code=422, detail="label must not be blank")
        learner = await learners.get_by_label(label) or await learners.create(label=label)
        return LearnerOut(id=learner.id, label=learner.label or label)

    def run_consolidation(session_id: UUID) -> None:
        """Hand an ALREADY-CLAIMED session (sessions.consolidated_at set) to a
        background task. Errors are logged, never raised: nothing here may
        fail a request."""

        async def _run() -> None:
            try:
                await loop._await_session_tail(session_id)
                await loop.consolidate_session(session_id)
            except Exception:
                logger.exception("consolidation failed for session %s", session_id)

        loop._fire_background(_run())

    async def sweep_unconsolidated(learner_id: UUID, exclude: UUID | None) -> None:
        """One background task that consolidates the learner's older chats
        one after another -- never all at once, which exhausted the DB pool
        and stalled the new chat's own requests in a live run."""

        async def _run() -> None:
            try:
                for old in await transcript.list_unconsolidated_eligible(
                    learner_id, loop.memory_config.min_turns_for_cli_auto_consolidation, exclude
                ):
                    if not await transcript.claim_for_consolidation(old):
                        continue
                    try:
                        await loop._await_session_tail(old)
                        await loop.consolidate_session(old)
                    except Exception:
                        logger.exception("consolidation failed for session %s", old)
            except Exception:
                logger.exception("consolidation sweep failed for learner %s", learner_id)

        loop._fire_background(_run())

    @api.post("/sessions", response_model=SessionOut)
    async def create_session(body: SessionIn) -> SessionOut:
        if await learners.get(body.learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        session_id = await transcript.create_session(
            body.learner_id,
            ablation_config=loop.ablation_config,
            app_mode=body.mode,
        )
        # a chat whose tab was closed never sent /end: catch it up now
        await sweep_unconsolidated(body.learner_id, exclude=session_id)
        return SessionOut(
            session_id=session_id,
            learner_id=body.learner_id,
            mode=body.mode,
        )

    @api.post("/sessions/{session_id}/end", response_model=EndOut)
    async def end_session(session_id: UUID) -> EndOut:
        """The client is leaving this chat (new chat, another chat, sign-out).
        Consolidation (thinking-style + claims) runs in the background, once,
        and only for chats long enough to carry an order (the same min-turns
        gate the CLI uses)."""
        try:
            await transcript.get_learner_id(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown session") from None
        min_turns = loop.memory_config.min_turns_for_cli_auto_consolidation
        if await transcript.get_turn_count(session_id) < min_turns:
            return EndOut(status="too_short")
        if not await transcript.claim_for_consolidation(session_id):
            return EndOut(status="already_consolidated")
        run_consolidation(session_id)
        return EndOut(status="scheduled")

    @api.get("/sessions/{session_id}/knobs", response_model=SessionKnobs)
    async def get_knobs(session_id: UUID) -> SessionKnobs:
        try:
            return await transcript.get_knobs(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown session") from None

    @api.patch("/sessions/{session_id}/knobs", response_model=SessionKnobs)
    async def patch_knobs(session_id: UUID, body: SessionKnobsPatch) -> SessionKnobs:
        """Change the length and/or depth level (0-100) mid-chat; unset
        fields are kept. Out-of-range values are rejected (422)."""
        try:
            current = await transcript.get_knobs(session_id)
            updated = current.model_copy(update=body.model_dump(exclude_none=True))
            await transcript.set_knobs(session_id, updated)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown session") from None
        return updated

    @api.get("/learners/{learner_id}/sessions", response_model=list[ChatSummary])
    async def list_sessions(learner_id: UUID, mode: str = "sandbox") -> list[ChatSummary]:
        """The chat-history sidebar: this learner's chats within ONE app
        mode, newest-active first (`ChatSummary.last_activity_at`) — a
        chat with no turns yet (just created) still appears, with
        `preview=None`, so "New chat" shows up immediately, not only
        after its first message."""
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        return await transcript.list_session_summaries(learner_id, mode)

    @api.get("/learners/{learner_id}/sessions/all", response_model=list[ChatSummary])
    async def list_all_sessions(learner_id: UUID) -> list[ChatSummary]:
        """Every chat this learner has, across all modes, newest-active first
        (the History page)."""
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        return await transcript.list_session_summaries(learner_id, None)


    async def _latest_session_anchor(session_id: UUID) -> tuple[UUID, int]:
        """A real (session_id, turn_index) to record a one-off node call
        against (invariant 2) -- the item's own last known turn, since
        `why`/`ask` aren't part of any live turn."""
        turns = await transcript.list_turns(session_id)
        return session_id, (turns[-1].turn_index if turns else 0)

    async def _claim_view(claim) -> ClaimDetailOut:
        current = await claim_store.get_current_statement(claim.id)
        base_statement = current.statement if current else claim.statement
        reviews = await review_store.list_for_claim(claim.id)
        overlay = apply_reviews_overlay(base_statement, reviews)
        evidence = await claim_store.list_evidence(claim.id)
        return ClaimDetailOut(
            id=claim.id, statement=overlay.statement, edited=overlay.edited,
            archived=overlay.archived, test=claim.test, status=claim.status.value,
            confidence=round(claim.confidence, 3),
            evidence=[
                EvidenceItemOut(
                    created_at=e.created_at, topic=e.topic,
                    axis=e.axis.value if e.axis else None, direction=e.direction.value,
                    test_fired=e.test_fired, contradiction_was_possible=e.contradiction_was_possible,
                    session_id=e.session_id,
                )
                for e in evidence
            ],
            reviews=[
                ReviewOut(id=r.id, review_type=r.review_type, revised_statement=r.revised_statement,
                          source=r.source, created_at=r.created_at)
                for r in reviews
            ],
        )

    async def _thinking_style_sessions(candidate) -> list[ThinkingStyleSessionOut]:
        out = []
        for i, sid in enumerate(candidate.session_ids):
            turns = await transcript.list_turns(sid)
            preview = turns[0].text if turns else "(no messages)"
            created_at = turns[0].created_at if turns else None
            confirms: bool | None = None
            if i > 0:
                calls = await node_calls.list_calls_for_session(sid, "ConfirmThinkingStyleMatch")
                if calls:
                    confirms = bool(calls[-1].output_json.get("confirms"))
            out.append(ThinkingStyleSessionOut(
                index=i, session_id=sid, created_at=created_at,
                topic_preview=preview, confirms=confirms,
            ))
        return out

    async def _thinking_style_view(candidate) -> ThinkingStyleDetailOut:
        reviews = await review_store.list_for_thinking_style(candidate.id)
        overlay = apply_reviews_overlay(candidate.path_summary, reviews)
        return ThinkingStyleDetailOut(
            id=candidate.id, summary=overlay.statement, edited=overlay.edited,
            archived=overlay.archived, status=candidate.status.value,
            confirmations=candidate.confirmation_count,
            sessions=await _thinking_style_sessions(candidate),
            reviews=[
                ReviewOut(id=r.id, review_type=r.review_type, revised_statement=r.revised_statement,
                          source=r.source, created_at=r.created_at)
                for r in reviews
            ],
        )

    @api.get("/learners/{learner_id}/thinking-style", response_model=ThinkingStyleOut)
    async def get_thinking_style(learner_id: UUID, include_archived: bool = False) -> ThinkingStyleOut:
        """Read-only view of what the memory layer has actually stored --
        EVERY status (the student can approve/edit/archive any of them, not
        just the ones the system already trusts). `include_archived`
        controls only the review-overlay "deleted" state (student-driven);
        it never hides a real system status like `retired`."""
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")

        async def item(c) -> ThinkingStyleItem | None:
            reviews = await review_store.list_for_thinking_style(c.id)
            overlay = apply_reviews_overlay(c.path_summary, reviews)
            if overlay.archived and not include_archived:
                return None
            return ThinkingStyleItem(
                id=c.id, summary=overlay.statement, status=c.status.value,
                confirmations=c.confirmation_count, sessions=len(set(c.session_ids)),
                edited=overlay.edited, archived=overlay.archived,
            )

        styles = await thinking_styles.list_by_learner(learner_id)
        confirmed, emerging, retired = [], [], []
        for c in sorted(styles, key=lambda c: -c.confirmation_count):
            i = await item(c)
            if i is None:
                continue
            if c.status is ThinkingStyleStatus.CONFIRMED:
                confirmed.append(i)
            elif c.status is ThinkingStyleStatus.CANDIDATE:
                emerging.append(i)
            else:
                retired.append(i)

        claims: list[ClaimItem] = []
        for claim in await claim_store.list_for_learner(learner_id):
            current = await claim_store.get_current_statement(claim.id)
            evidence = await claim_store.list_evidence(claim.id)
            reviews = await review_store.list_for_claim(claim.id)
            overlay = apply_reviews_overlay(
                current.statement if current else claim.statement, reviews
            )
            if overlay.archived and not include_archived:
                continue
            claims.append(ClaimItem(
                id=claim.id, statement=overlay.statement, test=claim.test,
                status=claim.status.value, confidence=round(claim.confidence, 3),
                evidence_count=len(evidence), sessions=len({e.session_id for e in evidence}),
                edited=overlay.edited, archived=overlay.archived,
            ))
        claims.sort(key=lambda c: (-c.evidence_count, -c.confidence))
        return ThinkingStyleOut(
            promotion_threshold=loop.memory_config.thinking_style_promotion_threshold,
            confirmed=confirmed, emerging=emerging, retired=retired, claims=claims,
        )

    @api.get("/claims/{claim_id}", response_model=ClaimDetailOut)
    async def get_claim(claim_id: UUID) -> ClaimDetailOut:
        claim = await claim_store.get(claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="unknown claim")
        return await _claim_view(claim)

    @api.get("/thinking-style/candidates/{candidate_id}", response_model=ThinkingStyleDetailOut)
    async def get_thinking_style_candidate(candidate_id: UUID) -> ThinkingStyleDetailOut:
        candidate = await thinking_styles.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="unknown thinking-style candidate")
        return await _thinking_style_view(candidate)

    @api.post("/claims/{claim_id}/review", response_model=ClaimDetailOut)
    async def review_claim(claim_id: UUID, body: ReviewIn) -> ClaimDetailOut:
        """The View/Edit/Approve/Delete actions. "Delete" (action=archive)
        never removes the row -- see reviews.py's module docstring."""
        claim = await claim_store.get(claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="unknown claim")
        if body.action == "edit" and not body.revised_statement:
            raise HTTPException(status_code=422, detail="revised_statement required for edit")
        current = await claim_store.get_current_statement(claim.id)
        previous = current.statement if current else claim.statement
        await review_store.add(
            claim_id=claim_id, review_type=body.action,
            revised_statement=body.revised_statement if body.action == "edit" else None,
            previous_statement=previous if body.action == "edit" else None,
        )
        return await _claim_view(claim)

    @api.post(
        "/thinking-style/candidates/{candidate_id}/review", response_model=ThinkingStyleDetailOut
    )
    async def review_thinking_style(candidate_id: UUID, body: ReviewIn) -> ThinkingStyleDetailOut:
        candidate = await thinking_styles.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="unknown thinking-style candidate")
        if body.action == "edit" and not body.revised_statement:
            raise HTTPException(status_code=422, detail="revised_statement required for edit")
        reviews = await review_store.list_for_thinking_style(candidate_id)
        previous = apply_reviews_overlay(candidate.path_summary, reviews).statement
        await review_store.add(
            thinking_style_candidate_id=candidate_id, review_type=body.action,
            revised_statement=body.revised_statement if body.action == "edit" else None,
            previous_statement=previous if body.action == "edit" else None,
        )
        return await _thinking_style_view(candidate)

    @api.post("/claims/{claim_id}/reviews/{review_id}/undo", response_model=ClaimDetailOut)
    async def undo_claim_review(claim_id: UUID, review_id: UUID) -> ClaimDetailOut:
        claim = await claim_store.get(claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="unknown claim")
        review = await review_store.get(review_id)
        if review is None or review.claim_id != claim_id:
            raise HTTPException(status_code=404, detail="unknown review")
        inverse = build_undo_review(review)
        if inverse is None:
            raise HTTPException(status_code=400, detail=f"{review.review_type} cannot be undone")
        await review_store.add(claim_id=claim_id, **inverse)
        return await _claim_view(claim)

    @api.post(
        "/thinking-style/candidates/{candidate_id}/reviews/{review_id}/undo",
        response_model=ThinkingStyleDetailOut,
    )
    async def undo_thinking_style_review(
        candidate_id: UUID, review_id: UUID
    ) -> ThinkingStyleDetailOut:
        candidate = await thinking_styles.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="unknown thinking-style candidate")
        review = await review_store.get(review_id)
        if review is None or review.thinking_style_candidate_id != candidate_id:
            raise HTTPException(status_code=404, detail="unknown review")
        inverse = build_undo_review(review)
        if inverse is None:
            raise HTTPException(status_code=400, detail=f"{review.review_type} cannot be undone")
        await review_store.add(thinking_style_candidate_id=candidate_id, **inverse)
        return await _thinking_style_view(candidate)

    @api.get("/claims/{claim_id}/why", response_model=WhyOut)
    async def why_claim(claim_id: UUID) -> WhyOut:
        claim = await claim_store.get(claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="unknown claim")
        evidence = await claim_store.list_evidence(claim.id)
        if not evidence:
            return WhyOut(explanation="Not enough evidence yet to explain this claim.", cached=True)
        fingerprint = claim_fingerprint(evidence)
        cached = await explanation_cache.get_cached(
            claim_id=claim.id, thinking_style_candidate_id=None, evidence_fingerprint=fingerprint
        )
        if cached is not None:
            return WhyOut(explanation=cached, cached=True)
        current = await claim_store.get_current_statement(claim.id)
        block = build_claim_evidence_block(
            claim, current.statement if current else claim.statement, evidence
        )
        session_id, turn_index = await _latest_session_anchor(evidence[-1].session_id)
        explanation = await loop._call_node(explain_item, session_id, turn_index, evidence_block=block)
        await explanation_cache.store(
            claim_id=claim.id, thinking_style_candidate_id=None, evidence_fingerprint=fingerprint,
            explanation=explanation, node_call_session_id=session_id, node_call_turn_index=turn_index,
        )
        return WhyOut(explanation=explanation, cached=False)

    @api.get("/thinking-style/candidates/{candidate_id}/why", response_model=WhyOut)
    async def why_thinking_style(candidate_id: UUID) -> WhyOut:
        candidate = await thinking_styles.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="unknown thinking-style candidate")
        fingerprint = thinking_style_fingerprint(candidate)
        cached = await explanation_cache.get_cached(
            claim_id=None, thinking_style_candidate_id=candidate.id, evidence_fingerprint=fingerprint
        )
        if cached is not None:
            return WhyOut(explanation=cached, cached=True)
        sessions = await _thinking_style_sessions(candidate)
        block = build_thinking_style_evidence_block(candidate, [
            {
                "index": s.index, "created_at": s.created_at, "topic_preview": s.topic_preview,
                "path_summary": candidate.path_summary, "confirms": s.confirms,
            }
            for s in sessions
        ])
        session_id, turn_index = await _latest_session_anchor(candidate.session_ids[-1])
        explanation = await loop._call_node(explain_item, session_id, turn_index, evidence_block=block)
        await explanation_cache.store(
            claim_id=None, thinking_style_candidate_id=candidate.id, evidence_fingerprint=fingerprint,
            explanation=explanation, node_call_session_id=session_id, node_call_turn_index=turn_index,
        )
        return WhyOut(explanation=explanation, cached=False)

    @api.get("/claims/{claim_id}/qna", response_model=list[QnAOut])
    async def list_claim_qna(claim_id: UUID) -> list[QnAOut]:
        return [
            QnAOut(id=t.id, question=t.question, answer=t.answer, intent=t.intent,
                   applied_review_id=t.applied_review_id, created_at=t.created_at)
            for t in await qna_store.list_for_claim(claim_id)
        ]

    @api.get("/thinking-style/candidates/{candidate_id}/qna", response_model=list[QnAOut])
    async def list_thinking_style_qna(candidate_id: UUID) -> list[QnAOut]:
        return [
            QnAOut(id=t.id, question=t.question, answer=t.answer, intent=t.intent,
                   applied_review_id=t.applied_review_id, created_at=t.created_at)
            for t in await qna_store.list_for_thinking_style(candidate_id)
        ]

    @api.post("/claims/{claim_id}/qna", response_model=AskOut)
    async def ask_claim(claim_id: UUID, body: AskIn) -> AskOut:
        claim = await claim_store.get(claim_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="unknown claim")
        evidence = await claim_store.list_evidence(claim.id)
        current = await claim_store.get_current_statement(claim.id)
        statement = current.statement if current else claim.statement
        block = build_claim_evidence_block(claim, statement, evidence)
        history = render_qna_history(await qna_store.list_for_claim(claim.id))
        anchor_session = evidence[-1].session_id if evidence else None
        if anchor_session is None:
            raise HTTPException(status_code=409, detail="not enough evidence yet to ask about")
        session_id, turn_index = await _latest_session_anchor(anchor_session)
        result = await loop._call_node(
            answer_item_question, session_id, turn_index,
            evidence_block=block, qna_history_block=history, question=body.question,
        )
        applied_id = None
        answer_text = result.answer
        if result.intent != "none":
            reviews = await review_store.list_for_claim(claim.id)
            live_statement = apply_reviews_overlay(statement, reviews).statement
            record_id = uuid4()
            applied_id = await apply_ask_intent(
                review_store, claim_id=claim.id, thinking_style_candidate_id=None,
                current_statement=live_statement, answer=result, qna_id=record_id,
            )
            answer_text = _describe_applied_change(result, live_statement, applied_id)
        else:
            record_id = uuid4()
        saved = await qna_store.add(
            claim_id=claim.id, thinking_style_candidate_id=None,
            question=body.question, answer=answer_text, intent=result.intent,
            applied_review_id=applied_id, node_call_session_id=session_id,
            node_call_turn_index=turn_index, qna_id=record_id,
        )
        return AskOut(id=saved.id, question=saved.question, answer=saved.answer,
                      intent=saved.intent, applied_review_id=saved.applied_review_id)

    @api.post("/thinking-style/candidates/{candidate_id}/qna", response_model=AskOut)
    async def ask_thinking_style(candidate_id: UUID, body: AskIn) -> AskOut:
        candidate = await thinking_styles.get(candidate_id)
        if candidate is None:
            raise HTTPException(status_code=404, detail="unknown thinking-style candidate")
        sessions = await _thinking_style_sessions(candidate)
        block = build_thinking_style_evidence_block(candidate, [
            {
                "index": s.index, "created_at": s.created_at, "topic_preview": s.topic_preview,
                "path_summary": candidate.path_summary, "confirms": s.confirms,
            }
            for s in sessions
        ])
        history = render_qna_history(await qna_store.list_for_thinking_style(candidate.id))
        session_id, turn_index = await _latest_session_anchor(candidate.session_ids[-1])
        result = await loop._call_node(
            answer_item_question, session_id, turn_index,
            evidence_block=block, qna_history_block=history, question=body.question,
        )
        applied_id = None
        answer_text = result.answer
        if result.intent != "none":
            reviews = await review_store.list_for_thinking_style(candidate.id)
            live_statement = apply_reviews_overlay(candidate.path_summary, reviews).statement
            record_id = uuid4()
            applied_id = await apply_ask_intent(
                review_store, claim_id=None, thinking_style_candidate_id=candidate.id,
                current_statement=live_statement, answer=result, qna_id=record_id,
            )
            answer_text = _describe_applied_change(result, live_statement, applied_id)
        else:
            record_id = uuid4()
        saved = await qna_store.add(
            claim_id=None, thinking_style_candidate_id=candidate.id,
            question=body.question, answer=answer_text, intent=result.intent,
            applied_review_id=applied_id, node_call_session_id=session_id,
            node_call_turn_index=turn_index, qna_id=record_id,
        )
        return AskOut(id=saved.id, question=saved.question, answer=saved.answer,
                      intent=saved.intent, applied_review_id=saved.applied_review_id)

    @api.get("/sessions/{session_id}/history", response_model=list[HistoryTurn])
    async def get_session_history(session_id: UUID) -> list[HistoryTurn]:
        """A chat's turn-by-turn record, for a client reopening it (a
        page reload, or a past chat picked from the sidebar) — see
        session_history.py for exactly how each turn is read back."""
        try:
            await transcript.get_learner_id(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown session") from None
        return await reconstruct_session_history(
            transcript, node_calls, disambiguation, session_id,
            review_store=review_store, claim_store=claim_store,
            answer_versions=answer_versions,
        )

    async def next_turn_index(session_id: UUID) -> int:
        turns = await transcript.list_turns(session_id)
        return max((t.turn_index for t in turns), default=-1) + 1

    @api.websocket("/sessions/{session_id}/chat")
    async def chat(ws: WebSocket, session_id: UUID) -> None:
        try:
            await transcript.get_learner_id(session_id)
        except KeyError:
            await ws.close(code=4404)
            return
        await ws.accept()
        lock = session_locks.setdefault(session_id, asyncio.Lock())
        connected = True

        async def send(event: dict) -> None:
            # A vanished client must never abort the turn (the answer should
            # still be produced and persisted) -- swallow, remember, move on.
            nonlocal connected
            if not connected:
                return
            try:
                await ws.send_json(event)
            except (WebSocketDisconnect, RuntimeError):
                connected = False

        active_sends[session_id] = send
        regen_task: asyncio.Task | None = None
        try:
            while connected:
                data = await ws.receive_json()
                if data.get("type") == "regenerate":
                    # A newer slider position supersedes a rewrite still in
                    # flight: cancel it (it records nothing) and start over.
                    if regen_task is not None and not regen_task.done():
                        regen_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await regen_task
                    if lock.locked():
                        await send({"type": "error", "message": "a turn is already running"})
                        continue
                    regen_task = asyncio.create_task(
                        _locked_regenerate(lock, session_id, data.get("request_id"), send)
                    )
                    continue
                if lock.locked():
                    await send({"type": "error", "message": "a turn is already running"})
                    continue
                async with lock:
                    await _run_turn(session_id, data, send)
        except WebSocketDisconnect:
            return
        finally:
            if active_sends.get(session_id) is send:
                active_sends.pop(session_id, None)

    async def _locked_regenerate(lock: asyncio.Lock, session_id: UUID, request_id, send) -> None:
        async with lock:
            await _run_regenerate(session_id, request_id, send)

    async def _run_regenerate(session_id: UUID, request_id, send) -> None:
        """Rewrite the latest answer at the session's current knob levels,
        streaming it as `regen_delta` events tagged with the client's
        request_id so a client can drop pieces of a superseded rewrite."""
        started = time.monotonic()
        first_output_ms: float | None = None

        async def on_delta(piece: str) -> None:
            nonlocal first_output_ms
            if first_output_ms is None:
                first_output_ms = (time.monotonic() - started) * 1000
            await send({"type": "regen_delta", "request_id": request_id, "text": piece})

        await send({"type": "regen_start", "request_id": request_id})
        try:
            result = await loop.regenerate_last_answer(session_id, on_delta=on_delta)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("regeneration failed for session %s", session_id)
            await send({"type": "regen_error", "request_id": request_id, "message": f"rewrite failed: {exc}"})
            return
        if result is None:
            await send({"type": "regen_skipped", "request_id": request_id})
            return
        turn_index, text, version = result
        total_ms = (time.monotonic() - started) * 1000
        await send({
            "type": "regen_done",
            "request_id": request_id,
            "turn_index": turn_index,
            "version": version,
            "text": text,
            "timing": {
                "first_output_ms": round(first_output_ms if first_output_ms is not None else total_ms),
                "total_ms": round(total_ms),
            },
        })

    class _Performance:
        """One turn's stage performance, generated from the moment the
        message arrives but HELD until the answer's first word: only then is
        it known to be an answer turn (an options turn never shows it -- the
        slime asks the options instead). Starting early is what lets the
        slime move with the answer rather than seconds behind it; the held
        actions are flushed in order on release, then later ones pass
        straight through. A lock keeps a flush and a new action from
        interleaving."""

        def __init__(self, turn_index: int, send) -> None:
            self.turn_index = turn_index
            self._send = send
            self._lock = asyncio.Lock()
            self._held: list[dict] = []
            self._released = False
            self._finished = False

        async def forward(self, action: dict) -> None:
            async with self._lock:
                if self._released:
                    await self._send({"type": "stage", "turn_index": self.turn_index, "action": action})
                else:
                    self._held.append(action)

        async def release(self) -> None:
            async with self._lock:
                if self._released:
                    return
                self._released = True
                await self._send({"type": "stage_start", "turn_index": self.turn_index})
                for action in self._held:
                    await self._send({"type": "stage", "turn_index": self.turn_index, "action": action})
                self._held.clear()
                if self._finished:
                    await self._send({"type": "stage_end", "turn_index": self.turn_index})

        async def finish(self) -> None:
            async with self._lock:
                self._finished = True
                if self._released:
                    await self._send({"type": "stage_end", "turn_index": self.turn_index})

    async def _run_stage(session_id: UUID, turn_index: int, message: str, perf: _Performance) -> None:
        """Generate this turn's performance (stage.py) into `perf`. Best
        effort: a failure here costs the slime its skit, never the answer.
        Runs to completion even if the turn turns out to be an options turn,
        so the call is still recorded to node_calls (invariant 2)."""
        stage_sink.set(perf.forward)  # this task's own context only
        try:
            await loop._call_node(stage_director, session_id, turn_index, message=message)
        except Exception:
            logger.warning("stage direction failed on turn %d for session %s",
                           turn_index, session_id, exc_info=True)
        finally:
            await perf.finish()

    async def _run_turn(session_id: UUID, data: dict, send) -> None:
        started = time.monotonic()
        kind = data.get("type")
        selected_option_id: UUID | None = None
        if kind == "message":
            text = str(data.get("text", "")).strip()
            if not text:
                await send({"type": "error", "message": "empty message"})
                return
        elif kind == "select_option":
            try:
                option = await disambiguation.get_option(UUID(str(data.get("option_id"))))
            except ValueError:
                option = None
            if option is None or option.session_id != session_id:
                await send({"type": "error", "message": "unknown option"})
                return
            if option.status is not OptionStatus.OPEN:
                # a double-tap, or a button from a set already resolved/superseded
                await send({"type": "error", "message": "that option is no longer available"})
                return
            text, selected_option_id = option.text, option.id
        else:
            await send({"type": "error", "message": f"unknown message type {kind!r}"})
            return

        turn_index = await next_turn_index(session_id)
        await send({"type": "turn_start", "turn_index": turn_index})
        first_output_ms: float | None = None
        perf: _Performance | None = None
        if data.get("stage"):
            perf = _Performance(turn_index, send)
            task = asyncio.create_task(_run_stage(session_id, turn_index, text, perf))
            stage_tasks.add(task)
            task.add_done_callback(stage_tasks.discard)

        async def on_delta(piece: str) -> None:
            nonlocal first_output_ms
            if first_output_ms is None:
                first_output_ms = (time.monotonic() - started) * 1000
                # The answer has started, so this is an answer turn, not
                # an options turn: let the performance out.
                if perf is not None:
                    await perf.release()
            await send({"type": "delta", "text": piece})

        options_sent = False

        async def on_event(event: dict) -> None:
            # Mid-turn events from the loop (loop.py `_emit_turn_event`):
            # options shown before memory has had its say, and the
            # "I remember" beat -- with or without retracting them.
            nonlocal first_output_ms, options_sent
            if event.get("type") == "options":
                options_sent = True
                if first_output_ms is None:
                    first_output_ms = (time.monotonic() - started) * 1000
            await send(event)

        try:
            message = await loop.handle_turn(
                session_id, turn_index, text, selected_option_id,
                on_delta=on_delta, on_event=on_event, defer_tail=True,
            )
            options = await loop.pending_options(session_id)
        except Exception as exc:
            logger.exception("turn %d failed for session %s", turn_index, session_id)
            await send({"type": "error", "message": f"the turn failed: {exc}"})
            return

        total_ms = (time.monotonic() - started) * 1000
        if options and not options_sent:
            await send({
                "type": "options",
                "message": message,
                "options": [{"id": str(o.id), "text": o.text} for o in options],
            })
        await send({
            "type": "done",
            "turn_index": turn_index,
            "kind": "options" if options else "answer",
            "text": message,
            "timing": {
                "first_output_ms": round(first_output_ms if first_output_ms is not None else total_ms),
                "total_ms": round(total_ms),
            },
        })

    app.include_router(api)
    app.include_router(build_feed_router(pool, tiers.fast))
    app.include_router(build_topics_router(
        pool, tiers.fast, loop._embedding_client, ablation_config=loop.ablation_config,
    ))

    # The built Flutter web app, if there is one, at "/" -- registered last so
    # it can never shadow /api. One command, one URL.
    if web_dir is not None and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app
