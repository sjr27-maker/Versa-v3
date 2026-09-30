# versa

## Core claim — read this first

Versa's core feature is that it **learns a person's thinking style over
time**: how they move through ideas (where they start, in what order, which
way, within what depth/breadth limits), stable across topics, set apart from
their mood and ability, and proven when it predicts their next unsteered
choice. Everything else is an evidence source for that or a use of it:

- **"Where this could go" (directions.py) is the primary evidence source** —
  the person recognises the path that matches what's already in their mind
  instead of having to write it. It stays unpersonalised (invariant 14): it
  is the measuring instrument.
- Also feeding it: the length/depth/breadth sliders (their own range),
  stated preferences, the ambiguity and approach options, and Sandbox chat,
  Learn a topic and Exam prep (exam prep is still walled off by invariant 13
  — an open decision). Study with others does not feed it.

`docs/THINKING_STYLE.md` has the agreed definition, the evidence inventory,
the layered design and what is and isn't proven yet. Read it before
changing anything that records, derives or uses learner evidence.

## Setup

Copy `.env.example` to `.env` and fill in:

- `DATABASE_URL` — Postgres connection string for the dev database. The
  test suite does NOT use it: it wipes and rebuilds the database named by
  `VERSA_TEST_DATABASE_URL` (a separate `versa_test`; if that is unset it
  falls back to a default that can be the dev database, so set it). For a
  real, persistent database (Cloud SQL, staging),
  apply the schema with `versa migrate` — it runs every
  `src/versa/migrations/*.sql` in order, once each, tracked in a
  `schema_migrations` ledger table, and is safe to re-run. `versa
  migrate --status` shows applied/pending; `versa migrate --baseline`
  adopts a database that already has the full schema but no ledger
  (stamps every migration as applied without running it). `pytest`
  does not use this path.
- `GEMINI_API_KEY` — required for any real (non-stub) LLM call. Get one
  at https://aistudio.google.com/apikey. Every `versa` command that
  calls an LLM (`chat`, `consolidate-session`) accepts `--stub` to run against
  `StubLLMClient` instead, which needs no key and costs nothing.

`GEMINI_MODEL_FAST` / `GEMINI_MODEL_CAPABLE` / `GEMINI_MODEL_BEST` are
optional overrides for the tier→model mapping in `model_config.py` —
only needed if the defaults there go stale (Gemini preview model ids
shift over time).

Entry points: `versa chat` and the other `versa` CLI commands
(`consolidate-session`, `migrate`, `review-claims`, `score-predictions`,
`compare-portraits`, ...), and `versa serve` — the HTTP/WebSocket API the
Flutter app in `app/` talks to (`src/versa/server.py`). Sign-in is ON by
default (`src/versa/accounts.py`: Firebase Google/email tokens, invite-only
sign-up, name-only sign-in for the testers sooraj/adithya on a laptop) and a
guard checks that every request only touches the signed-in learner's own
things; `VERSA_AUTH=off` gives the old open server, allowed only on
127.0.0.1. `docs/DEPLOY.md` covers Cloud Run, Firebase, invites and the
Android build. Every entry point builds its `SessionLoop` through
`session_builder.build_session_loop`, the one shared assembly point.

## Invariants

### 1. The hypothesis store is append-only

The `HypothesisStore` (and any store that persists reasoning state) must
never delete rows. Concretely:

- No `delete` / `remove` methods on the store class.
- No `DELETE` SQL anywhere in the store module or its migrations.
- Retirement is modeled by moving rows to the `archived` tier, not by
  removing them. Archived hypotheses can be brought back with
  `resurrect()`.
- Evidence is appended, never mutated in place. `reweight()` records new
  probability/confidence *and* appends the evidence ref that justified
  the update — it does not overwrite prior evidence.

Why: Versa's whole premise is auditing how beliefs evolved over time.
Deleting a hypothesis or overwriting its evidence destroys the record
we're trying to build. If something "shouldn't be there anymore," archive
it; the trail matters more than tidiness.

### 2. Every node call is persisted to `node_calls`

Every invocation of a Node's primary async method (`Node.run(...)`) must
be recorded to the `node_calls` table with:

