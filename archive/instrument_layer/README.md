# Parked: the instrument layer (locate + predict)

Removed from the live architecture on 2026-09-21 ahead of a redesign, kept
here in case it's useful later. Nothing in `src/probe/` imports it, and
pytest only collects `tests/` (see `[tool.pytest.ini_options]`), so this
directory is inert.

## What it is
Purpose-built mini-exercises (`locate`: spot the seeded error in a worked
solution; `predict`: guess a swap's outcome before it's revealed) whose event
stream is turned into supports / contradicts / uninformative by a
hand-written, deterministic contract (no LLM). Evidence lands in a separate
capability-claim store, kept apart from preference claims so "can do X" never
gets counted as "prefers X".

## Contents
- `src/instruments.py`, `src/capability.py`, `src/method_capabilities.py` —
  the three modules, verbatim.
- `src/models_instrument_layer.py` — the row shapes that lived in
  `probe/models.py` (everything after its "instrument layer" marker).
- `src/loop_instrument_methods.py.txt` — SessionLoop's three methods
  (`present_instrument_turn`, `record_instrument_event`,
  `finalize_instrument_turn`); they were class members, hence `.txt`.
- `src/score_predictions_capability_reader.py.txt` — the capability-side
  calibration reader that lived in `probe/score_predictions.py`.
- `tests/` — the ten test files that covered all of the above.

## To restore
1. Copy the three modules back into `src/probe/`.
2. Append `models_instrument_layer.py`'s body to `probe/models.py` (re-add
   `field_validator, model_validator` to its pydantic import).
3. Paste the loop methods back inside `class SessionLoop`
   (re-add `InteractionStore` to loop.py's `probe.interactions` import).
4. Append the score_predictions reader (+ its imports) and, if wanted,
   the `--split-by-source` flag in `cli.py`.
5. Copy `tests/` back; run the suite.

## The database was NOT touched
Migrations 049-054 are append-only history and stay in
`src/probe/migrations/`. Their tables (`interaction_contracts`, `instruments`,
`instrument_events`, `capability_claims`, `capability_evidence`) and the
`source='instrument'` value on `claim_evidence` remain in the schema, dormant.

## Not archived here
The four later primitives (order / adjust / choose / construct), the
in-session instrument routes and auto-trigger, and the portrait / noticing /
corrections work were uncommitted when this was done. They are in a git stash
(`git stash list`) — not in the tree.
