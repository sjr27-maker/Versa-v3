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
  - "options": either `DisambiguationOptions` ran (and `FinalAnswer` did
    not, since an options-offered turn never also answers the same
    turn) — the fixed "which of these did you mean?" question plus the
    branches it actually offered, each carrying its CURRENT status —
    OR a `DisambiguationTurn` with `kind=REASON_CONFIRMATION` exists
    (IDEAS.md "ask for confirmation directly"): a REASON-based memory
    match offered directly as yes/no, which never calls
    DisambiguationOptions at all (its two options are fixed Python
    literals, not LLM output), so it needs this separate check, OR
    `kind=APPROACH_GUESS` exists (legacy rows from the removed Guess
    Mode, migrations 061/062): read-only, so old chats still open.
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
from versa.disambiguate import (
    REASON_CONFIRM_NO_TEXT,
    REASON_CONFIRM_YES_TEXT,
    REASON_REJECTED_STATEMENT,
    DisambiguationStore,
    render_reason_confirmation_message,
)
from versa.models import (
    BranchStatus,
    DisambiguationTurnKind,
    HistoryOption,
    HistoryTurn,
)

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
    review_store=None,
    claim_store=None,
    answer_versions=None,
) -> list[HistoryTurn]:
    """The full session, chronological, one `HistoryTurn` per row in
    `turns`. See this module's own docstring for how `kind` is decided.

    `review_store` is optional (None reproduces the pre-claim-update
    behavior exactly, every `claim_update` field simply None) so every
    other caller of this function keeps working unchanged; server.py's
    `GET /api/sessions/{id}/history` is the one caller that passes it."""
    turns = await transcript.list_turns(session_id)
    # A turn rewritten with the length/depth sliders shows its latest
    # version (answer_versions, migration 071), as the live chat did.
    latest = await answer_versions.latest_by_turn(session_id) if answer_versions is not None else {}
    out = []
    for t in turns:
        turn = await _reconstruct_turn(node_calls, disambiguation, session_id, t.turn_index, t.text)
        if turn.kind == "answer" and t.turn_index in latest:
            turn.tutor_text = latest[t.turn_index][1]
        if review_store is not None:
            turn.claim_update = await _reconstruct_claim_update(
                review_store, claim_store, session_id, t.turn_index
            )
        out.append(turn)
    return out


async def _reconstruct_claim_update(
    review_store, claim_store, session_id: UUID, turn_index: int
) -> dict | None:
    reviews = await review_store.list_sandbox_chat_reviews_for_turn(session_id, turn_index)
    if not reviews:
        return None
    review = reviews[-1]  # a turn causes at most one; last-wins is defensive only
    statement = review.revised_statement
    if statement is None and claim_store is not None:
        claim = await claim_store.get(review.claim_id)
        if claim is not None:
            current = await claim_store.get_current_statement(claim.id)
            statement = current.statement if current else claim.statement
    return {
        "kind": "claim", "action": review.review_type, "claim_id": str(review.claim_id),
        "statement": statement, "review_id": str(review.id),
    }


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


async def _was_click_continuation(
    disambiguation: DisambiguationStore, session_id: UUID, turn_index: int, student_text: str
) -> bool:
    """True when THIS options-offering turn was itself a click resolving
    the immediately preceding turn's offer -- only legacy chats from the
    removed Guess Mode have this shape, the one case where
    an "options" turn's own `turns.text` is a system-authored option
    copy, not the student's words, the same distinction the "answer"
    branch above already makes for a click that led straight to an
    answer. There is no FinalAnswer call to read `branch_context` off
    here (that is the whole point of a continuation -- it asks again
    instead of answering), so this checks the one other durable signal
    for "this text was a click": a MATCHED branch, from the turn right
    before this one, whose statement is exactly this text. Turn 0 can
    never be a continuation (no earlier turn to have clicked)."""
    if turn_index == 0:
        return False
    prior_turn = await disambiguation.get_turn_for_index(session_id, turn_index - 1)
    if prior_turn is None:
        return False
    prior_branches = await disambiguation.list_branches_for_turn(prior_turn.id)
    return any(
        b.status is BranchStatus.MATCHED and b.statement == student_text
        for b in prior_branches
    )


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
        # A reason-confirmation click (IDEAS.md "ask for confirmation
        # directly") is ALSO system-authored turn text, but always
        # passes branch_context=None -- that path is only ever reached
        # via one of these two fixed, code-owned literals, so checking
        # student_text against them directly is the same safe pattern
        # REASON_CONFIRM_YES_TEXT's own comment establishes for loop.py's
        # click resolution.
        was_click_resolution = (
            final.input_json.get("branch_context") is not None
            or student_text in (REASON_CONFIRM_YES_TEXT, REASON_CONFIRM_NO_TEXT)
        )
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
        continuation = await _was_click_continuation(
            disambiguation, session_id, turn_index, student_text
        )
        return HistoryTurn(
            turn_index=turn_index,
            student_text=None if continuation else student_text,
            kind="options",
            options_message=OPTIONS_QUESTION,
            options=options,
        )

    # No DisambiguationOptions call recorded -- still check for a
    # reason-confirmation offer, which never calls that node (its two
    # options are the fixed Yes/No literals above, not LLM-generated --
    # see loop.py's reason-match branch).
    disamb_turn = await disambiguation.get_turn_for_index(session_id, turn_index)
    if disamb_turn is not None and disamb_turn.kind is DisambiguationTurnKind.REASON_CONFIRMATION:
        branches = await disambiguation.list_branches_for_turn(disamb_turn.id)
        reason_text = next(
            (b.statement for b in branches if b.statement != REASON_REJECTED_STATEMENT), ""
        )
        options = [
            HistoryOption(id=o.id, text=o.text, status=o.status)
            for o in await disambiguation.list_options_for_turn(disamb_turn.id)
        ]
        return HistoryTurn(
            turn_index=turn_index,
            student_text=student_text,
            kind="options",
            options_message=render_reason_confirmation_message(reason_text),
            options=options,
        )

    # Legacy read-only path for the removed Guess Mode's approach-guess
    # offers (migrations 061/062): no DisambiguationOptions call exists.
    if disamb_turn is not None and disamb_turn.kind is DisambiguationTurnKind.APPROACH_GUESS:
        options = [
            HistoryOption(id=o.id, text=o.text, status=o.status)
            for o in await disambiguation.list_options_for_turn(disamb_turn.id)
        ]
        continuation = await _was_click_continuation(
            disambiguation, session_id, turn_index, student_text
        )
        return HistoryTurn(
            turn_index=turn_index,
            student_text=None if continuation else student_text,
            kind="options",
            options_message="Let me guess how you'd like this explained:",
            options=options,
        )

    return HistoryTurn(turn_index=turn_index, student_text=student_text, kind="pending")