- `node_name`
- `session_id`
- `turn_index`
- `input_json`  (kwargs to `run()`, JSON-serialized; non-serializable
  dependencies like `HypothesisStore` are excluded)
- `output_json` (return value, JSON-serialized)
- `timestamp`

This is enforced by routing every node call through
`SessionLoop._call_node()` rather than invoking `node.run(...)` directly.
Do not call `node.run(...)` from production code paths outside the loop.

Why: the audit trail is the product. If a node executed and we don't
have its inputs+outputs on disk, we can't reconstruct how a belief
changed — which defeats the point of building versa in the first place.

### 3. Value-function terms are individually disable-able

Every term in the `ValueFunction` (learning_value, information_value,
long_term_value, time_cost, cognitive_cost, frustration_risk) must be
independently toggle-able via `ValueFunctionConfig` without any code
changes. A disabled term contributes 0 to `score()` and skips its LLM
calls entirely. The full six-term breakdown must survive on every
`ActionScore` even when some terms are zero, so ablation runs are
comparable side-by-side in `node_calls`.

Why: this codebase is a research artifact whose main question is which
terms actually matter. If turning one off requires editing code, we
can't run apples-to-apples ablations. Keep the config knob, keep the
breakdown, don't collapse to a single float.

### 4. RETIRED — the concept graph is gone

This invariant governed `ConceptGraph` and `concept_nodes`, both of
which no longer exist: `src/versa/concept_graph.py` and
`src/versa/seed.py` were deleted in commit 5451b95, and migration
`032_retire_full_mode.sql` drops `concept_nodes`, `concept_graphs`,
`concept_prerequisites`, and `learner_overlay`. There is no
`ConceptNode` model, no `ConceptGraph` class, and no `versa seed-graph`
command; `versa chat` reports "minimal_branch mode — no concept graph".

The entry is kept as a numbered tombstone rather than deleted so the
later invariants keep the numbers they are cross-referenced by —
invariants 6-11 each say "the same AST-based check used for invariants
1, 4, ..." and renumbering would silently break every one of those
references.

Do not restore the concept graph in order to satisfy a task that
assumes it exists. It was removed on measured evidence (it lost to a
plain-LLM baseline at 20-40x the cost), and nothing in the current
architecture reads one — so any structure rebuilt here would have zero
consumers by construction.

### 5. World-model revisions are append-only

`WorldModelRevisionStore` must never delete rows. Concretely:

- No `delete` / `remove` methods on the class.
- No `DELETE` SQL anywhere in the `revision` module or its migrations.
- Resolution is modeled by moving a revision's `status` from `pending`
  to `approved` or `rejected` via UPDATE, not by removing it. A
  rejected revision stays on record as a rejected claim, not as if it
  had never been proposed.
- `approve()` never overwrites `proposed_change` — it records the
  human-confirmed structured edit separately, as
  `applied_field_updates`, on the same row. The original free-text
  claim and the edit that was actually applied both remain visible.

This is a separate invariant from #1 and #4, not a restatement of
either: the rationale is again distinct, so it gets its own entry.

Why: a `WorldModelRevision` is a claim about the concept graph, evidence-
backed the same way a `Hypothesis` is a claim about the learner — and
Versa's premise (invariant 1) is auditing how *all* beliefs evolved, not
just learner-facing ones. Deleting a resolved revision would erase the
record of what was proposed, what a human decided about it, and why —
exactly the trail that makes a rejected-but-plausible claim or an
approved edit's justification recoverable later.

### 6. The branch store is append-only

`BranchStore` (`branches`/`branch_generations`) must never delete rows.
Concretely:

- No `delete` / `remove` methods on the class.
- No `DELETE` SQL anywhere in the `branches` module or its migrations.
- Resolution is modeled by moving a branch's `status` from `open` to
  `matched`, `unmatched`, or `superseded` via UPDATE, not by removing
  it. A generation that turned out to predict nothing right stays on
  record as `unmatched`, not as if it had never been generated.
- Verified by the same AST-based check used for invariants 1 and 4.

This is a separate invariant from #1, not a restatement of it: the
rationale is different, so it gets its own entry rather than being
folded into the hypothesis-store rule.

