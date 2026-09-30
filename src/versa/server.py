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
    GET  /api/learners/{id}/style-patterns how they move through ideas, from their own
                                          choices (style_patterns.py), every gate shown
    GET  /api/sessions/{id}/history         one chat's turn-by-turn record, to resume it
    POST /api/sessions/{id}/end            consolidate a finished chat in the background
    Notes (notes.py): GET /api/sessions/{id}/notes (latest notes, answers so far,
    up to date?), POST /api/sessions/{id}/notes (write them -- only when the
    learner asks; priced), GET /api/sessions/{id}/notes/{note_id}/pdf
    Learn a topic (topics.py): /api/topic-explorations[/from-link|/from-pdf],
    /api/topic-nodes/{id}/expand, /api/topics, /api/learners/{id}/topics,
    /api/topics/{id}, /api/lessons/{id}[/start|/activity|/activity-result]
    (activity: a tap-to-answer quiz on the lesson's current point, acted out
    on the stage and shown in the chat; activity-result grades the tap)
    Exam prep (exams.py): /api/exams[/from-link|/from-pdf|/from-course],
    /api/learners/{id}/exams, /api/exams/{id}, /api/exams/{id}/mock,
    /api/exam-units/{id}/quiz, /api/exam-quizzes/{id}[/submit],
    /api/exams/{id}/plan (GET, POST), /api/exam-plan-items/{id}/done
    Stage quick checks: the client sends {"type": "stage_check", "turn_index",
    "question", "choices", "picked", "answer"} when the student answers one;
    the server keeps it (stage_checks) and replies with nothing.
    Directions (directions.py): a message, pick or regenerate may add
    "directions": "fork" (links the answer ends with) or true (cards) --
    the client shows them. A fork pick adds "continue": true, so the answer
    carries on instead of starting over. Then, after an answer, the server sends
    {"type": "directions", "turn_index", "set_id", "cards": [{"id", "text"}]}
    (display order); the client sends {"type": "direction", "card_id"} to
    take one, which runs as the next turn. Versa guessed the pick before the
    set went out (pick_prediction.py); right after `turn_start` for a pick
    it sends {"type": "guess", "turn_index", "hit", "predicted", "picked",
    "hits", "guesses", "picks_seen", "because": [lines]}. A directions frame
    is a HAND of three dealt from a pool (directions.py lib-v2) and carries
    "deal_index"; the client sends {"type": "more_directions", "set_id"} for
    "other directions" and gets the next hand as another directions frame
    (same turn_index), or {"type": "directions_exhausted", "turn_index"}.
    Rooms (rooms/, experimental): /api/rooms[/from-pdf], /api/rooms/{code}/join,
    /api/rooms/{code}/state, /api/rooms/summaries, WS /api/rooms/{code}/ws
    Billing (billing.py): GET /api/learners/{id}/billing (plan, Plus expiry,
    Exam Pass window), POST /api/learners/{id}/billing/sync {exam_id?} (right
    after a purchase), POST /api/billing/revenuecat/webhook (RevenueCat)
    Sparks (sparks.py): GET /api/learners/{id}/sparks -- balance, plan, costs,
    next refill and recent events. Priced HTTP actions answer 402 with
    {"detail": {"reason": "sparks", "action", "needed", "balance", "tier",
    "next_refill_at"}} when the balance is too low.
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
    {"type": "paywall", "reason": "sparks", "action": "answer", "needed",
     "balance", "tier", "next_refill_at"}  not enough Sparks: the turn did not
                                          run (sent instead of turn_start)
    {"type": "sparks", "balance": N, "spent": N}   just before `done` on an
                                          answer turn. Options turns cost nothing.

  IDEAS.md "oh wait...": memory is checked alongside the ambiguity check, so
    {"type": "options", ...}   may arrive BEFORE the turn is over (still
                               not clickable until `done`), and then
    {"type": "recalled", "retracted": true|false}   memory knew what the
                               student meant: any options shown are taken
                               back and the answer's deltas follow; `done`
                               then has kind "answer". retracted=false: the
                               options were never shown (memory won first).
    {"type": "adapted", "path": [labels], "because": [lines]}   before the
                               answer's deltas, once the learner's usual way
                               into an idea is clear from their own direction
                               picks (pick_prediction.py): the answer starts
                               that way. Never on a direction pick or lesson.

  server -> client, only when the turn asked for "stage" and is an ANSWER
  (an options turn has no performance: the slime asks the options instead)
    {"type": "stage_start", "turn_index": N}   once the turn commits to answering
                                  (generation began when the message arrived)
    {"type": "stage", "turn_index": N, "action": {...}}   0..n, as generated
    {"type": "stage_end", "turn_index": N}
  These run alongside the answer's deltas (see stage.py) and may finish
  after `done`.

  server -> client, lesson chats only (topics.py), when a task was judged
  (or a point's quiz answered right, POST /api/lessons/{id}/activity-result)
  complete -- usually just after `done`, from the turn's background tail
    {"type": "progress", "lesson_id", "task_id", "lesson_percent",
     "chapter_percent", "topic_percent", "lesson_status"}
  and, when that completes the lesson, the reward it earned
    {"type": "sparks_reward", "reason": "lesson_completed", "amount": N}
  (the same event, reason "study_streak", follows `sparks` on the answer that
  completes a run of study days, also before `done`)

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
import html
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.requests import HTTPConnection
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from versa import chatter as _chatter
from versa import directions as _directions
from versa import images as _images
from versa import notes as _notes
from versa import pick_prediction as _pick_prediction
from versa import style_patterns as _style_patterns
from versa.accounts import (
    AccountStore,
    Auth,
    build_auth_router,
    build_guard,
    current_learner,
    ws_subprotocol,
)
from versa.answer_versions import AnswerVersionStore
from versa.audit import NodeCallStore, TranscriptStore
from versa.billing import Billing, build_billing_router
from versa.claims import ClaimStore
from versa.disambiguate import DisambiguationStore
from versa.domain_config import DomainConfig
from versa.embeddings import EmbeddingClient
from versa.exams import build_exams_router
from versa.feed import build_feed_router
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
from versa.profiles import ProfileStore, build_profiles_router
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
from versa.rooms import RoomHub, build_rooms_router
from versa.session_builder import build_session_loop
from versa.session_history import reconstruct_session_history
from versa.knob_events import KnobEventStore
from versa.session_knobs import SessionKnobs
from versa.sparks import InsufficientSparks, SparkEngine, build_sparks_router
from versa.stage import StageCheckStore, StageDirector, stage_sink
from versa.topics import build_topics_router

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
    breadth: int | None = Field(None, ge=0, le=100)


class SessionOut(BaseModel):
    session_id: UUID
    learner_id: UUID
    mode: str
    knobs: SessionKnobs = SessionKnobs()


class EndOut(BaseModel):
    status: Literal["scheduled", "already_consolidated", "too_short"]


class StylePatternsOut(BaseModel):
    version: str
    patterns: list[_style_patterns.StylePattern]
    # where every card type sits (directions.py): family and its place on the
    # four axes -- what the constellation places stars by
    space: dict[str, dict] = {}
    # experimenting on a miss (style_patterns.miss_follow_through): misses,
    # how many were read as a way out, and what happened when a later hand
    # offered that way -- taken, and held in a later chat
    misses: dict[str, int] = {}
    # this learner's own new moves: what they asked for that no card type
    # covers (StyleReader.learner_moves)
    new_moves: list[dict] = []


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


def render_invite_page(code: str, problem: str | None, android_url: str | None) -> str:
    """The page an invite link opens (GET /invite/{code}). Versa is shown as a
    phone app, so this page only hands over the code and the download."""
    safe_code = html.escape(code.upper())
    if problem is not None:
        body = f"<h1>This invite can't be used</h1><p>{html.escape(problem)}</p>" \
               "<p>Ask the person who invited you for a new link.</p>"
    else:
        download = (
            f'<a class="button" href="{html.escape(android_url, quote=True)}">Get Versa for Android</a>'
            if android_url else "<p>The person who invited you will send you the app.</p>"
        )
        body = (
            "<h1>You're invited to Versa</h1>"
            "<p>Learn, how you think.</p>"
            f"{download}"
            "<p>Open the app, sign in with Google or email, and enter this invite code:</p>"
            f'<p class="code">{safe_code}</p>'
            "<p class=\"small\">The code works once per person. Keep it to yourself.</p>"
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Versa invite</title><style>"
        ":root{--bg:#f7f4ee;--fg:#1d1b18;--muted:#6b645a;--accent:#3d5a80;--card:#fff}"
        "@media (prefers-color-scheme: dark){:root{--bg:#161513;--fg:#eee9e0;--muted:#a39b8f;"
        "--accent:#8fb0d9;--card:#201e1b}}"
        "body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}"
        "main{max-width:440px;margin:0 auto;padding:48px 16px}"
        ".card{background:var(--card);border-radius:16px;padding:28px}"
        "h1{font-size:24px;margin:0 0 8px}p{color:var(--muted)}"
        ".code{font:600 28px/1.2 ui-monospace,monospace;letter-spacing:4px;color:var(--fg);"
        "text-align:center;padding:12px;border:1px dashed var(--muted);border-radius:12px}"
        ".button{display:block;text-align:center;background:var(--accent);color:#fff;"
        "padding:14px;border-radius:12px;text-decoration:none;font-weight:600;margin:20px 0}"
        ".small{font-size:13px}</style></head>"
        f"<body><main><div class=\"card\">{body}</div></main></body></html>"
    )


def create_app(
    pool: asyncpg.Pool,
    tiers: ModelTierClients,
    embedding_client: EmbeddingClient,
    *,
    domain_config: DomainConfig | None = None,
    web_dir: Path | str | None = None,
    llm_mode: Literal["live", "stub"] = "live",
    cors_origin_regex: str = LOCAL_ORIGIN_REGEX,
    sparks: SparkEngine | None = None,
    billing: Billing | None = None,
    auth: Auth | None = None,
    android_download_url: str | None = None,
) -> FastAPI:
    """`auth` (accounts.py) turns on sign-in: every /api route but health,
    sign-in and the RevenueCat webhook then needs a session token, and may
    only touch the signed-in learner's own things. None -- the test suite,
    and `versa serve` with VERSA_AUTH=off on a laptop -- is the old open
    server with name-only `POST /api/learners`."""
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

    # Sparks (sparks.py): what real work costs, and what learning earns back.
    # `VERSA_SPARKS=off` turns charging off. Billing (billing.py) connects
    # RevenueCat: which plan a learner is on, bought Spark packs, Exam Passes.
    # Without REVENUECAT_SECRET_KEY it is off and everyone is Free.
    if billing is None:
        billing = Billing.from_env(pool) if sparks is None else Billing(pool, None)
    if sparks is None:
        sparks = SparkEngine.from_env(pool, billing.tiers)
    billing.sparks = sparks
    background: set[asyncio.Task] = set()

    def on_lesson_progress(session_id: UUID, progress: dict) -> None:
        send_fn = active_sends.get(session_id)
        if send_fn is not None:
            asyncio.create_task(send_fn({"type": "progress", **progress}))
        if progress.get("lesson_status") == "done":
            task = asyncio.create_task(_reward_lesson(session_id, progress))
            background.add(task)
            task.add_done_callback(background.discard)

    async def _reward_lesson(session_id: UUID, progress: dict) -> None:
        try:
            learner_id = await transcript.get_learner_id(session_id)
            amount = await sparks.reward(
                learner_id, "lesson_completed", f"lesson:{progress['lesson_id']}",
                ref={"lesson_id": progress["lesson_id"], "session_id": session_id},
            )
        except Exception:
            logger.exception("lesson reward failed for session %s", session_id)
            return
        send_fn = active_sends.get(session_id)
        if amount and send_fn is not None:
            await send_fn({"type": "sparks_reward", "reason": "lesson_completed", "amount": amount})

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
    stage_director = StageDirector(tiers.stage or tiers.fast)
    # Pictures attached to messages (images.py): read once on upload; a turn
    # gets the reading with its message.
    images_router = _images.build_images_router(pool, tiers.fast)
    image_store = images_router.image_store
    stage_checks = StageCheckStore(pool)
    knob_events = KnobEventStore(pool)
    # Performances run alongside (and may outlive) their turn; hold a
    # reference so a running one isn't garbage-collected.
    stage_tasks: set[asyncio.Task] = set()
    # "Where this could go" (directions.py): offered after every answer.
    direction_store = _directions.DirectionStore(pool)
    # Versa's guess at each pick, made before the cards go out (pick_prediction.py).
    pick_predictions = _pick_prediction.PredictionStore(pool)
    direction_tasks: set[asyncio.Task] = set()
    # session -> the turn most recently started here. Held in memory because
    # a turn's own row is written in its deferred tail, so reading turns back
    # can lag behind what the learner has actually done.
    latest_turn: dict[UUID, int] = {}
    # session -> (turn, message, answer, presentation) of the latest answer the
    # directions were offered for: "other directions" needs them to write a
    # fresh pool once one is spent. In memory only -- after a restart a spent
    # pool just answers `directions_exhausted`.
    offer_context: dict[UUID, tuple[int, str, str, str]] = {}
    # session -> the text of its latest answer (or options question): whether
    # it ended by asking the learner something decides if an "ok" is an
    # answer or just chatter (chatter.py). In memory only.
    last_answer: dict[UUID, str] = {}
    session_locks: dict[UUID, asyncio.Lock] = {}

    guard = build_guard(auth, pool)
    profiles = ProfileStore(pool)
    accounts = AccountStore(pool)
    show_docs = auth is None or os.environ.get("VERSA_API_DOCS", "").lower() == "on"
    app = FastAPI(
        title="Versa",
        docs_url="/api/docs" if show_docs else None,
        openapi_url="/api/openapi.json" if show_docs else None,
        # Every route runs the guard first (accounts.py); it lets non-/api
        # paths (the web app, the invite page) and the public routes through.
        dependencies=[Depends(guard)],
    )
    app.state.auth = auth
    app.state.loop = loop
    app.state.sparks = sparks
    app.state.billing = billing
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    api = APIRouter(prefix="/api")

    @api.get("/health")
    async def health() -> dict:
        # `auth` tells the app which sign-in screen to draw (accounts.py).
        return {
            "status": "ok", "llm": llm_mode,
            "auth": auth.public_config() if auth is not None else {"required": False},
        }

    if auth is None:
        # The open server's name-only sign-in. With sign-in on, the dev
        # testers use POST /api/auth/dev instead and this route doesn't exist.
        @api.post("/learners", response_model=LearnerOut)
        async def upsert_learner(body: LearnerIn) -> LearnerOut:
            label = body.label.strip()
            if not label:
                raise HTTPException(status_code=422, detail="label must not be blank")
            learner = await learners.get_by_label(label) or await learners.create(label=label)
            return LearnerOut(id=learner.id, label=learner.label or label)

    @api.get("/me")
    async def me(conn: HTTPConnection) -> dict:
        """Who this token belongs to (the app checks its saved sign-in with
        this on start). Only with sign-in on."""
        if auth is None:
            raise HTTPException(status_code=404, detail="sign-in is off on this server")
        learner_id = current_learner(conn)
        learner = await learners.get(learner_id)
        if learner is None:
            raise HTTPException(status_code=401, detail="sign in first")
        identity = await accounts.identity_for_learner(learner_id)
        return {
            "learner": {"id": str(learner_id),
                        "label": (await profiles.display_name(learner_id)) or learner.label or "Learner"},
            "profile_complete": await profiles.has_profile(learner_id),
            "email": identity["email"] if identity is not None else None,
            "sign_in_method": identity["sign_in_method"] if identity is not None else None,
        }

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
        """Change the length, depth and/or breadth level (0-100) mid-chat; unset
        fields are kept. Out-of-range values are rejected (422). A move that
        changed anything is also kept in `knob_events` (the session row only
        holds the current levels)."""
        try:
            current = await transcript.get_knobs(session_id)
            updated = current.model_copy(update=body.model_dump(exclude_none=True))
            await transcript.set_knobs(session_id, updated)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown session") from None
        await knob_events.record(
            session_id=session_id, turn_count=await transcript.get_turn_count(session_id),
            before=current, after=updated,
        )
        _style_patterns.forget(await transcript.get_learner_id(session_id))
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

    @api.get("/learners/{learner_id}/style-patterns", response_model=StylePatternsOut)
    async def get_style_patterns(learner_id: UUID) -> StylePatternsOut:
        """How this learner moves through ideas, read off their own choices
        (style_patterns.py, docs/THINKING_STYLE.md layer 3): confirmed,
        emerging and fading patterns, each with every gate and its numbers.
        Derived on read; no model call."""
        if await learners.get(learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        patterns = await _style_patterns.StyleReader(pool).patterns(learner_id)
        space = {
            slot: {"family": _directions.FAMILY_OF[slot], "coords": list(_directions.COORDS[slot]),
                   "label": _pick_prediction.slot_label(slot)}
            for slot in _directions.SLOTS
        }
        reader = _style_patterns.StyleReader(pool)
        return StylePatternsOut(version=_style_patterns.STYLE_VERSION, patterns=patterns, space=space,
                                misses=await reader.follow_through(learner_id),
                                new_moves=await reader.learner_moves(learner_id))

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

        # The old free-text detector is retired (2026-09-30): its past
        # candidates stay on record but are no longer shown -- the thinking
        # style is GET .../style-patterns. Only claims are listed here.
        styles: list = []
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
        await ws.accept(subprotocol=ws_subprotocol(ws))
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
                if data.get("type") == "stage_check":
                    # the student answered the stage's quick check: keep it
                    await _record_stage_check(session_id, data, send)
                    continue
                if data.get("type") == "more_directions":
                    if lock.locked():
                        await send({"type": "error", "message": "a turn is already running"})
                        continue
                    async with lock:
                        await _more_directions(session_id, data, send)
                    continue
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
                        _locked_regenerate(lock, session_id, data.get("request_id"), send,
                                           _presentation(data))
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

    async def _record_stage_check(session_id: UUID, data: dict, send) -> None:
        choices = [
            {"id": str(c.get("id", ""))[:24], "text": str(c.get("text", ""))[:60]}
            for c in (data.get("choices") or [])[:3] if isinstance(c, dict)
        ]
        picked = str(data.get("picked") or "")
        if not picked or picked not in {c["id"] for c in choices}:
            await send({"type": "error", "message": "that answer isn't one of the choices"})
            return
        answer = data.get("answer")
        await stage_checks.record(
            session_id=session_id, turn_index=int(data.get("turn_index") or 0),
            question=str(data.get("question") or ""), choices=choices, picked_id=picked,
            answer_id=str(answer) if answer is not None and str(answer) in {c["id"] for c in choices} else None,
        )

    async def _locked_regenerate(
        lock: asyncio.Lock, session_id: UUID, request_id, send, presentation: str | None = None,
    ) -> None:
        async with lock:
            await _run_regenerate(session_id, request_id, send, presentation)

    async def _run_regenerate(session_id: UUID, request_id, send, presentation: str | None = None) -> None:
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
        if presentation:
            # The window moved (the pad or a slider): the directions under
            # this answer are re-pitched for it. The rewritten turn is the
            # latest one by definition (a resumed chat may not have it noted).
            latest_turn.setdefault(session_id, turn_index)
            question = next(
                (t.text for t in await transcript.list_turns(session_id) if t.turn_index == turn_index), text,
            )
            task = asyncio.create_task(
                _offer_directions(session_id, turn_index, question, text, send, presentation)
            )
            direction_tasks.add(task)
            task.add_done_callback(direction_tasks.discard)

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
            self._discarded = False

        @property
        def released(self) -> bool:
            return self._released

        async def discard(self) -> None:
            """The turn asked options instead of answering: this skit acted
            out a guess, so it is never shown (the director's call still
            finishes and is recorded -- invariant 2 -- it just goes nowhere)."""
            async with self._lock:
                if not self._released:
                    self._discarded = True
                    self._held.clear()

        async def forward(self, action: dict) -> None:
            async with self._lock:
                if self._discarded:
                    return
                if self._released:
                    await self._send({"type": "stage", "turn_index": self.turn_index, "action": action})
                else:
                    self._held.append(action)

        async def release(self) -> None:
            async with self._lock:
                if self._released or self._discarded:
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

    class _StageRun:
        """One turn's stage: ONE continuous animation, written from the moment
        the student sends -- alongside the answer, not after it (2026-09-27:
        "make them load in like 2-3 sec"). The director gets the question,
        the previous answer for continuity, and for a fork continuation the
        direction tapped. It is SHOWN once the turn commits to answering
        (the loop's "answering" event; the held beats flush at once) and
        never on a turn that asks options instead (2026-09-28: the options
        used to stop a skit already playing, and the pick then restarted it
        from scratch). Best effort: a failure costs the slime its skit,
        never the answer."""

        def __init__(self, session_id: UUID, turn_index: int, message: str, perf,
                     continues: str | None, photo: bool = False) -> None:
            self.session_id, self.turn_index, self.message = session_id, turn_index, message
            self.perf, self.continues, self.photo = perf, continues, photo
            self.task = asyncio.create_task(self._work())
            stage_tasks.add(self.task)
            self.task.add_done_callback(stage_tasks.discard)

        async def _work(self) -> None:
            stage_sink.set(self.perf.forward)  # this task's own context only
            try:
                kwargs: dict = {"message": self.message, "live": True}
                if self.turn_index > 0:
                    previous = await node_calls.get_call_for_turn(
                        self.session_id, self.turn_index - 1, "FinalAnswer")
                    if previous and isinstance(previous.output_json, str):
                        kwargs["previous_answer"] = previous.output_json
                if self.continues:
                    kwargs["continues"] = self.continues
                if self.photo:
                    # the learner's own picture: the slime can hold it up
                    kwargs["photo"] = True
                hooks = loop.lesson_hooks
                if hooks is not None:
                    # a lesson: act out the point being taught, whatever was typed
                    note = await hooks.stage_note(self.session_id)
                    if note:
                        kwargs["lesson"] = note
                await loop._call_node(stage_director, self.session_id, self.turn_index, **kwargs)
            except Exception:
                logger.warning("stage direction failed on turn %d for session %s",
                               self.turn_index, self.session_id, exc_info=True)
            finally:
                await self.perf.finish()

    def _presentation(data: dict) -> str | None:
        """What a client says it shows under answers: "fork" (links the
        answer ends with), "compass" (one card per family around the
        answer), any other truthy value = cards ("strip"), or nothing --
        then no set is made at all."""
        wanted = data.get("directions")
        if wanted in ("fork", "compass"):
            return wanted
        return "strip" if wanted else None

    async def _guess_pick(session_id: UUID, set_id: UUID) -> None:
        """Record Versa's guess at which card will be taken, BEFORE the set is
        sent (pick_prediction.py). Arithmetic only, so it costs milliseconds;
        best effort -- a failure costs the guess, never the directions."""
        try:
            learner_id = await transcript.get_learner_id(session_id)
            if learner_id is not None:
                await pick_predictions.predict_for_set(
                    learner_id=learner_id, session_id=session_id, set_id=set_id,
                )
        except Exception:
            logger.warning("pick prediction failed for set %s", set_id, exc_info=True)

    async def _reveal_guess(learner_id: UUID, set_id: UUID, picked_slot: str, turn_index: int, send) -> None:
        """After a pick: tell the learner whether Versa guessed it, its recent
        record, and what the guess rested on -- they see it learn them."""
        try:
            prediction = await pick_predictions.get(set_id)
            if prediction is None:
                return
            hits, guesses = await pick_predictions.record(learner_id)
            await send({
                "type": "guess",
                "turn_index": turn_index,
                "hit": prediction.predicted_slot == picked_slot,
                "predicted": _pick_prediction.slot_label(prediction.predicted_slot),
                "picked": _pick_prediction.slot_label(picked_slot),
                "hits": hits,
                "guesses": guesses,
                "picks_seen": prediction.evidence_count,
                "because": _pick_prediction.explain(prediction),
            })
        except Exception:
            logger.warning("revealing the guess failed for set %s", set_id, exc_info=True)

    async def _deal(
        session_id: UUID, turn_index: int, knobs, pool_id: UUID, pool_cards: dict,
        deal_index: int, presentation: str, send,
    ) -> bool:
        """Deal a hand from a pool (directions.deal_hand: one card per family,
        at random; sometimes one card swapped for the pool's path or wild
        card), record Versa's guess before it goes out, and send it. False
        when the pool has nothing left to deal."""
        dealt = await direction_store.dealt_slots(pool_id)
        library = [k for k in pool_cards if k in _directions.SLOTS]
        compass = presentation == "compass"
        hand = _directions.deal_hand(library, dealt, _directions.COMPASS_HAND_SIZE if compass else _directions.HAND_SIZE)
        # the compass has one point per family: no path or wild card in it
        extras_left = set() if compass else {e for e in _directions.EXTRAS if e in pool_cards and e not in dealt}
        if "wild" in extras_left and not pool_cards.get("~wild_tag"):
            extras_left.discard("wild")  # a wild card is only offered once it has a place in the space
        # right after a miss in this chat, the next answer's first hand is
        # widened at random (an extra for certain): experimenting on a miss
        widen = not compass and deal_index == 0 and await direction_store.last_was_miss(session_id)
        hand = _directions.with_extras(hand, extras_left, widen=widen)
        if not hand:
            return False
        extras = {}
        if "wild" in hand:
            extras["wild"] = {"tagged_as": pool_cards["~wild_tag"]}
        if "path" in hand:
            first, second = pool_cards["~path_slots"].split(">")
            # a path pick is read as choosing its first step; the order is on the card
            extras["path"] = {"tagged_as": first, "path_slots": f"{first}>{second}"}
        direction_set = await direction_store.add_set(
            session_id=session_id, turn_index=turn_index, knobs=knobs,
            cards={slot: pool_cards[slot] for slot in hand},
            positions=_directions.shuffled_positions(slots=hand), presentation=presentation,
            pool_id=pool_id, deal_index=deal_index, extras=extras,
            # on record only when the widening happened (the pool had an extra left)
            experiment="after_miss" if widen and any(e in hand for e in _directions.EXTRAS) else None,
        )
        await _guess_pick(session_id, direction_set.id)
        await send({
            "type": "directions",
            "turn_index": turn_index,
            "set_id": str(direction_set.id),
            "presentation": presentation,
            "deal_index": deal_index,
            # the family places a card on the compass (and colours it anywhere)
            "cards": [{"id": str(c.id), "text": c.text,
                       "family": _directions.FAMILY_OF.get(c.tagged_as or c.slot)}
                      for c in direction_set.cards],
        })
        return True

    async def _offer_directions(
        session_id: UUID, turn_index: int, message: str, answer: str, send, presentation: str = "strip",
        deal_index: int = 0,
    ) -> None:
        """After an answer: "where this could go" (directions.py). A pool of
        card types is drawn at random from the library (two per family), plus
        a random two-step path and one wild card, written in one model call
        pitched inside the session's depth/breadth window and kept whole
        (direction_pools); a hand of three is dealt from it. The wild card is
        tagged to its nearest library type by embedding -- blind to the
        learner. Best effort -- a failure costs the cards, never the answer.
        Cards that arrive after the learner already moved on are not stored:
        they were never shown, so they can't be evidence of anything."""
        offer_context[session_id] = (turn_index, message, answer, presentation)
        try:
            knobs = await transcript.get_knobs(session_id)
            taken = await direction_store.taken_texts(session_id)
            slots = _directions.draw_pool()
            route = _directions.draw_path(slots)
            cards = await loop._call_node(
                loop.suggest_directions, session_id, turn_index,
                message=message, answer=answer, depth=knobs.depth, breadth=knobs.breadth,
                slots=slots, path=list(route), wild=True,
                # passed only once they have taken one, so the first set is unchanged
                **({"path_so_far": taken} if taken else {}),
            )
            if not cards or latest_turn.get(session_id) != turn_index:
                return  # the learner already moved on: these cards were never shown
            pool_cards: dict = dict(cards)
            if "path" in pool_cards:
                pool_cards["~path_slots"] = f"{route[0]}>{route[1]}"
            if "wild" in pool_cards:
                try:
                    pool_cards["~wild_tag"] = await _directions.tag_wild(pool_cards["wild"], loop._embedding_client)
                except Exception:
                    logger.warning("tagging a wild card failed for session %s", session_id, exc_info=True)
            pool_id = await direction_store.add_pool(session_id=session_id, turn_index=turn_index, cards=pool_cards)
            await _deal(session_id, turn_index, knobs, pool_id, pool_cards, deal_index, presentation, send)
        except Exception:
            logger.warning("directions failed on turn %d for session %s", turn_index, session_id,
                           exc_info=True)

    async def _keep_miss(session_id: UUID, set_id: UUID, turn_index: int, question: str, earlier: str) -> None:
        """A miss (build item 8): they passed every card by asking their own
        question -- the one time Versa sees what was in their mind when none
        of its cards matched. Kept with the card type it is nearest to (by
        embedding, no model call), unless it moved to a new subject. When the
        embedding can't place it, one fast model call reads what kind of move
        it makes (directions.ReadMiss): one of the types after all, or a new
        move -- kept with its embedding, so moves the cards don't offer can
        be found (style_patterns.discover_moves). In the background: it never
        holds up the answer."""
        try:
            recorder = loop._interaction_recorder
            follow_up = (await recorder.same_subject(session_id, turn_index, question)
                         if recorder is not None else None)
            ranked = await _directions.nearest_types(question, loop._embedding_client)
            miss = await direction_store.add_miss(set_id=set_id, question=question, follow_up=follow_up,
                                                  ranked=ranked)
            if miss is None or miss.tagged_as is not None or miss.follow_up is False:
                return
            reading = await loop._call_node(loop.read_miss, session_id, turn_index, earlier=earlier,
                                            question=question)
            if reading is None:
                return
            embedding = None
            if reading.same_subject and reading.type is None:
                from versa.embeddings import TASK_SIMILARITY

                embedding = await loop._embedding_client.embed(reading.move, task_type=TASK_SIMILARITY)
            await direction_store.add_reading(set_id=set_id, reading=reading, embedding=embedding)
        except Exception:
            logger.warning("keeping a miss failed for session %s", session_id, exc_info=True)
        finally:
            # a miss just read counts from the next turn on
            with contextlib.suppress(Exception):
                _style_patterns.forget(await transcript.get_learner_id(session_id))

    async def _more_directions(session_id: UUID, data: dict, send) -> None:
        """"Other directions": nothing in this hand matched. Kept as a `more`
        event on the hand (a signal in itself), then the next hand is dealt
        from the same pool -- instant, no model call -- or, once the pool is
        spent, a fresh pool is written for the same answer."""
        try:
            set_id = UUID(str(data.get("set_id")))
        except ValueError:
            await send({"type": "error", "message": "unknown directions"})
            return
        latest = await direction_store.latest_set(session_id)
        if latest is None or latest.id != set_id or await direction_store.is_settled(set_id):
            await send({"type": "error", "message": "those directions are no longer open"})
            return
        try:
            await direction_store.record_event(set_id=set_id, kind="more", next_turn_index=latest.turn_index)
            _style_patterns.forget(await transcript.get_learner_id(session_id))
        except _directions.AlreadySettled:
            await send({"type": "error", "message": "those directions are no longer open"})
            return
        knobs = await transcript.get_knobs(session_id)
        if latest.pool_id is not None:
            pool_cards = await direction_store.get_pool(latest.pool_id)
            if await _deal(session_id, latest.turn_index, knobs, latest.pool_id, pool_cards,
                           latest.deal_index + 1, latest.presentation, send):
                return
        ctx = offer_context.get(session_id)
        if ctx is not None and ctx[0] == latest.turn_index:
            turn_index, message, answer, presentation = ctx
            await _offer_directions(session_id, turn_index, message, answer, send, presentation,
                                    deal_index=latest.deal_index + 1)
            return
        await send({"type": "directions_exhausted", "turn_index": latest.turn_index})

    async def _run_turn(session_id: UUID, data: dict, send) -> None:
        started = time.monotonic()
        kind = data.get("type")
        selected_option_id: UUID | None = None
        continues: str | None = None
        picture = None
        picture_id = None
        if kind == "message":
            text = str(data.get("text", "")).strip()
            picture_id = data.get("image_id")
            if not text and not picture_id:
                await send({"type": "error", "message": "empty message"})
                return
            # "ok", "thanks!", "haha", "hi": a reaction, not a question
            # (chatter.py). A short reply, no model call, nothing stored, no
            # Spark -- and the open cards and options stay open. Never with a
            # picture: "hi" + a photo of a problem is a question.
            said = last_answer.get(session_id, "").rstrip(" \n*_)\"'")
            chat_kind = None if picture_id else _chatter.classify(text, after_question=said.endswith("?"))
            if chat_kind is not None:
                latest = await direction_store.latest_set(session_id)
                directions_open = latest is not None and not await direction_store.is_settled(latest.id)
                await send({"type": "chatter", "kind": chat_kind,
                            "text": _chatter.reply(chat_kind, directions_open=directions_open)})
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
        elif kind == "direction":
            try:
                found = await direction_store.open_set_for_card(UUID(str(data.get("card_id"))))
            except ValueError:
                found = None
            if found is None or found[0].session_id != session_id:
                await send({"type": "error", "message": "unknown suggestion"})
                return
            direction_set, card = found
            latest = await direction_store.latest_set(session_id)
            if latest is None or latest.id != direction_set.id or await direction_store.is_settled(direction_set.id):
                await send({"type": "error", "message": "that suggestion is no longer available"})
                return
            text = card.text
            # a fork link continues the answer it ended; a card starts a new one
            continues = card.text if data.get("continue") else None
        else:
            await send({"type": "error", "message": f"unknown message type {kind!r}"})
            return

        # Asking needs a Spark in hand; whether one is spent depends on how
        # the turn ends (options are free, an answer costs one).
        learner_id = await transcript.get_learner_id(session_id)
        if kind == "message" and picture_id:
            # only the learner's own picture, and only one that was read
            try:
                picture = await image_store.get(UUID(str(picture_id)))
            except ValueError:
                picture = None
            if picture is None or picture.learner_id != learner_id or not picture.reading:
                await send({"type": "error", "message": "unknown picture"})
                return
        try:
            await sparks.require(learner_id, "answer")
        except InsufficientSparks as exc:
            await send({"type": "paywall", **exc.detail()})
            return

        turn_index = await next_turn_index(session_id)
        # What the learner did with the directions under the last answer:
        # took one, or passed them by asking their own question.
        try:
            if kind == "direction":
                await direction_store.record_event(
                    set_id=direction_set.id, kind="picked", card_id=card.id, next_turn_index=turn_index,
                )
            elif kind == "message":
                latest = await direction_store.latest_set(session_id)
                if latest is not None and not await direction_store.is_settled(latest.id):
                    await direction_store.record_event(
                        set_id=latest.id, kind="passed", next_turn_index=turn_index,
                    )
                    earlier = offer_context.get(session_id)
                    loop._fire_background(_keep_miss(
                        session_id, latest.id, turn_index, text, earlier[1] if earlier else ""))
        except _directions.AlreadySettled:
            if kind == "direction":
                await send({"type": "error", "message": "that suggestion is no longer available"})
                return
        if picture is not None:
            # From here on the turn's message is the words + what the picture
            # shows (images.py): the answer, memory and the stage all get it.
            # (A miss above was kept with the words alone.)
            text = _images.with_image(text, picture.reading or "")
        # the thinking style is analysed turn by turn: whatever they just did
        # counts in this turn's reading of it
        _style_patterns.forget(learner_id)
        latest_turn[session_id] = turn_index
        await send({"type": "turn_start", "turn_index": turn_index})
        if kind == "direction":
            await _reveal_guess(learner_id, direction_set.id, card.slot, turn_index, send)
        first_output_ms: float | None = None
        # The stage starts the moment the student sends: one animation of the
        # whole explanation, written alongside the answer (_StageRun).
        # After a clarifying question, the stage performs the QUESTION they
        # asked, as they clarified it -- not the option's own wording, which on
        # its own gave the director nothing to act out (found 2026-09-30: a
        # truss-bridge question got "Awesome! Let's keep going!").
        stage_text = text
        if kind == "select_option" and data.get("stage"):
            asked = next((t.text for t in await transcript.list_turns(session_id)
                          if t.turn_index == option.turn_index), None)
            if asked:
                stage_text = f"{asked}\n(They clarified that they meant: {option.text})"
        stage = (
            _StageRun(session_id, turn_index, stage_text, _Performance(turn_index, send), continues,
                      photo=picture is not None)
            if data.get("stage") else None
        )

        async def on_delta(piece: str) -> None:
            nonlocal first_output_ms
            if first_output_ms is None:
                first_output_ms = (time.monotonic() - started) * 1000
                if stage is not None:
                    await stage.perf.release()  # an answer, whatever path wrote it
            await send({"type": "delta", "text": piece})

        options_sent = False

        async def on_event(event: dict) -> None:
            # Mid-turn events from the loop (loop.py `_emit_turn_event`):
            # options shown before memory has had its say, and the
            # "I remember" beat -- with or without retracting them.
            nonlocal first_output_ms, options_sent
            if event.get("type") == "answering":
                # the turn answers: the stage may show (it's the server's
                # own signal, not the client's)
                if stage is not None:
                    await stage.perf.release()
                return
            if event.get("type") == "options":
                options_sent = True
                if first_output_ms is None:
                    first_output_ms = (time.monotonic() - started) * 1000
            await send(event)
            if event.get("type") == "recalled" and stage is not None:
                # memory knew what they meant: this turn answers. After the
                # event, so the app's "I remember" beat leads the show.
                await stage.perf.release()

        try:
            message = await loop.handle_turn(
                session_id, turn_index, text, selected_option_id,
                on_delta=on_delta, on_event=on_event, defer_tail=True, continues=continues,
                direction_pick=kind == "direction",
            )
            options = await loop.pending_options(session_id)
        except Exception as exc:
            logger.exception("turn %d failed for session %s", turn_index, session_id)
            if stage is not None:
                await stage.perf.discard()
            await send({"type": "error", "message": f"the turn failed: {exc}"})
            return

        if stage is not None:
            # options: the slime asks them instead -- this skit never shows;
            # an answer that somehow never said so still gets its skit
            await (stage.perf.discard() if options else stage.perf.release())
        total_ms = (time.monotonic() - started) * 1000
        if options and not options_sent:
            await send({
                "type": "options",
                "message": message,
                "options": [{"id": str(o.id), "text": o.text} for o in options],
            })
        if not options:
            try:
                # the answer was already delivered: take what is there
                charge = await sparks.charge(
                    learner_id, "answer", f"turn:{session_id}:{turn_index}",
                    ref={"session_id": session_id, "turn_index": turn_index},
                    after_the_fact=True,
                )
            except Exception:
                logger.exception("charging turn %d of session %s failed", turn_index, session_id)
            else:
                await send({"type": "sparks", "balance": charge.balance, "spent": charge.spent})
                if charge.streak_reward:
                    await send({"type": "sparks_reward", "reason": "study_streak",
                                "amount": charge.streak_reward})
        last_answer[session_id] = message
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
        presentation = _presentation(data)
        if not options and presentation:
            # Only for a client that shows the strip (it asks, like "stage"):
            # a set is evidence only if it was actually seen. Started after
            # `done`, so the strip always follows its answer.
            task = asyncio.create_task(
                _offer_directions(session_id, turn_index, text, message, send, presentation)
            )
            direction_tasks.add(task)
            task.add_done_callback(direction_tasks.discard)

    app.include_router(api)
    if auth is not None:
        app.include_router(build_auth_router(
            auth, pool, has_profile=profiles.has_profile, display_name=profiles.display_name,
        ))
    # The sign-up profile (profiles.py): asked once after the first sign-in.
    app.include_router(build_profiles_router(pool, tiers.fast))
    app.include_router(images_router)
    # Revision notes of a chat (notes.py): made only when the learner asks.
    async def _chat_so_far(session_id: UUID) -> list[HistoryTurn]:
        return await reconstruct_session_history(
            transcript, node_calls, disambiguation, session_id, answer_versions=answer_versions,
        )

    app.include_router(_notes.build_notes_router(pool, tiers.best, _chat_so_far, sparks=sparks))
    app.include_router(build_sparks_router(sparks, pool))
    app.include_router(build_billing_router(billing))
    app.include_router(build_feed_router(pool, tiers.fast))
    app.include_router(build_topics_router(
        pool, tiers.fast, loop._embedding_client, ablation_config=loop.ablation_config,
        sparks=sparks, on_progress=on_lesson_progress,
    ))
    # Exam preparation (exams.py): syllabus units, unit quizzes, mock tests.
    exams_router = build_exams_router(pool, tiers.fast, sparks=sparks)
    app.state.exam_service = exams_router.exam_service
    app.state.direction_tasks = direction_tasks  # tests wait on these
    app.include_router(exams_router)
    # Rooms (experimental, rooms/): group study chats with Versa as a member.
    room_hub = RoomHub(pool, tiers.fast)
    app.state.room_hub = room_hub
    app.include_router(build_rooms_router(room_hub))

    @app.get("/invite/{code}", response_class=HTMLResponse, include_in_schema=False)
    async def invite_page(code: str) -> HTMLResponse:
        """What an invite link opens: the code, and where to get the app."""
        problem = accounts.invite_problem(await accounts.get_invite(code), datetime.now(UTC))
        return HTMLResponse(render_invite_page(code, problem, android_download_url))

    # The built Flutter web app, if there is one, at "/" -- registered last so
    # it can never shadow /api. One command, one URL.
    if web_dir is not None and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app
