"""The live side of rooms: who is connected, what they're sent, and Versa's
turn in the conversation.

Every room has at most one RoomDirector call running at a time. Things that
happen while it runs (more messages, a join, a click) queue up and are handed
to the NEXT call together, so a fast back-and-forth between people costs one
call per burst, not one per message -- and Versa always decides with the
latest chat in front of it.

Each connected device gets:
    {"type": "state", ...}    everything, on connect (so a reconnect resyncs)
    {"type": "message", ...}  each new message it is allowed to see
    {"type": "board", ...}    members/online, everyone's tasks, and the
                              options still open for THIS person
    {"type": "typing", ...}   someone (or Versa) is typing
    {"type": "stage_start" | "stage" | "stage_end", ...}
                              the slime acting out a teaching message
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg

from versa.resources import ExtractedResource
from versa.rooms.nodes import RoomDecision, RoomDirector, mentions_versa
from versa.rooms.store import (
    MemberRow,
    MessageRow,
    OptionSetRow,
    RoomCodeTaken,
    RoomRow,
    RoomStore,
    TaskRow,
    done_task_ids,
    open_option_sets,
)
from versa.stage import StageDirector, stage_sink
from versa.topics import GenerateBranches, OutlineResource

logger = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]

VERSA = "Versa"
TRANSCRIPT_MESSAGES = 40
RESOURCE_EXCERPT_CHARS = 12_000
DIRECTOR_EXCERPT_CHARS = 5_000
HISTORY_MESSAGES = 300


class RoomError(Exception):
    """A request that can't be done (shown to the person as is)."""


@dataclass
class _Trigger:
    kind: str  # created | joined | message | pick
    name: str
    seq: int
    text: str = ""
    must_reply: bool = False

    def describe(self) -> str:
        if self.kind == "created":
            return f"{self.name} just created this room and is waiting to start"
        if self.kind == "joined":
            return f"{self.name} just joined the room"
        if self.kind == "pick":
            return f'{self.name} clicked the option "{self.text}" (message [{self.seq}])'
        direct = " -- and is talking to YOU directly" if self.must_reply else ""
        return f"{self.name} wrote message [{self.seq}]{direct}"


def _iso(value) -> str:
    return value.isoformat()


