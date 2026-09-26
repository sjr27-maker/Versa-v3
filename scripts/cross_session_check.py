"""Does what Versa learned in one chat change a LATER chat? (IDEAS.md
section 4, "Cross-session adaptation & reference checks".)

Runs real Gemini + embeddings against the dev database (DATABASE_URL):

    learner A, chat 1   states a preference, names a project, resolves an
                        ambiguity -- then ends (consolidation runs, as the
                        app's "end chat" does)
    learner A, chat 2   fresh chat: a project reference, a reworded repeat of
                        the ambiguity, a reference to the earlier chat
    control,   chat 2   a brand-new learner sends the SAME chat-2 messages

For every chat-2 turn it reads back what actually reached the answer:
`turn_diagnostics` (memory match, history block, reference bindings) and the
recorded `FinalAnswer` input (which personalization blocks were non-empty),
and puts the two learners' answers side by side.

    uv run python scripts/cross_session_check.py [--out report.md]

Chats are created as ordinary Sandbox sessions, so they show up in the app
under the learners' names.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from uuid import UUID

from dotenv import load_dotenv

from versa.db import create_pool
from versa.embeddings import build_embedding_client
from versa.learner import LearnerStore
from versa.llm import build_tier_clients
from versa.session_builder import build_session_loop

CHAT_1 = [
    (
        "Hi! I'm building a Flask app for my mom's bakery so customers can order cakes "
        "online. Please keep your answers short and use everyday analogies - long walls "
        "of text lose me."
    ),
    "How should I store the orders - SQLite or Postgres?",
    "can you help me with derivatives?",
    "ok, what's the derivative of x^3 then?",
    (
        "Back to the bakery app - how do I send the customer a confirmation email when "
        "they order?"
    ),
    "thanks, that makes sense",
]

CHAT_2 = [
    "How do I add a login page to my project?",
    "can you help me with derivatives again?",
    "Last time we talked about storing the orders - which one did we pick, and why?",
]

# FinalAnswer inputs that carry cross-session personalization.
BLOCK_KEYS = (
    "learner_history_block",
    "structural_requirement",
    "reference_bindings_block",
    "claim_constraints_block",
    "thinking_style_hint",
)

out_lines: list[str] = []


def _j(value):
    """jsonb may arrive decoded (the pool's codec) or as text."""
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def emit(line: str = "") -> None:
    print(line, flush=True)
    out_lines.append(line)


def write_report(path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")


async def play(loop, session_id: UUID, messages: list[str], *, click_options: bool) -> list[dict]:
    """Send each message; when options come back, click the first one (an
    extra turn). Returns one record per turn."""
    turns: list[dict] = []
    idx = 0
    for text in messages:
        start = time.monotonic()
        reply = await loop.handle_turn(session_id, idx, text)
        await loop.wait_for_background_tasks()
        options = await loop.pending_options(session_id)
        turns.append({
            "turn": idx, "sent": text, "reply": reply,
            "options": [o.text for o in options], "secs": time.monotonic() - start,
        })
        idx += 1
        if options and click_options:
            pick = options[0]
            start = time.monotonic()
            reply = await loop.handle_turn(session_id, idx, pick.text, selected_option_id=pick.id)
            await loop.wait_for_background_tasks()
            turns.append({
                "turn": idx, "sent": f"[clicked] {pick.text}", "reply": reply,
                "options": [o.text for o in await loop.pending_options(session_id)],
                "secs": time.monotonic() - start,
            })
            idx += 1
    return turns


async def annotate(pool, session_id: UUID, turns: list[dict]) -> None:
    async with pool.acquire() as conn:
        for t in turns:
            d = await conn.fetchrow(
                "SELECT memory_match_found, memory_match_confirmed_resolution, "
                "branching_skipped_by_memory, memory_match_via, history_block_used, "
                "jsonb_array_length(history_block_source_ids) AS n_hist, "
                "reference_bindings_injected, reason_confirmation_offered, warnings "
                "FROM turn_diagnostics WHERE session_id=$1 AND turn_index=$2",
                session_id, t["turn"],
            )
            t["diag"] = dict(d) if d else {}
            fa = await conn.fetchrow(
                "SELECT input_json FROM node_calls WHERE session_id=$1 AND turn_index=$2 "
                "AND node_name='FinalAnswer' ORDER BY seq DESC LIMIT 1",
                session_id, t["turn"],
            )
            inp = (_j(fa["input_json"]) or {}) if fa else {}
            t["blocks"] = {k: inp.get(k) or "" for k in BLOCK_KEYS if k in inp}


def fmt_diag(d: dict) -> str:
    if not d:
        return "(no diagnostics row)"
    bits = [
        f"memory_match={d['memory_match_found']}"
        + (f" via {d['memory_match_via']}" if d["memory_match_via"] else ""),
        f"confirmed={d['memory_match_confirmed_resolution']}",
        f"options_skipped_by_memory={d['branching_skipped_by_memory']}",
        f"history_block={d['history_block_used']} ({d['n_hist']} items)",
        f"reason_q={d['reason_confirmation_offered']}",
    ]
    refs = _j(d["reference_bindings_injected"]) or []
    if refs:
        bits.append(f"ref_bindings={refs}")
    warns = _j(d["warnings"]) or []
    if warns:
        bits.append(f"warnings={warns}")
    return ", ".join(bits)


def report_turns(title: str, turns: list[dict], *, show_blocks: bool) -> None:
    emit(f"\n### {title}\n")
    for t in turns:
        emit(f"**turn {t['turn']}** ({t['secs']:.1f}s) - sent: {t['sent']}")
        if t["options"]:
            emit(f"- OPTIONS offered: {t['options']}")
        emit(f"- diagnostics: {fmt_diag(t.get('diag', {}))}")
        if show_blocks and t.get("blocks"):
            present = {k: len(v) for k, v in t["blocks"].items() if v}
            emit(f"- personalization reaching FinalAnswer (chars): {present or 'none'}")
            for k, v in t["blocks"].items():
                if v:
                    emit(f"  - `{k}`:\n\n```\n{v.strip()[:1200]}\n```")
        emit(f"- reply ({len(t['reply'].split())} words):\n\n> "
             + t["reply"].strip().replace("\n", "\n> ") + "\n")


async def learned_state(pool, learner_id: UUID) -> None:
    async with pool.acquire() as conn:
        emit("\n### What was stored about learner A after chat 1\n")
        for r in await conn.fetch(
            "SELECT fact_type, situation, resolution, reason FROM learner_facts "
            "WHERE learner_id=$1 ORDER BY created_at", learner_id):
            emit(f"- fact [{r['fact_type']}]: {r['situation']} -> {r['resolution']}"
                 + (f" (reason: {r['reason']})" if r["reason"] else ""))
        for r in await conn.fetch(
            "SELECT label, stated_preference FROM stated_preferences "
            "WHERE learner_id=$1 AND has_preference ORDER BY seq", learner_id):
            emit(f"- stated preference [{r['label']}]: {r['stated_preference']}")
        for r in await conn.fetch(
            "SELECT reference_text, resolved_to FROM reference_bindings "
            "WHERE learner_id=$1 ORDER BY seq", learner_id):
            emit(f"- reference binding: '{r['reference_text']}' -> {r['resolved_to']}")
        for r in await conn.fetch(
            "SELECT statement, status, confidence FROM claims WHERE learner_id=$1", learner_id):
            emit(f"- claim [{r['status']}, conf {r['confidence']}]: {r['statement']}")
        for r in await conn.fetch(
            "SELECT * FROM thinking_style_candidates WHERE learner_id=$1", learner_id):
            emit(f"- thinking-style candidate [{r['status']}, {r['confirmation_count']}x]: {r['path_summary']}")


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None, help="also write the report to this file")
    args = parser.parse_args()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "your-key-here":
        sys.exit("GEMINI_API_KEY is not set in .env")

    stamp = time.strftime("%H%M")
    pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=6)
    try:
        loop = build_session_loop(pool, build_tier_clients(api_key), build_embedding_client(api_key))
        learners = LearnerStore(pool)
        a = await learners.create(label=f"Riya (cross-session {stamp})")
        ctrl = await learners.create(label=f"Control (cross-session {stamp})")
        emit(f"# Cross-session adaptation check - {time.strftime('%Y-%m-%d %H:%M')}\n")
        emit(f"learner A `{a.label}` = {a.id}  \ncontrol `{ctrl.label}` = {ctrl.id}")

        s1 = await loop._transcript.create_session(a.id, ablation_config=loop.ablation_config)
        chat1 = await play(loop, s1, CHAT_1, click_options=True)
        await annotate(pool, s1, chat1)
        # what the app's "end chat" does (server.py end_session)
        if await loop._transcript.claim_for_consolidation(s1):
            await loop.consolidate_session(s1)
            await loop.wait_for_background_tasks()
        report_turns("Learner A - chat 1", chat1, show_blocks=False)
        await learned_state(pool, a.id)

        s2 = await loop._transcript.create_session(a.id, ablation_config=loop.ablation_config)
        chat2 = await play(loop, s2, CHAT_2, click_options=True)
        await annotate(pool, s2, chat2)
        sc = await loop._transcript.create_session(ctrl.id, ablation_config=loop.ablation_config)
        control = await play(loop, sc, CHAT_2, click_options=True)
        await annotate(pool, sc, control)

        report_turns("Learner A - chat 2 (new chat)", chat2, show_blocks=True)
        report_turns("Control learner - same messages, no history", control, show_blocks=True)
        emit(f"\nsessions: chat1={s1} chat2={s2} control={sc}")
    finally:
        await pool.close()
        if args.out:
            write_report(args.out)


if __name__ == "__main__":
    asyncio.run(main())
