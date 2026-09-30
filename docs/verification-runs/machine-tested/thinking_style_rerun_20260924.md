# Thinking-style promotion — live re-run (billed key)

UTC 2026-09-24 | real Gemini (billed key), real dev database, full
`build_session_loop` (every store wired, not the minimal loop the
2026-09-22 run used) | re-run of `thinking_style_AS_20260922.md`, which
never reached the mechanism because of the free-tier 20 req/day/model cap.

## Setup

- Fresh learner `AS-rerun-190438`, id `68856861-ebaa-4c7f-9ad7-7e0d41f27e82`.
- Same 5 sessions as the 09-22 plan: a direct question, then a "why does
  that actually work" follow-up, on 5 different subjects. When options were
  offered, the script clicked the first one. `consolidate_session` ran after
  each session.

## Result — mechanism verified (staged run)

| Session | Topic | Facts | Candidate status | Confirmations |
|---|---|---|---|---|
| 0 | photosynthesis (options offered, clicked) | 2 | candidate (created) | 1 |
| 1 | French Revolution | 2 | candidate | 2 |
| 2 | neural networks (options offered, clicked) | 2 | candidate | 3 |
| 3 | supply and demand | 2 | candidate | 4 |
| 4 | vaccines | 2 | **confirmed** | 5 |

- 12 turns, 0 failed, 2 option clicks, 92 `node_calls` rows.
- One candidate, matched across all 5 independent sessions, promoted at
  exactly `thinking_style_promotion_threshold = 5`:
  *"The student begins with a broad overview request, and once provided,
  seeks a deeper explanation focused on the underlying mechanisms and
  principles."*

## What this does and does not show

- **Shows:** the detect → compare → confirm → promote pipeline works end to
  end against real models, and the 09-22 blocker was purely quota.
- **Does not show:** that Versa adapted to a real student. The input was
  scripted to have the identical shape every time, so a match was the
  expected outcome.
- **Not yet tested:**
  1. **Negative control.** Would a learner with *varied* session shapes
     avoid a false promotion? The summary above is fairly generic ("overview,
     then deeper why"), which many students would produce, so this is the
     more important open check.
  2. **Effect on answers.** Whether the confirmed style actually reaches the
     `FinalAnswer` prompt in session 6 and changes the answer.