Why: `HypothesisGenerator` rebuilds a speculative prediction tree every
turn and discards most of it — but "discard" means retiring branches to
a terminal status, the same way a hypothesis retires to `archived`
rather than disappearing, not erasing the record that a generation
happened and what it predicted. Crucially, this store does **not**
write into `HypothesisStore`: a branch match is a single-turn,
episodic signal, and Versa's premise (invariant 1) is auditing
*confirmed*, evidence-backed belief — promoting a branch into a real
`Hypothesis` on one coincidental match would let episodic noise
corrupt that durable record. Only a future consolidation step (not
built yet — deliberately deferred until real match data exists to
define "a pattern that repeats") would ever bridge the two; until
then, the wall between episodic branches and durable hypotheses is
itself part of what this invariant protects.

### 7. Turn diagnostics and hypothesis tier changes are append-only

`TurnDiagnosticsStore` (`turn_diagnostics`) and
`HypothesisStore.list_tier_changes`'s backing table
(`hypothesis_tier_changes`) must never delete rows. Concretely:

- No `delete` / `remove` methods on either class.
- No `DELETE` SQL anywhere in `diagnostics.py`, or in `store.py`'s
  `hypothesis_tier_changes` writes, or in either one's migrations.
- `turn_diagnostics` has one row per `handle_turn()` call, written
  once, never updated afterward — a turn's recorded diagnostics
  (call counts, whether the guardrail fired, warnings, whether Teach
  failed) are a historical fact the instant that turn finishes.
- `hypothesis_tier_changes` only ever grows via `retier()`'s INSERT on
  a real transition — never mutated or pruned.
- Verified by the same AST-based check used for invariants 1, 4, and 6.

This is a separate invariant from the others, not a restatement of
any of them: `turn_diagnostics` and `hypothesis_tier_changes` are
audit trail in the same spirit as `node_calls` (invariant 2), but they
didn't exist when that invariant was written and are new stores in
their own right, so they get their own entry rather than being
silently folded into invariant 2's wording after the fact.

Why: both exist specifically so a UI can *read* what already
happened instead of recomputing it — a per-turn call-count breakdown,
whether `MAX_CALLS_PER_TURN` fired, a hypothesis's tier history. If
either could be edited or pruned, "zero business logic in the UI"
would quietly stop being true: a UI that can't trust the record to be
complete has to start re-deriving things itself, which is exactly the
failure mode this whole feature was built to avoid.

### 8. The options store is append-only

`OptionStore` (`options`) must never delete rows. Concretely:

- No `delete` / `remove` methods on the class.
- No `DELETE` SQL anywhere in the `options` module or its migrations.
- Resolution is modeled by moving an option's `status` from `open` to
  `selected` or `superseded` via UPDATE, not by removing it. An option
  the student never clicked stays on record as `superseded`, not as if
  it had never been offered.
- Verified by the same AST-based check used for invariants 1, 4, 6,
  and 7.

This is a separate invariant from invariant 6, not a restatement of
it: options and branches are different tables with different
rationale, so it gets its own entry rather than being folded into the
branch-store wording.

Why: an option is the record of what was actually offered to the
student and which claim they did or didn't affirm — the evidence trail
for `Branch.evidence_satisfied`. Deleting a superseded option would
erase proof that a specific, unambiguous choice was on the table and
not taken, which is exactly the kind of fact CLAUDE.md invariant 1's
"audit how beliefs evolved" premise depends on, extended to the
options channel: an option is not written into `HypothesisStore`
either (same wall as invariant 6 describes for branches) — it only
ever flips `evidence_satisfied` on the branch it maps to.

### 9. The disambiguation store is append-only

`DisambiguationStore` (`disambiguation_turns`/`disambiguation_branches`/
`disambiguation_options`) must never delete rows. Concretely:

- No `delete` / `remove` methods on the class.
- No `DELETE` SQL anywhere in the `disambiguate` module or its
  migrations.
- Resolution is modeled by moving a branch's or option's `status` from
  `open` to `matched`/`selected` or `superseded` via UPDATE, not by
  removing it — same transitions as invariants 6 and 8, on this mode's
  own tables.
- `disambiguation_turns` is written once per `AssessAndBranch` call,
  unconditionally, whether or not `needs_branches` fires — a turn
  judged unambiguous is still a queryable row with zero branches, not
  a gap.
