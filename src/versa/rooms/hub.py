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
    {"type": "board", ...}    members/online, everyone's tasks, the topic's
                              parts (covered or not), and the options still
                              open for THIS person
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

from versa.resources import OUTLINE_TEXT_CHARS, ExtractedResource
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
from versa.topics import GenerateBranches, OutlineResource, sample_text

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
    kind: str  # created | joined | message | pick | quiz
    name: str
    seq: int
    text: str = ""
    must_reply: bool = False
    detail: str = ""

    def describe(self) -> str:
        if self.kind == "created":
            return f"{self.name} just created this room and is waiting to start"
        if self.kind == "joined":
            return f"{self.name} just joined the room"
        if self.kind == "pick":
            return f'{self.name} clicked the option "{self.text}" (message [{self.seq}])'
        if self.kind == "race":
            return f'{self.name} {self.detail} (message [{self.seq}])'
        if self.kind == "quiz":
            return f'{self.name} answered their task by tapping "{self.text}" -- {self.detail} (message [{self.seq}])'
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
                "excerpt": sample_text(resource.text, OUTLINE_TEXT_CHARS), "profile": "",
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
            meta = {"option_set_id": str(chosen_set.id), "option_id": str(option.id),
                    "prompt": chosen_set.prompt}
            # A quiz task's options: the tap is the answer, graded against
            # the one kept with the task (never the client's word for it).
            quiz_task = None
            if question is not None and question.kind == "task" and question.meta.get("answer"):
                quiz_task = next((t for t in await self.store.list_tasks(room.id)
                                  if t.message_id == question.id), None)
            race = question is not None and question.kind == "question" and bool(question.meta.get("race"))
            if race:
                if (await self.store.list_races(room.id)).get(chosen_set.id) is not None:
                    raise RoomError("that race is already over")
                # graded, but the answer stays secret until the race closes
                meta.update({"race": True, "correct": option.text == str(question.meta.get("answer"))})
            if quiz_task is not None:
                right = str(question.meta["answer"])
                meta.update({"quiz": True, "correct": option.text == right, "right_answer": right})
            msg = await self.store.add_message(
                room.id, sender="member", kind="pick", member_id=member.id, text=option.text,
                to_member_id=member.id if private else None, private=private, meta=meta,
            )
            await self.store.add_pick(chosen_set.id, option.id, member.id, msg.id)
            if quiz_task is not None and meta["correct"]:
                done = done_task_ids(await self.store.list_task_events(room.id))
                if quiz_task.id not in done:
                    evidence = f'tapped the right answer: "{option.text}"'
                    progress = await self.store.add_message(
                        room.id, sender="versa", kind="progress", text=quiz_task.description,
                        to_member_id=member.id,
                        meta={"task_id": str(quiz_task.id), "task_kind": quiz_task.kind, "evidence": evidence},
                    )
                    await self.store.add_task_event(quiz_task.id, "completed", evidence, progress.id)
                else:
                    progress = None
            else:
                progress = None
            race_detail = ""
            if race:
                right = str(question.meta.get("answer"))
                members = await self.store.list_members(room.id)
                tapped = {p.member_id for p in await self.store.list_picks(room.id) if p.set_id == chosen_set.id}
                if meta["correct"]:
                    race_detail = f'WON the race "{chosen_set.prompt}" by tapping "{option.text}" first'
                    progress = await self.store.add_message(
                        room.id, sender="versa", kind="progress", text=f"{member.name} won the race",
                        meta={"race_set_id": str(chosen_set.id), "winner_id": str(member.id),
                              "winner": member.name, "right_answer": right},
                    )
                elif tapped >= {m.id for m in members}:
                    race_detail = f'was the last to answer the race "{chosen_set.prompt}" -- nobody got it'
                    progress = await self.store.add_message(
                        room.id, sender="versa", kind="progress", text="Nobody got that one",
                        meta={"race_set_id": str(chosen_set.id), "winner": None, "right_answer": right},
                    )
        await self._broadcast_message(room.id, msg)
        if progress is not None:
            await self._broadcast_message(room.id, progress)
        await self.push_boards(room.id)
        if race:
            if race_detail:  # a wrong tap while the race is still open: nothing for Versa to say
                self._trigger(room.id, _Trigger("race", member.name, msg.seq, text=option.text,
                                                must_reply=True, detail=race_detail))
        elif quiz_task is not None:
            detail = ("RIGHT -- their task is done" if meta["correct"]
                      else f'WRONG (the right answer: "{meta["right_answer"]}")')
            self._trigger(room.id, _Trigger("quiz", member.name, msg.seq, text=option.text,
                                            must_reply=True, detail=detail))
        else:
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
            "room": await self.store.get_room(room_id),
            "done_parts": await self.store.list_done_parts(room_id),
            "races": await self.store.list_races(room_id),
            "graded": await self.store.list_graded_picks(room_id),
            "members": await self.store.list_members(room_id),
            "tasks": await self.store.list_tasks(room_id),
            "done": done_task_ids(await self.store.list_task_events(room_id)),
            "sets": await self.store.list_option_sets(room_id),
            "picks": await self.store.list_picks(room_id),
            "online": self.online(room_id),
        }

    def _board(self, member_id: UUID, snap: dict) -> dict:
        names = {m.id: m.name for m in snap["members"]}
        task_by_message = {t.message_id: t.id for t in snap["tasks"] if t.message_id is not None}
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
                    # the choices of this person's quiz task: a tap answers it
                    "task_id": str(task_by_message[s.message_id]) if s.message_id in task_by_message else None,
                    # a race: one question for everyone, first right tap wins
                    "race": s.id in snap["races"],
                }
                for s in open_option_sets(member_id, snap["sets"], snap["picks"])
                if snap["races"].get(s.id) is None  # a race closes the moment it is decided
            ],
            "scores": _scores(snap["members"], snap["graded"]),
            "parts": _parts(snap["room"], snap["done_parts"]),
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
        task_options = {
            s.message_id: [o.text for o in s.options] for s in snap["sets"] if s.message_id is not None
        }
        points = {s["member_id"]: s["points"] for s in _scores(members, snap["graded"])}
        member_ctx = []
        for m in members:
            mine = [t for t in tasks if t.member_id == m.id]
            open_ = [t for t in mine if t.id not in done]
            member_ctx.append({
                "name": m.name,
                "online": m.id in snap["online"],
                "current_task": (
                    {"kind": open_[0].kind, "description": open_[0].description,
                     "options": task_options.get(open_[0].message_id, [])} if open_ else None
                ),
                "done_count": sum(1 for t in mine if t.id in done),
                "points": points.get(str(m.id), 0),
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
                done_parts=sorted(snap["done_parts"]),
            )
            await self._apply(room, decision, members, tasks, done, snap["done_parts"])
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
        done_parts: set[int] | None = None,
    ) -> None:
        by_name = {m.name: m for m in members}
        done_parts = set(done_parts or ())
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
                # always a quiz: its options are that person's to tap, and the
                # right one stays on the server (message_out never sends it)
                for who in [target] if target else members:
                    set_id = uuid4()
                    msg = await self.store.add_message(
                        room.id, sender="versa", kind="task", text=action.text,
                        to_member_id=who.id,
                        meta={"task_kind": action.kind, "option_set_id": str(set_id),
                              "options": action.options, "answer": action.answer},
                    )
                    tasks.append(await self.store.add_task(room.id, who.id, action.kind, action.text, msg.id))
                    await self.store.add_option_set(set_id, room.id, who.id, action.text, action.options, msg.id)
                    await self._broadcast_message(room.id, msg)
                board_changed = True
            elif action.type == "part_done" and action.part is not None:
                if action.part in done_parts:
                    continue
                title = room.outline[action.part - 1]["title"]
                msg = await self.store.add_message(
                    room.id, sender="versa", kind="progress", text=title,
                    meta={"part": action.part, "part_title": title, "evidence": action.evidence},
                )
                done_parts.add(action.part)
                await self._broadcast_message(room.id, msg)
                board_changed = True
            elif action.type == "race":
                set_id = uuid4()
                msg = await self.store.add_message(
                    room.id, sender="versa", kind="question", text=action.prompt,
                    meta={"race": True, "option_set_id": str(set_id), "options": action.options,
                          "answer": action.answer},
                )
                await self.store.add_option_set(set_id, room.id, None, action.prompt, action.options, msg.id)
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


