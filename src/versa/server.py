"""HTTP + WebSocket API over `SessionLoop` — what the Versa app talks to.

The loop is built through `session_builder.build_session_loop` (the one
shared assembly point), so a chat here gets exactly the personalization,
memory, options and audit trail `versa chat` does. This module adds only
transport:

    GET  /api/health                       liveness + whether the LLM is live or a stub
    POST /api/learners      {label}        get-or-create a learner by name (no passwords yet)
    POST /api/sessions      {learner_id}   start a chat
    WS   /api/sessions/{id}/chat           one chat, one turn at a time

Chat protocol (JSON text frames).

  client -> server
    {"type": "message", "text": "..."}            a typed message
    {"type": "select_option", "option_id": "..."}  a click on an offered reading

  server -> client, per turn, in this order
    {"type": "turn_start", "turn_index": N}
    {"type": "delta", "text": "..."}      0..n pieces of the answer as it is written
    {"type": "options", "message": "...", "options": [{"id", "text"}]}   ambiguous turns
    {"type": "done", "turn_index": N, "kind": "answer" | "options", "text": "...",
     "timing": {"first_output_ms": ..., "total_ms": ...}}
    {"type": "error", "message": "..."}   the turn failed; the socket stays usable

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
import logging
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from versa.audit import TranscriptStore
from versa.disambiguate import DisambiguationStore
from versa.domain_config import DomainConfig
from versa.embeddings import EmbeddingClient
from versa.learner import LearnerStore
from versa.llm import ModelTierClients
from versa.loop import SessionLoop
from versa.models import OptionStatus
from versa.session_builder import build_session_loop

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


class SessionOut(BaseModel):
    session_id: UUID
    learner_id: UUID
    mode: str


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
    loop: SessionLoop = build_session_loop(
        pool, tiers, embedding_client, domain_config=domain_config
    )
    learners = LearnerStore(pool)
    transcript = TranscriptStore(pool)
    disambiguation = DisambiguationStore(pool)
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

    @api.post("/sessions", response_model=SessionOut)
    async def create_session(body: SessionIn) -> SessionOut:
        if await learners.get(body.learner_id) is None:
            raise HTTPException(status_code=404, detail="unknown learner")
        session_id = await transcript.create_session(
            body.learner_id, ablation_config=loop.ablation_config
        )
        return SessionOut(session_id=session_id, learner_id=body.learner_id, mode=body.mode)

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

        try:
            while connected:
                data = await ws.receive_json()
                if lock.locked():
                    await send({"type": "error", "message": "a turn is already running"})
                    continue
                async with lock:
                    await _run_turn(session_id, data, send)
        except WebSocketDisconnect:
            return

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

        async def on_delta(piece: str) -> None:
            nonlocal first_output_ms
            if first_output_ms is None:
                first_output_ms = (time.monotonic() - started) * 1000
            await send({"type": "delta", "text": piece})

        try:
            message = await loop.handle_turn(
                session_id, turn_index, text, selected_option_id,
                on_delta=on_delta, defer_tail=True,
            )
            options = await loop.pending_options(session_id)
        except Exception as exc:
            logger.exception("turn %d failed for session %s", turn_index, session_id)
            await send({"type": "error", "message": f"the turn failed: {exc}"})
            return

        total_ms = (time.monotonic() - started) * 1000
        if options:
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

    # The built Flutter web app, if there is one, at "/" -- registered last so
    # it can never shadow /api. One command, one URL.
    if web_dir is not None and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app
