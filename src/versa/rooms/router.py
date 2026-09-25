"""HTTP + WebSocket routes for rooms, mounted by server.py.

    POST /api/rooms                  {code, name, topic | link}  create (topic search or web link)
    POST /api/rooms/from-pdf         multipart code, name, file  create from an uploaded PDF
    POST /api/rooms/{code}/join      {name}                      join (the same name rejoins)
    GET  /api/rooms/{code}/state     ?member_id=                 everything this person can see
    POST /api/rooms/summaries        {memberships: [{member_id, seen_seq}]}
                                                                 the rooms list (last message, unread)
    WS   /api/rooms/{code}/ws        ?member_id=                 the live room (see hub.py)

WebSocket, client -> server:
    {"type": "message", "text": "..."}
    {"type": "pick", "option_id": "..."}
    {"type": "typing"}
Errors come back as {"type": "error", "message": "..."}; the socket stays open.

No authentication, like the rest of the local server: a name and a room code
are enough to get in.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from versa import resources as _resources
from versa.rooms.hub import RoomError, RoomHub, room_out
from versa.rooms.store import MemberRow, RoomCodeTaken, RoomRow

logger = logging.getLogger(__name__)

_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,23}$")
_RESERVED_NAMES = {"versa", "system", "everyone", "all"}


class CreateIn(BaseModel):
    code: str
    name: str
    topic: str | None = None
    link: str | None = None


class JoinIn(BaseModel):
    name: str


class MembershipIn(BaseModel):
    member_id: UUID
    seen_seq: int = 0


class SummariesIn(BaseModel):
    memberships: list[MembershipIn] = Field(default_factory=list, max_length=100)


def _check_code(code: str) -> str:
    code = code.strip()
    if not _CODE.match(code):
        raise HTTPException(
            status_code=422,
            detail="room code: 3-24 letters, digits, - or _, starting with a letter or digit",
        )
    return code


def _check_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name or len(name) > 40:
        raise HTTPException(status_code=422, detail="your name: 1-40 characters")
    if name.lower() in _RESERVED_NAMES:
        raise HTTPException(status_code=422, detail=f"{name!r} is taken in every room -- pick another name")
    return name


def _joined(room: RoomRow, member: MemberRow) -> dict:
    return {"room": room_out(room), "member": {"id": str(member.id), "name": member.name}}


def build_rooms_router(hub: RoomHub, *, link_fetcher: Callable | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")
    store = hub.store
    fetch_link = link_fetcher or _resources.fetch_link

    async def create(code: str, name: str, **kwargs) -> dict:
        try:
            room, member = await hub.create_room(code, name, **kwargs)
        except RoomCodeTaken:
            raise HTTPException(
                status_code=409, detail=f"the room code {code!r} is taken -- pick another, or join it",
            ) from None
        except RoomError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None
        return _joined(room, member)

    @router.post("/rooms")
    async def create_room(body: CreateIn) -> dict:
        code, name = _check_code(body.code), _check_name(body.name)
        if body.link and body.link.strip():
            if await store.get_room_by_code(code) is not None:
                raise HTTPException(status_code=409, detail=f"the room code {code!r} is taken -- pick another, or join it")
            try:
                resource = await fetch_link(body.link.strip())
            except _resources.ResourceError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            return await create(code, name, resource=resource, source_kind="link")
        topic = " ".join((body.topic or "").split())
        if not topic:
            raise HTTPException(status_code=422, detail="give the room a topic, a link or a PDF")
        return await create(code, name, query=topic[:200], source_kind="search")

    @router.post("/rooms/from-pdf")
    async def create_room_from_pdf(
        code: str = Form(...), name: str = Form(...), file: UploadFile = File(...)
    ) -> dict:
        code, name = _check_code(code), _check_name(name)
        if await store.get_room_by_code(code) is not None:
            raise HTTPException(status_code=409, detail=f"the room code {code!r} is taken -- pick another, or join it")
        data = await file.read(_resources.MAX_PDF_BYTES + 1)
        try:
            resource = await asyncio.to_thread(_resources.extract_pdf, data, file.filename)
        except _resources.ResourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return await create(code, name, resource=resource, source_kind="pdf")

    @router.post("/rooms/{code}/join")
    async def join_room(code: str, body: JoinIn) -> dict:
        name = _check_name(body.name)
        try:
            room, member = await hub.join(code, name)
        except RoomError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return _joined(room, member)

    async def resolve(code: str, member_id: UUID) -> tuple[RoomRow, MemberRow] | None:
        room = await store.get_room_by_code(code)
        member = await store.get_member(member_id)
        if room is None or member is None or member.room_id != room.id:
            return None
        return room, member

    @router.get("/rooms/{code}/state")
    async def room_state(code: str, member_id: UUID) -> dict:
        found = await resolve(code, member_id)
        if found is None:
            raise HTTPException(status_code=404, detail="unknown room or member")
        return await hub.state_for(*found)

    @router.post("/rooms/summaries")
    async def summaries(body: SummariesIn) -> list[dict]:
        out = []
        for m in body.memberships:
            member = await store.get_member(m.member_id)
            if member is None:
                continue
            summary = await hub.summary_for(member, m.seen_seq)
            if summary is not None:
                out.append(summary)
        return out

    @router.websocket("/rooms/{code}/ws")
    async def room_socket(ws: WebSocket, code: str, member_id: UUID) -> None:
        found = await resolve(code, member_id)
        if found is None:
            await ws.close(code=4404)
            return
        room, member = found
        await ws.accept()
        connected = True

        async def send(event: dict) -> None:
            # A vanished device must never break the room for everyone else.
            nonlocal connected
            if not connected:
                return
            try:
                await ws.send_json(event)
            except (WebSocketDisconnect, RuntimeError):
                connected = False

        await hub.connect(room.id, member.id, send)
        try:
            await send(await hub.state_for(room, member))
            while connected:
                data = await ws.receive_json()
                kind = data.get("type")
                try:
                    if kind == "message":
                        await hub.post_message(room, member, str(data.get("text", "")))
                    elif kind == "pick":
                        try:
                            option_id = UUID(str(data.get("option_id")))
                        except ValueError:
                            raise RoomError("unknown option") from None
                        await hub.pick(room, member, option_id)
                    elif kind == "typing":
                        await hub.member_typing(room.id, member)
                    else:
                        raise RoomError(f"unknown message type {kind!r}")
                except RoomError as exc:
                    await send({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("room %s: socket for %s failed", code, member.name)
        finally:
            await hub.disconnect(room.id, member.id, send)

    router.room_hub = hub  # type: ignore[attr-defined]  # exposed for tests
    return router