RACE_WIN_POINTS = 3
QUIZ_POINTS = 1


def _scores(members: list[MemberRow], graded: list[tuple[UUID, dict]]) -> list[dict]:
    """The scoreboard, derived from graded taps: a race won is worth
    RACE_WIN_POINTS, a quiz task answered right QUIZ_POINTS. Highest first."""
    by_id = {m.id: {"member_id": str(m.id), "name": m.name, "points": 0, "wins": 0} for m in members}
    for member_id, meta in graded:
        row = by_id.get(member_id)
        if row is None or not meta.get("correct"):
            continue
        if meta.get("race"):
            row["points"] += RACE_WIN_POINTS
            row["wins"] += 1
        else:
            row["points"] += QUIZ_POINTS
    return sorted(by_id.values(), key=lambda r: (-r["points"], r["name"].lower()))


def _parts(room: RoomRow | None, done: set[int]) -> list[dict]:
    """The topic's parts in order: covered, the one the group is on, or ahead."""
    if room is None:
        return []
    current = next((i + 1 for i in range(len(room.outline)) if i + 1 not in done), None)
    return [
        {"title": p.get("title", ""), "done": i + 1 in done, "current": i + 1 == current}
        for i, p in enumerate(room.outline)
    ]


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
        # a quiz task's right answer never leaves the server
        "meta": {k: v for k, v in msg.meta.items() if k != "answer"},
        "created_at": _iso(msg.created_at),
    }


