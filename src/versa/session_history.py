"""Reconstructing a session's turn-by-turn record for a client that is
RESUMING a chat — a reload of the app, or a past chat reopened from the
sidebar (server.py's `GET /api/sessions/{id}/history`).

This is a read-side assembly, the same category as `history_block.py`'s
retrieval rendering, NOT a second write path or a new source of truth:
every field comes back out of a store that already writes it —
`TranscriptStore` for what the student's bubble said (`turns.text`,
the right display text for a typed message, but NOT for a
click-resolution — see `_reconstruct_turn`'s own comment on why that
one case needs `node_calls` too), `NodeCallStore` for what the tutor actually said
(CLAUDE.md invariant 2's `node_calls`, the same table
`SessionLoop._build_disambiguation_history` already reads back for its
own compact recent-history window — this module does the same read over
the WHOLE session instead of the last few turns), and
`DisambiguationStore` for which reading, if any, is still waiting on a
click (`disambiguation_options.status`, read live, never re-derived —
a resumed view can never show a button as clickable that the running app
would have refused).

Deliberately does NOT reach into `SessionLoop`'s own attributes
(`loop._transcript` etc.) — see `scripts/measure_turn.py`'s own docstring
for why that pattern is fine for a measuring tool but not for production
code: this module takes the three stores it needs as plain arguments,
the same way `server.py` already constructs its own `TranscriptStore`/
`DisambiguationStore` instances rather than reaching into the loop's.

A turn a real conversation went through resolves to exactly one of three
kinds:
  - "answer": `FinalAnswer` ran and returned a recorded string — the
    common case, a direct reply.
  - "options": `DisambiguationOptions` ran (and `FinalAnswer` did not,
    since an options-offered turn never also answers the same turn) —
    the fixed "which of these did you mean?" question plus the branches
    it actually offered, each carrying its CURRENT status.
  - "pending": neither ran, or `FinalAnswer` raised before its call was
    ever recorded (a live failure the student saw as an in-band error
    message that itself is never persisted, by `_run_final_answer`'s own
    design — see loop.py). A resumed view shows this turn had no
    recorded response, rather than fabricating the live failure wording
    it can no longer recover verbatim.
"""

from __future__ import annotations

from uuid import UUID

from versa.audit import NodeCallStore, TranscriptStore
from versa.disambiguate import DisambiguationStore
from versa.models import HistoryOption, HistoryTurn

# The fixed message loop.py shows in place of an answer whenever a turn's
# AssessAndBranch judged the message ambiguous — see disambiguate.py's
# module docstring, step 2. Duplicated as a literal, not imported from
# loop.py, on purpose: it is loop.py's own UI-facing string, not a
# constant this module should depend on the internals of.
OPTIONS_QUESTION = "Which of these did you mean?"


async def reconstruct_session_history(
    transcript: TranscriptStore,
    node_calls: NodeCallStore,
    disambiguation: DisambiguationStore,
    session_id: UUID,
) -> list[HistoryTurn]:
    """The full session, chronological, one `HistoryTurn` per row in
    `turns`. See this module's own docstring for how `kind` is decided."""
    turns = await transcript.list_turns(session_id)
    return [
        await _reconstruct_turn(node_calls, disambiguation, session_id, t.turn_index, t.text)
        for t in turns
    ]


async def latest_open_options(
    transcript: TranscriptStore,
    node_calls: NodeCallStore,
    disambiguation: DisambiguationStore,
    session_id: UUID,
) -> list[HistoryOption]:
    """The still-open readings (if any) of this session's LATEST turn —
    what a client asks after reconnecting to know whether it should show
    buttons, without reconstructing the whole conversation. Only ever
    non-empty for the latest turn: once a later turn exists, every
    earlier options-turn was necessarily resolved (a click, or typed-past
    superseding every branch) for the conversation to have continued.
    """
    turns = await transcript.list_turns(session_id)
    if not turns:
        return []
    latest = turns[-1]
    turn = await _reconstruct_turn(
        node_calls, disambiguation, session_id, latest.turn_index, latest.text
    )
    if turn.kind != "options":
        return []
    return [o for o in turn.options if o.status.value == "open"]


async def _reconstruct_turn(
    node_calls: NodeCallStore,
    disambiguation: DisambiguationStore,
    session_id: UUID,
    turn_index: int,
    student_text: str,
) -> HistoryTurn:
    final = await node_calls.get_call_for_turn(session_id, turn_index, "FinalAnswer")
    if final is not None and isinstance(final.output_json, str):
        # `turn_text` == turns.text is the clicked option's own copy,
        # never the student's words, on EXACTLY the turns where
        # `_finish_turn_with_fact` (loop.py) passed FinalAnswer a real
        # branch_context -- the other three call sites into it (a
        # memory-confirmed shortcut, an unambiguous direct answer, the
        # empty-options fallback) all pass branch_context=None, and
        # those ARE the student's own typed message. `branch_context`
        # is always one of FinalAnswer's own kwargs (never omitted), so
        # it is always a key in this call's recorded input_json --
        # reading it back is exactly the same click/no-click signal
        # loop.py itself relies on, not a new one invented here.
        was_click_resolution = final.input_json.get("branch_context") is not None
        return HistoryTurn(
            turn_index=turn_index,
            student_text=None if was_click_resolution else student_text,
            kind="answer",
            tutor_text=final.output_json,
        )

    options_call = await node_calls.get_call_for_turn(
        session_id, turn_index, "DisambiguationOptions"
    )
    if options_call is not None:
        disamb_turn = await disambiguation.get_turn_for_index(session_id, turn_index)
        options = (
            [
                HistoryOption(id=o.id, text=o.text, status=o.status)
                for o in await disambiguation.list_options_for_turn(disamb_turn.id)
            ]
            if disamb_turn is not None
            else []
        )
        return HistoryTurn(
            turn_index=turn_index,
            student_text=student_text,
            kind="options",
            options_message=OPTIONS_QUESTION,
            options=options,
        )

    return HistoryTurn(turn_index=turn_index, student_text=student_text, kind="pending")