class RoomHub:
    def __init__(
        self,
        pool: asyncpg.Pool,
        llm,
        *,
        stage_llm=None,
    ) -> None:
        self.store = RoomStore(pool)
        self.director = RoomDirector(llm)
        self.branches = GenerateBranches(llm)
        self.outline = OutlineResource(llm)
        self.stage = StageDirector(stage_llm or llm)
        self._sockets: dict[UUID, dict[UUID, set[Send]]] = {}
        self._pending: dict[UUID, list[_Trigger]] = {}
        self._workers: dict[UUID, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()
        self._pick_locks: dict[UUID, asyncio.Lock] = {}

    # ============================================================ audit

    async def _call(self, node, room_id: UUID | None, seq: int, **kwargs):
        """Run a node and record its full input and output (or its error) to
        room_node_calls -- the rooms equivalent of SessionLoop._call_node."""
        input_json: dict = {"kwargs": kwargs}
        prompt = getattr(node, "prompt", None)
        if callable(prompt):
            input_json["prompt"] = prompt(**kwargs)
        name = getattr(node, "name", None) or type(node).__name__
        try:
            output = await node.run(**kwargs)
        except Exception as exc:
            await self.store.record_node_call(
                room_id=room_id, seq=seq, node_name=name, input_json=input_json,
                output_json=None, error=f"{type(exc).__name__}: {exc}",
            )
            raise
        await self.store.record_node_call(
            room_id=room_id, seq=seq, node_name=name, input_json=input_json, output_json=output,
        )
        return output

    # ===================================================== create / join

    async def create_room(
        self,
        code: str,
        name: str,
        *,
        query: str | None = None,
        resource: ExtractedResource | None = None,
        source_kind: str = "search",
    ) -> tuple[RoomRow, MemberRow]:
        if await self.store.get_room_by_code(code) is not None:
            raise RoomCodeTaken(code)
        excerpt: str | None = None
        if resource is not None:
            kwargs = {
                "title": resource.title, "headings": resource.headings,
                "excerpt": resource.outline_excerpt(), "profile": "",
            }
            try:
                result = await self.outline.run(**kwargs)
            except Exception as exc:
                await self._record_failed_outline(self.outline, kwargs, exc)
                raise RoomError("could not read a topic out of that resource") from exc
            title, parts, node, query = result["title"], result["branches"], self.outline, resource.title
            excerpt = (resource.text or "")[:RESOURCE_EXCERPT_CHARS] or None
        else:
            query = " ".join((query or "").split())
            kwargs = {"path": [query], "existing": [], "profile": ""}
            try:
                parts = await self.branches.run(**kwargs)
            except Exception as exc:
                await self._record_failed_outline(self.branches, kwargs, exc)
                raise RoomError("could not map that topic, try rephrasing it") from exc
            title, node, result = query, self.branches, parts
        outline = [{"title": p["title"], "summary": p["summary"]} for p in parts]
        if not outline:
            raise RoomError("could not map that topic, try rephrasing it")
        room = await self.store.add_room(
            code=code, title=title, source_kind=source_kind, query=query, outline=outline,
            created_by=" ".join(name.split()),
            resource_url=resource.url if resource else None,
            resource_filename=resource.filename if resource else None,
            resource_excerpt=excerpt,
        )
        await self.store.record_node_call(
            room_id=room.id, seq=0, node_name=node.name,
            input_json={"kwargs": kwargs, "prompt": node.prompt(**kwargs)}, output_json=result,
        )
        member, _ = await self.store.get_or_add_member(room.id, name)
        msg = await self.store.add_message(
            room.id, sender="system", kind="event", member_id=member.id,
            text=f"{member.name} created the room",
        )
        self._trigger(room.id, _Trigger("created", member.name, msg.seq, must_reply=True))
        return room, member

    async def _record_failed_outline(self, node, kwargs: dict, exc: Exception) -> None:
        try:
            await self.store.record_node_call(
                room_id=None, seq=0, node_name=node.name,
                input_json={"kwargs": kwargs, "prompt": node.prompt(**kwargs)},
                output_json=None, error=f"{type(exc).__name__}: {exc}",
            )
        except Exception:
            logger.exception("could not record a failed room outline call")

    async def join(self, code: str, name: str) -> tuple[RoomRow, MemberRow]:
        room = await self.store.get_room_by_code(code)
        if room is None:
            raise RoomError("there's no room with that code")
        member, created = await self.store.get_or_add_member(room.id, name)
        if created:
            msg = await self.store.add_message(
                room.id, sender="system", kind="event", member_id=member.id,
                text=f"{member.name} joined",
            )
            await self._broadcast_message(room.id, msg)
            await self.push_boards(room.id)
            self._trigger(room.id, _Trigger("joined", member.name, msg.seq, must_reply=True))
        return room, member

    # ======================================================= from people

    async def post_message(self, room: RoomRow, member: MemberRow, text: str) -> MessageRow:
        text = text.strip()
        if not text:
            raise RoomError("empty message")
        msg = await self.store.add_message(
            room.id, sender="member", kind="text", member_id=member.id, text=text[:4000],
        )
        await self._broadcast_message(room.id, msg)
        self._trigger(room.id, _Trigger(
            "message", member.name, msg.seq, text=text, must_reply=mentions_versa(text),
        ))
        return msg

    async def pick(self, room: RoomRow, member: MemberRow, option_id: UUID) -> MessageRow:
        lock = self._pick_locks.setdefault(room.id, asyncio.Lock())
        async with lock:
            sets = await self.store.list_option_sets(room.id)
            picks = await self.store.list_picks(room.id)
            live = open_option_sets(member.id, sets, picks)
            chosen_set, option = None, None
            for s in live:
                for o in s.options:
                    if o.id == option_id:
                        chosen_set, option = s, o
            if chosen_set is None or option is None:
                raise RoomError("that option is no longer available")
            question = next(
                (m for m in await self.store.list_messages(room.id, limit=HISTORY_MESSAGES)
                 if m.id == chosen_set.message_id),
                None,
            )
            private = bool(question and question.private)
            msg = await self.store.add_message(
                room.id, sender="member", kind="pick", member_id=member.id, text=option.text,
                to_member_id=member.id if private else None, private=private,
                meta={"option_set_id": str(chosen_set.id), "option_id": str(option.id),
                      "prompt": chosen_set.prompt},
            )
            await self.store.add_pick(chosen_set.id, option.id, member.id, msg.id)
        await self._broadcast_message(room.id, msg)
        await self.push_boards(room.id)
        self._trigger(room.id, _Trigger("pick", member.name, msg.seq, text=option.text, must_reply=True))
        return msg

    async def member_typing(self, room_id: UUID, member: MemberRow) -> None:
        await self._send_all(
            room_id, {"type": "typing", "member_id": str(member.id), "name": member.name, "on": True},
            skip=member.id,
        )

    # ======================================================= connections

    async def connect(self, room_id: UUID, member_id: UUID, send: Send) -> None:
        self._sockets.setdefault(room_id, {}).setdefault(member_id, set()).add(send)
        await self.push_boards(room_id)

    async def disconnect(self, room_id: UUID, member_id: UUID, send: Send) -> None:
        by_member = self._sockets.get(room_id, {})
        sends = by_member.get(member_id)
        if sends is not None:
            sends.discard(send)
            if not sends:
                by_member.pop(member_id, None)
        await self.push_boards(room_id)

    def online(self, room_id: UUID) -> set[UUID]:
        return set(self._sockets.get(room_id, {}))

    async def _send_all(self, room_id: UUID, event: dict, *, skip: UUID | None = None) -> None:
        for member_id, sends in list(self._sockets.get(room_id, {}).items()):
            if member_id == skip:
                continue
            for send in list(sends):
                await send(event)

    async def _broadcast_message(self, room_id: UUID, msg: MessageRow) -> None:
        names = {m.id: m.name for m in await self.store.list_members(room_id)}
        out = message_out(msg, names)
        for member_id, sends in list(self._sockets.get(room_id, {}).items()):
            if not msg.visible_to(member_id):
                continue
            for send in list(sends):
                await send({"type": "message", "message": out})

    async def push_boards(self, room_id: UUID) -> None:
        by_member = self._sockets.get(room_id)
        if not by_member:
            return
        snapshot = await self._snapshot(room_id)
        for member_id, sends in list(by_member.items()):
            board = self._board(member_id, snapshot)
            for send in list(sends):
                await send({"type": "board", "board": board})

    # =========================================================== state

    async def _snapshot(self, room_id: UUID) -> dict:
        return {
            "members": await self.store.list_members(room_id),
            "tasks": await self.store.list_tasks(room_id),
            "done": done_task_ids(await self.store.list_task_events(room_id)),
            "sets": await self.store.list_option_sets(room_id),
            "picks": await self.store.list_picks(room_id),
            "online": self.online(room_id),
        }

    def _board(self, member_id: UUID, snap: dict) -> dict:
        names = {m.id: m.name for m in snap["members"]}
        return {
            "members": [
                {"id": str(m.id), "name": m.name, "online": m.id in snap["online"]}
                for m in snap["members"]
            ],
            "tasks": [
                {
                    "id": str(t.id), "member_id": str(t.member_id),
                    "member_name": names.get(t.member_id, "?"), "kind": t.kind,
                    "description": t.description, "done": t.id in snap["done"],
                    "created_at": _iso(t.created_at),
                }
                for t in snap["tasks"]
            ],
            "options": [
                {
                    "set_id": str(s.id), "prompt": s.prompt, "for_everyone": s.member_id is None,
                    "options": [{"id": str(o.id), "text": o.text} for o in s.options],
                }
                for s in open_option_sets(member_id, snap["sets"], snap["picks"])
            ],
        }

    async def state_for(self, room: RoomRow, member: MemberRow) -> dict:
        names = {m.id: m.name for m in await self.store.list_members(room.id)}
        messages = await self.store.list_visible_messages(room.id, member.id, limit=HISTORY_MESSAGES)
        return {
            "type": "state",
            "room": room_out(room),
            "me": {"id": str(member.id), "name": member.name},
            "messages": [message_out(m, names) for m in messages],
            "board": self._board(member.id, await self._snapshot(room.id)),
        }

    async def summary_for(self, member: MemberRow, seen_seq: int) -> dict | None:
        room = await self.store.get_room(member.room_id)
        if room is None:
            return None
        members = await self.store.list_members(room.id)
        names = {m.id: m.name for m in members}
        last = await self.store.list_visible_messages(room.id, member.id, limit=1)
        return {
            "code": room.code,
            "title": room.title,
            "member_id": str(member.id),
            "name": member.name,
            "member_names": [m.name for m in members],
            "online": len(self.online(room.id)),
            "last_message": message_out(last[0], names) if last else None,
            "unread": await self.store.count_visible_after(room.id, member.id, seen_seq),
            "created_at": _iso(room.created_at),
        }

    # ============================================================ Versa

    def _trigger(self, room_id: UUID, trigger: _Trigger) -> None:
        self._pending.setdefault(room_id, []).append(trigger)
        worker = self._workers.get(room_id)
        if worker is None or worker.done():
            self._workers[room_id] = asyncio.create_task(self._work(room_id))

    async def _work(self, room_id: UUID) -> None:
        # No await between the emptiness check and the pop, so a trigger
        # added meanwhile is either in this batch or sees a running worker.
        while self._pending.get(room_id):
            batch = self._pending.pop(room_id)
            try:
                await self._direct(room_id, batch)
            except Exception:
                logger.exception("room %s: Versa's turn failed", room_id)

    async def wait_idle(self) -> None:
        """Until no Versa turn or stage performance is running (tests)."""
        while True:
            running = [t for t in (*self._workers.values(), *self._background) if not t.done()]
            if not running:
                return
            await asyncio.gather(*running, return_exceptions=True)

    async def _direct(self, room_id: UUID, batch: list[_Trigger]) -> None:
        room = await self.store.get_room(room_id)
        if room is None:
            return
        snap = await self._snapshot(room_id)
        members: list[MemberRow] = snap["members"]
        names = {m.id: m.name for m in members}
        tasks: list[TaskRow] = snap["tasks"]
        done: set[UUID] = snap["done"]
        member_ctx = []
        for m in members:
            mine = [t for t in tasks if t.member_id == m.id]
            open_ = [t for t in mine if t.id not in done]
            member_ctx.append({
                "name": m.name,
                "online": m.id in snap["online"],
                "current_task": (
                    {"kind": open_[0].kind, "description": open_[0].description} if open_ else None
                ),
                "done_count": sum(1 for t in mine if t.id in done),
                "queued_count": max(len(open_) - 1, 0),
            })
        recent = await self.store.list_messages(room_id, limit=TRANSCRIPT_MESSAGES)
        must_reply = any(t.must_reply for t in batch)
        if must_reply:
            await self._versa_typing(room_id, True)
        try:
            decision: RoomDecision = await self._call(
                self.director, room_id, recent[-1].seq if recent else 0,
                title=room.title,
                outline=room.outline,
                resource_excerpt=(room.resource_excerpt or "")[:DIRECTOR_EXCERPT_CHARS],
                members=member_ctx,
                open_options=_describe_open_sets(snap["sets"], snap["picks"], names),
                transcript=[_transcript_line(m, names) for m in recent],
                events=[t.describe() for t in batch],
                must_reply=must_reply,
            )
            await self._apply(room, decision, members, tasks, done)
        finally:
            if must_reply:
                await self._versa_typing(room_id, False)

    async def _versa_typing(self, room_id: UUID, on: bool) -> None:
        await self._send_all(room_id, {"type": "typing", "member_id": None, "name": VERSA, "on": on})

    async def _apply(
        self,
        room: RoomRow,
        decision: RoomDecision,
        members: list[MemberRow],
        tasks: list[TaskRow],
        done: set[UUID],
    ) -> None:
        by_name = {m.name: m for m in members}
        board_changed = False
        for action in decision.actions:
            target = by_name.get(action.to) if action.to else None
            if action.type == "say":
                msg = await self.store.add_message(
                    room.id, sender="versa", kind="text" if action.kind == "chat" else action.kind,
                    text=action.text, to_member_id=target.id if target else None,
                    private=action.private and target is not None,
                )
                await self._broadcast_message(room.id, msg)
                if msg.kind == "content" and not msg.private:
                    self._perform(room.id, msg)
            elif action.type == "options":
                set_id = uuid4()
                msg = await self.store.add_message(
                    room.id, sender="versa", kind="question", text=action.prompt,
                    to_member_id=target.id if target else None,
                    meta={"option_set_id": str(set_id), "options": action.options},
                )
                await self.store.add_option_set(
                    set_id, room.id, target.id if target else None, action.prompt, action.options, msg.id,
                )
                await self._broadcast_message(room.id, msg)
                board_changed = True
            elif action.type == "task":
                for who in [target] if target else members:
                    msg = await self.store.add_message(
                        room.id, sender="versa", kind="task", text=action.text,
                        to_member_id=who.id, meta={"task_kind": action.kind},
                    )
                    tasks.append(await self.store.add_task(room.id, who.id, action.kind, action.text, msg.id))
                    await self._broadcast_message(room.id, msg)
                board_changed = True
            elif action.type == "complete_task" and target is not None:
                open_ = [t for t in tasks if t.member_id == target.id and t.id not in done]
                if not open_:
                    continue
                task = open_[0]
                msg = await self.store.add_message(
                    room.id, sender="versa", kind="progress", text=task.description,
                    to_member_id=target.id,
                    meta={"task_id": str(task.id), "task_kind": task.kind, "evidence": action.evidence},
                )
                await self.store.add_task_event(task.id, "completed", action.evidence, msg.id)
                done.add(task.id)
                await self._broadcast_message(room.id, msg)
                board_changed = True
        if board_changed:
            await self.push_boards(room.id)

    def _perform(self, room_id: UUID, msg: MessageRow) -> None:
        """Act a teaching message out on everyone's stage, in the background.
        Best effort: a failure costs the skit, never the message."""

        async def run() -> None:
            async def forward(action: dict) -> None:
                await self._send_all(room_id, {"type": "stage", "seq": msg.seq, "action": action})

            await self._send_all(room_id, {"type": "stage_start", "seq": msg.seq})
            stage_sink.set(forward)  # this task's own context only
            try:
                await self._call(self.stage, room_id, msg.seq, message=msg.text[:800])
            except Exception:
                logger.warning("room %s: stage direction failed", room_id, exc_info=True)
            finally:
                await self._send_all(room_id, {"type": "stage_end", "seq": msg.seq})

        task = asyncio.create_task(run())
        self._background.add(task)
        task.add_done_callback(self._background.discard)


# ================================================================ wire shapes


def room_out(room: RoomRow) -> dict:
    return {
        "id": str(room.id),
        "code": room.code,
        "title": room.title,
        "source_kind": room.source_kind,
        "query": room.query,
        "resource_url": room.resource_url,
        "resource_filename": room.resource_filename,
        "outline": room.outline,
        "created_by": room.created_by,
        "created_at": _iso(room.created_at),
    }


def message_out(msg: MessageRow, names: dict[UUID, str]) -> dict:
    if msg.sender == "versa":
        sender_name = VERSA
    elif msg.member_id is not None:
        sender_name = names.get(msg.member_id, "?")
    else:
        sender_name = ""
    return {
        "id": str(msg.id),
        "seq": msg.seq,
        "sender": msg.sender,
        "member_id": str(msg.member_id) if msg.member_id else None,
        "sender_name": sender_name,
        "kind": msg.kind,
        "text": msg.text,
        "to_member_id": str(msg.to_member_id) if msg.to_member_id else None,
        "to_name": names.get(msg.to_member_id) if msg.to_member_id else None,
        "private": msg.private,
        "meta": msg.meta,
        "created_at": _iso(msg.created_at),
    }


def _transcript_line(msg: MessageRow, names: dict[UUID, str]) -> str:
    who = names.get(msg.member_id, "?") if msg.member_id else ""
    to = names.get(msg.to_member_id, "?") if msg.to_member_id else "everyone"
    if msg.sender == "system":
        return f"[{msg.seq}] ({msg.text})"
    if msg.sender == "member":
        if msg.kind == "pick":
            return f'[{msg.seq}] {who} clicked: "{msg.text}" (answering: {msg.meta.get("prompt", "")})'
        return f"[{msg.seq}] {who}: {msg.text}"
    if msg.kind == "task":
        return f"[{msg.seq}] Versa gave {to} a task: {msg.text}"
    if msg.kind == "progress":
        return f"[{msg.seq}] Versa marked {to}'s task done: {msg.text}"
    if msg.kind == "question" and msg.meta.get("options"):
        opts = " | ".join(msg.meta["options"])
        return f"[{msg.seq}] Versa -> {to} (options: {opts}): {msg.text}"
    private = ", private" if msg.private else ""
    return f"[{msg.seq}] Versa -> {to}{private}: {msg.text}"


def _describe_open_sets(sets: list[OptionSetRow], picks, names: dict[UUID, str]) -> list[str]:
    newest: dict[UUID | None, OptionSetRow] = {}
    for s in sets:
        newest[s.member_id] = s
    out = []
    picked_by: dict[UUID, list[str]] = {}
    for p in picks:
        picked_by.setdefault(p.set_id, []).append(names.get(p.member_id, "?"))
    for audience, s in newest.items():
        who = names.get(audience, "?") if audience else "everyone"
        done = picked_by.get(s.id, [])
        if audience is not None and done:
            continue
        answered = f" (already answered by: {', '.join(done)})" if done else ""
        out.append(f'for {who}: "{s.prompt}" -> ' + " | ".join(o.text for o in s.options) + answered)
    return out