def _transcript_line(msg: MessageRow, names: dict[UUID, str]) -> str:
    who = names.get(msg.member_id, "?") if msg.member_id else ""
    to = names.get(msg.to_member_id, "?") if msg.to_member_id else "everyone"
    if msg.sender == "system":
        return f"[{msg.seq}] ({msg.text})"
    if msg.sender == "member":
        if msg.kind == "pick":
            graded = ""
            if msg.meta.get("quiz"):
                graded = " -- RIGHT" if msg.meta.get("correct") else f' -- WRONG, right: "{msg.meta.get("right_answer")}"'
            return f'[{msg.seq}] {who} clicked: "{msg.text}" (answering: {msg.meta.get("prompt", "")}){graded}'
        return f"[{msg.seq}] {who}: {msg.text}"
    if msg.kind == "task":
        options = " | ".join(msg.meta.get("options") or [])
        return f"[{msg.seq}] Versa gave {to} a task: {msg.text}" + (f" (tap: {options})" if options else "")
    if msg.kind == "progress" and "race_set_id" in msg.meta:
        winner = msg.meta.get("winner")
        return (f'[{msg.seq}] RACE OVER: {winner} won (answer: "{msg.meta.get("right_answer")}")' if winner
                else f'[{msg.seq}] RACE OVER: nobody got it (answer: "{msg.meta.get("right_answer")}")')
    if msg.kind == "question" and msg.meta.get("race"):
        return f'[{msg.seq}] Versa started a RACE for everyone: {msg.text} (tap: {" | ".join(msg.meta.get("options") or [])})'
    if msg.kind == "progress" and msg.meta.get("part"):
        return f'[{msg.seq}] Versa marked part {msg.meta["part"]} covered: {msg.text}'
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