- Verified by the same AST-based check used for invariants 1, 4, 6, 7,
  and 8.

This is a separate invariant from invariants 6 and 8, not a
restatement of either: `ReasoningMode.DISAMBIGUATE` (see ablation.py)
is a wholly separate reasoning architecture from the branch tree/
options system those invariants describe, running against its own
parallel tables rather than the `branches`/`options` tables — see
disambiguate.py's module docstring for why those tables couldn't be
literally reused (`options.branch_id`'s FK to `branches(id)`) and,
more importantly, why they shouldn't be: the existing tree-based
system still depends on every column `branches` already has, and this
new mode must not require altering it.

Why: same rationale as invariants 6 and 8, extended to this mode — a
distinct reading the student typed past, or a branch set generated for
a message later judged unambiguous, is still a fact about how this
mode reasoned about that turn. Deleting any of it would erase the same
kind of "what was actually offered, and what happened to it" trail
invariant 8 protects, just for a different architecture.

### 10. The memory layer is append-only

`LearnerFactStore` (`learner_facts`) and `ThinkingStyleStore`
(`thinking_style_candidates`) must never delete rows. Concretely:

- No `delete` / `remove` methods on either class.
- No `DELETE` SQL anywhere in the `memory` module or its migrations.
- `learner_facts` rows are never mutated after insert — a fact is a
  historical record of what was true at that turn, written once by
  `WriteLearnerFact`, read many times by later searches, never edited.
- A `thinking_style_candidates` row's `status` moves `candidate` ->
  `confirmed`/`retired` via UPDATE only, same resurrection-over-
  deletion principle as `HypothesisStore`'s tiers — a candidate that
  stops matching is retired, not erased, and `confirmation_count`/
  `session_ids` only ever grow, via `ThinkingStyleStore.confirm()`.
- Verified by the same AST-based check used for invariants 1, 4, 6, 7,
  8, and 9.
- (2026-09-30) The free-text thinking-style detector that wrote
  `thinking_style_candidates` is retired: nothing writes the table and
  nothing reads it into a prompt (the thinking style is `style_patterns.py`,
  derived on read). Its rows stay, as this invariant requires.

This is a separate invariant from the others, not a restatement of
any of them: the memory layer (`memory.py`) is a derived, searchable
layer built on top of `DisambiguationStore` (invariant 9), not the
same store or the same rationale — it exists to give minimal_branch
cross-turn and cross-session recall, a concern neither invariant 6, 8,
nor 9 was written to cover.

Why: `learner_facts` is the literal record of how past uncertainty in
a specific student was actually resolved — deleting or editing one
would let the system quietly forget something it once confirmed,
directly undermining the reason this layer exists (skip re-asking
what's already known). `thinking_style_candidates` is a hypothesis
about a cross-session pattern in exactly the same evidentiary sense
`Hypothesis` is a claim about a learner (invariant 1) — a candidate
that later stops matching is evidence the earlier confirmations were
wrong or the pattern faded, which is itself worth keeping on record,
not silently removing.

### 11. The evidence store is append-only

`EvidenceStore` (`evidence_records`, migration 033) must never delete
rows. Concretely:

- No `delete` / `remove` methods on the class.
- No `DELETE` SQL anywhere in the `evidence` module or its migration.
- A recorded finding is never edited after insert — it is a historical
  fact about what a check saw at that moment. A finding that later
  turns out to have been misleading is corrected by *adding* a new
  record next to it, not by changing or removing the old one.
- Verified by the same AST-based check used for invariants 1, 4, 6, 7,
  8, 9, and 10.

Why: `evidence_records` exists so verification findings survive with
their context intact — and the load-bearing part of that context is
`source_type`. A `staged_verification` row was produced by a
deliberate scripted run and can only show that a *mechanism functions*;
an `organic_session` row came from real use. If a row could be edited
or deleted, the record of "what was actually tested, under what
conditions, and what it showed" — the whole reason to keep this log —
stops being trustworthy, and a staged mechanism test could quietly be
made to read like proof the system adapted to a student. The
`summary` column is where that distinction is kept legible at a glance
("mechanism verified …", never "adapted to the student" for a staged
row); that wording is the writer's responsibility, but the row it
lives on must be immutable for the phrasing to mean anything later.

### 12. Rooms are append-only (experimental, branch `experiment/rooms`)

The rooms tables (`rooms`, `room_members`, `room_messages`, `room_tasks`,
`room_task_events`, `room_option_sets`, `room_options`,
`room_option_picks`, `room_node_calls` -- migration
`rooms_001_rooms.sql`, code in `src/versa/rooms/`) must never delete or
update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `RoomStore`.
- No `DELETE` or `UPDATE` SQL anywhere in the `rooms` package or its
  migration.
- State that changes is derived from rows that were only ever added: a
  task is done if its latest `room_task_events` row says `completed`; an
  option set is open for a person until they pick from it or a newer set
  for the same audience arrives. Nothing is flagged in place.
- (2026-10-01) Tasks are tap-to-answer quizzes and races are one question
  for everyone, first right tap wins: a quiz's right answer is kept in its
  message's meta (never sent to a device), a tap is graded into the pick
  message's meta, a race is closed by the `progress` message that names its
  winner (or nobody), and the scoreboard and the topic's covered parts are
  derived from those messages -- still no row edited, no score stored.
- Every model call a room makes is recorded to `room_node_calls` with its
  full input (incl. the prompt) and output, or its error -- invariant 2's
  payload in the rooms' own table, because a room is not a `sessions` row
  (the same precedent as `feed_generations` / `topic_generations`).
- Rooms are walled off from the personal memory layer: nothing in
  `rooms/` reads or writes learner facts, claims, thinking styles or any
  core table (same wall as invariants 6/8).
- Verified by `tests/test_rooms_append_only.py`, the same AST-based check
  used for invariants 1, 4, 6-11.

Why: a room is a record of how a group learned together -- what Versa
chose to say, to whom, privately or not, and why. Editing or pruning it
would make that unreadable later, exactly as for every other store.

### 13. Exam preparation is append-only

The exam tables (`exams`, `exam_units`, `exam_generations`,
`exam_quizzes`, `exam_questions`, `exam_submissions`, `exam_answers`,
`exam_plans`, `exam_plan_items`, `exam_plan_item_events` -- migrations
`075_exams.sql` and `076_exam_plans.sql`, code in `src/versa/exams.py`) must never
delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `ExamStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `exams.py` or its migration.
- A quiz is one sitting: a retake is a new quiz, never an edit of an old
  one, and a quiz is handed in at most once (`exam_submissions.quiz_id`
  is UNIQUE). Scores are never stored -- they are derived from
  `exam_answers`.
- Re-planning writes a new study plan (the latest is the plan). Whether a
  plan item is done is never stored: quizzes/mocks are matched to
  handed-in sittings, and ticks are `exam_plan_item_events`, latest wins.
- Every model call (syllabus, questions, grading) is recorded to
  `exam_generations` with its full input (incl. the prompt) and output or
  error -- invariant 2's payload in exam prep's own table, since there is
  no `sessions` row (same precedent as `topic_generations`).
- Walled off from the personal learner model (decided 2026-09-26):
  nothing in `exams.py` reads or writes learner facts, claims or thinking
  styles. Results are episodic evidence only, until the claims layer has a
  guard against counting its own nudges as evidence. The one thing read
  from outside is the sign-up profile's stated board and level
  (profiles.py, invariant 18) when an exam is set up from a search -- what
  the student said, not something concluded about them.
- Verified by `tests/test_exams_append_only.py`, the same AST-based check
  used for invariants 1, 4, 6-12.

Why: a student's exam history -- what they were asked, what they
answered, what was marked right -- is the record a later weakness map or
readiness estimate would be derived from. If an attempt could be edited
or pruned, any number computed from it later stops meaning anything.

### 14. Directions ("where this could go") are append-only

`DirectionStore` (`direction_sets`, `direction_cards`, `direction_events`
-- migration `078_directions.sql`, code in `src/versa/directions.py`) must
never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `DirectionStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `directions.py` or its migration.
- A set is written once with its cards, including the position each card
  was SHOWN at (shuffled per set). What happened to it is one
  `direction_events` row -- picked (which card, how many ms after it was
  offered) or passed (the learner asked their own question) -- the first
  thing they did settles it (`set_id` is UNIQUE there).
- (From 2026-09-29, migration 086, directions.py lib-v2.) There is no fixed
  skeleton any more: each answer's cards are written for a POOL drawn at
  random from a library of 16 card types (two per family), plus a random
  two-step path card and one wild card (tagged afterwards to its nearest
  type by embedding); the learner sees a HAND of three dealt at random from
  the pool, one per family, and "other directions" deals the next hand
  (a `more` event). The draw, the path and the tagging never take anything
  about the learner; the whole pool is kept (`direction_pools`), and every
  pick is read against the hand it was taken from (choice.py), which is
  what keeps random hands comparable.
- (From 2026-09-30, migration 088.) A pass is a MISS: the question they
  asked instead is kept once in `direction_misses` (set_id UNIQUE), read
  against the library by embedding (never a model call, never anything
  about the learner), and the next answer's first hand in that chat is
  widened at random (`direction_sets.experiment = 'after_miss'`). What the
  widening reads is only that this chat's last set was passed -- something
  the learner did -- and which extra goes in is random.
- (Migration 089.) A miss the embedding can't place is read by one fast
  model call (`ReadMiss`, via `_call_node`), given only the question and the
  one before it, into `direction_miss_readings` (set_id UNIQUE, written
  once). A reading that is none of the library's types is a new move; groups
  of them are candidate card types for a PERSON to add to the library --
  nothing changes the cards on its own.
- The set is given no
  thinking style, claims, profile or cross-session history -- only the
  message, the answer, the learner's own depth/breadth sliders, and (from
  2026-09-29) the directions they took earlier in THIS chat, so each set
  builds on where they are instead of re-offering ground covered. Both
  personal inputs are things the learner did, not things the system
  concluded, and both apply to every slot alike. That is what makes a pick
  clean evidence rather than an echo of what the system already believed
  (the circularity risk in IDEAS.md). Do not personalise the skeleton or
  add any other generator input without a guard for that -- in particular,
  never what `pick_prediction.py` has learned (its guess and the learner's
  way in shape the answer, never the cards).
- (2026-09-30) A message with a picture (images.py, invariant 21) reaches
  the set as the words plus the picture's written reading -- still the
  message: what the learner gave, not anything concluded about them. A pass
  is kept (`direction_misses`) with the typed words alone.
- The model call goes through `SessionLoop._call_node` (invariant 2).
- Verified by `tests/test_directions_append_only.py`, the same AST-based
  check used for invariants 1, 4, 6-13.

Why: a learner's order of approach -- which direction they take, in what
order, and what they pass over -- is the thinking-style evidence this
feature exists to collect, and session-end consolidation reads it back.
If a pick could be edited or a set pruned, that order would stop being a
record of what the learner actually did.

### 15. The stage's quick checks are append-only

`StageCheckStore` (`stage_checks`, migration `080_stage_checks.sql`, code
in `src/versa/stage.py`) must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `StageCheckStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `stage.py` or its migration.
- One row per pick: the question, its choices, what was picked, the
  answer the director marked right (or NULL), and whether they matched.
  Written once, never edited.
- Walled off: nothing here is written into claims or thinking styles --
  episodic evidence only, the same wall as invariants 6/8/13/14.
- Verified by `tests/test_stage_checks_append_only.py`, the same AST-based
  check used for invariants 1, 4, 6-14.

Why: whether a student got the stage's check right is a record of what
actually landed. If a wrong answer could be quietly edited to right, or
pruned, any later reading of "what this student understood" stops being
trustworthy.

### 16. Sparks are an append-only ledger

`spark_events` (migration `081_sparks.sql`, code in `src/versa/sparks.py`)
must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `SparkStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `sparks.py` or its migration.
- A balance is never stored: it is `SUM(amount)` over the learner's events.
  Spends are negative; welcome grants, refills, refunds, rewards and
  purchases are positive.
- A refund is a new event pointing at the spend it reverses
  (`ref.spend_key`), never an edit of the spend.
- Every event has a UNIQUE `idempotency_key`, so a retried request, a
  double-tapped button or a replayed RevenueCat webhook lands at most once.
  A refill window that added nothing is still recorded (amount 0) so a
  window refills once.
- Charging is per-learner serialized (`pg_advisory_xact_lock`), so two
  requests can never spend the same Spark.
- Sparks are billing state, not the learner model: nothing in `sparks.py`
  reads or writes facts, claims or thinking styles, and exam results only
  enter as pass/fail for a reward (invariant 13 still holds).
- Verified by `tests/test_sparks_append_only.py`, the same AST-based check
  used for invariants 1, 4, 6-15.

Why: Sparks are money-adjacent. A student (or a judge, or a refund
dispute) must be able to see exactly why a balance is what it is -- what
was spent on what, what was earned back and when. An editable balance
can't answer that.

### 17. Billing records are append-only

`billing_events` and `exam_pass_grants` (migration `082_billing.sql`, code
in `src/versa/billing.py`) must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `BillingStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `billing.py` or its migration.
- Every purchase notice Versa receives -- a RevenueCat webhook or a
  purchase found by an app-triggered sync -- is kept verbatim, once
  (`event_key` is UNIQUE: RevenueCat's event id, or `sync:<transaction>`).
- One purchase is applied once, whichever route it arrives by: Spark packs
  are keyed by store transaction in the Spark ledger (invariant 16), Exam
  Passes by `exam_pass_grants.transaction_id` (UNIQUE).
- Whether an Exam Pass is running is derived from `starts_at`/`ends_at`,
  never flagged. Whether a learner has Plus is RevenueCat's answer, read
  (and cached briefly), never copied into a Versa column.
- Billing never blocks learning: if RevenueCat can't be reached, the last
  known plan (or Free) is used.
- Verified by `tests/test_billing.py`, the same AST-based check used for
  invariants 1, 4, 6-16.

Why: what a student paid for, and what Versa gave them for it, has to be
provable later -- for the student, for a refund, for a store review. An
edited or pruned billing record can't prove anything.

### 18. Accounts and the sign-up profile are append-only

`AccountStore` (`learner_identities`, `learner_sign_ins`, `invites`,
`invite_redemptions`, `invite_revocations`) and `ProfileStore`
(`learner_profiles`, `profile_extractions`) -- migration `083_accounts.sql`,
code in `src/versa/accounts.py` and `src/versa/profiles.py` -- must never
delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on either store.
- No `DELETE` or `UPDATE` SQL anywhere in `accounts.py`, `profiles.py` or
  their migration.
- An identity (a Firebase uid, or a tester's name) is written the first time
  it signs in and never edited; every sign-in adds a `learner_sign_ins` row.
- How often an invite was used is derived from `invite_redemptions`, never
  stored; withdrawing one adds an `invite_revocations` row.
- Editing the profile writes a new `learner_profiles` row -- the latest is
  the profile. Each one keeps the consent given with it (version, and a
  parent's or guardian's under 18). The model call that reads it is recorded
  to `profile_extractions` with its prompt and output or error (invariant 2's
  payload in accounts' own table: there is no session yet).
- The profile is what the learner SAID, not something inferred: it is given
  to the answer, the ambiguity check, the options, Learn-a-topic and exam
  syllabus search, but never written into facts, claims or thinking styles,
  and never given to "where this could go" (invariant 14).
- Every learner-owned id a route accepts must be ownership-checked by the
  guard (`accounts.OWNER_SQL`); `tests/test_accounts.py` fails if a route
  gains a path parameter that is neither there nor deliberately unowned.
- Verified by `tests/test_accounts.py`, the same AST-based check used for
  invariants 1, 4, 6-17.

Why: who could sign in as whom, who let them in, and what they agreed to
must be provable later -- for a parent asking what their child consented
to, for a leaked invite, for a dispute about an account. And the profile a
learner gave at 15 is a record of what Versa was told then, not something
to be overwritten when they're 16.

### 19. Slider moves are append-only

`KnobEventStore` (`knob_events`, migration `084_knob_events.sql`, code in
`src/versa/knob_events.py`) must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `KnobEventStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `knob_events.py` or its migration.
- One row per settled move that changed something (the app debounces a
  drag into one PATCH): the levels before and after, and how many turns the
  session had. `sessions` still holds only the current levels -- what the
  next answer is written at -- and this is the history of how they got
  there.
- Verified by `tests/test_knob_events.py`, the same AST-based check used for
  invariants 1, 4, 6-18.

Why: a slider move is the learner setting their own range, unprompted --
the Range lens of their thinking style (docs/THINKING_STYLE.md). With only
the last value per session kept, every earlier move was lost, and a range
can't be learned from a record that forgets where it has been.

### 20. Pick predictions are append-only

`PredictionStore` (`direction_predictions`, migration
`085_direction_predictions.sql`, code in `src/versa/pick_prediction.py`)
must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `PredictionStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `pick_prediction.py` or its
  migration.
- One guess per directions set, written BEFORE the set is sent, so it can
  never have seen the pick. It keeps the scores per slot and the exact
  contributions behind them (everyone else's picks, the learner's own, their
  order of approach or how they open a chat, and what was set aside and
  why). Whether it was right is never stored: it is derived from the set's
  `direction_events` row, and so is the record the learner is shown.
- The guess never changes the set: cards are generated and shuffled exactly
  as before (invariant 14). It is only revealed after the pick.
- Verified by `tests/test_pick_prediction.py`, the same AST-based check used
  for invariants 1, 4, 6-19.

Why: these guesses are the proof of the core claim -- if Versa is learning
how someone thinks, they get better over time. A guess that could be edited
after the pick, or a miss that could be pruned, would make that curve mean
nothing. And "what contributed to what must be seen": the breakdown is kept
with the guess, not recomputed later from data that has since grown.

### 21. Pictures are append-only

`ImageStore` (`images`, migration `090_images.sql`, code in
`src/versa/images.py`) must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `ImageStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `images.py` or its migration.
- One row per upload, written once AFTER the reading call: the bytes, their
  sniffed type and hash, the prompt the reader got, and its reading or error.
  A picture that couldn't be read is still a row (reading NULL).
- A picture is read ONCE, on upload, and only the reading goes further: a
  Sandbox/lesson turn gets it with its message (the server looks it up by
  `image_id`, only for the session learner's own picture); a room message
  and an exam answer carry it as text the app wrote in (rooms and exams stay
  walled off, invariants 12/13). Nothing downstream is given the pixels, so
  what any turn was told about a picture is always readable afterwards.
- The reading call is recorded on the row itself (prompt + output/error) --
  invariant 2's payload in images' own table, since there may be no session.
- Verified by `tests/test_images.py`, the same AST-based check used for
  invariants 1, 4, 6-20.

Why: a picture is evidence of what the learner was working on, and the
reading is what Versa actually understood from it. If either could be
edited or pruned, a later answer could no longer be traced to what it was
shown.

### 22. Revision notes are append-only, and made only when asked

`NoteStore` (`notes`, migration `091_notes.sql`, code in `src/versa/notes.py`)
must never delete or update rows. Concretely:

- No `delete` / `remove` / `update` / `set_` methods on `NoteStore`.
- No `DELETE` or `UPDATE` SQL anywhere in `notes.py` or its migration.
- One row per request that ran the model, written once AFTER the call: the
  chat it covers and how far (`through_turn`, `answers`), the prompt, the raw
  reply, and the parsed notes -- or the error. A failed attempt is still a
  row (content NULL). The call's prompt and output live on the row
  (invariant 2's payload in notes' own table; notes are not a turn).
- Notes are made ONLY by `POST /api/sessions/{id}/notes` -- the learner
  tapping "Generate notes". Nothing makes them on a schedule, at the end of a
  chat, or in the background. Asking again when nothing new was studied
  returns the latest notes and writes nothing (and charges nothing).
- Notes are not evidence: nothing in claims, thinking styles, observations
  or the direction cards reads the `notes` table. The profile and confirmed
  thinking styles shape how notes are WRITTEN, never what they say is true.
- The PDF is drawn from a row on request and never stored.
- Verified by `tests/test_notes.py`, the same AST-based check used for
  invariants 1, 4, 6-21.

Why: notes are what the learner takes away from a chat. If they could be
rewritten, what someone revised from could no longer be traced back to the
chat and the prompt that produced it; and notes made without being asked
would spend a learner's Sparks on something they never wanted.
