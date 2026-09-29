# Thinking style — the core claim, the evidence, and how it will be proved

This is the document the rest of Versa hangs off. Everything else — the
ambiguity options, the directions, the sliders, the three modes — exists to
feed or use what is written here. Agreed 2026-09-29.

---

## 1. The claim

A person often can't say what they want — the thought exists before the
words for it. So Versa doesn't ask; it **offers**. After every answer it
lays out where the idea could go, in a fresh form for any topic, and the
person recognises the one that matches what was already in their mind.
That recognition is evidence of how they think.

A single session is coloured by **mood** (tired, rushed, curious) and
**ability** (this topic is hard for them, today). What persists across
topics and weeks, once those are set aside, is their **way of thinking**.
If none of the offered paths matches, that is information too: keep
varying what is offered until something matches, then check whether the
match persists.

The end goal: a system whose answers get better as time passes, until it
offers what the person would have picked before they pick it.

### Definition (agreed)

> A **thinking style** is how a person, given free choice, moves through
> ideas — where they start, in what order, in which direction, and within
> what depth and breadth limits — stable across topics and over time,
> measured against their cohort's default, and **proven when it predicts
> their next unsteered choice**. Interests (what pulls them) are tracked
> alongside, but as a separate model.

- **Free choice** — only unsteered evidence counts toward proof. If Versa
  shaped the options from its own belief and the person then picked one,
  that pick partly reflects Versa.
- **Stable across topics and time** — once is a moment; across photosynthesis,
  recursion and economics, over weeks, is a style. A style may be
  conditional ("new topic → starts concrete; familiar → straight to why").
- **Against the cohort** — nearly every beginner wants an example first.
  That's being new, not a style. A style is how someone *differs* from the
  default for people like them (board, class, level — the sign-up profile
  is the cohort key, not evidence).
- **Predicts** — if knowing the style doesn't improve the guess of what
  they'll tap next, it isn't a finding.

### Two claims, kept separate

- **A — Versa can identify a thinking style.** Proven by prediction. This
  is the core claim now.
- **B — Adapting to it helps them.** Needs a style-on vs style-off
  comparison on an outcome fixed in advance. Later. (The popular
  "learning styles → matched teaching" idea has weak evidence — Pashler et
  al. 2008 — so B is not assumed.)

### What is not style

- **Ability** — what they can handle right now; changes as they learn.
- **Mood / state** — one session's condition.
- **Topic difficulty** — a preference that only shows where the topic is
  hard is conditional, or ability, not a general style.

---

## 2. The lenses — how every observation is split

| Lens | Question | Main sources |
|---|---|---|
| **Style** | How do they move through an idea? | direction order, topic-tree expand pattern, (legacy approach-axis picks) |
| **Range** | Within what depth / breadth / length limits? | slider moves, answer rewrites, tree depth reached |
| **Interest** | What pulls them? | searches, topic switches, "what comes next" picks |
| **Ability** | What can they handle in this topic now? | stage checks, help level, stuck_repeat, lesson checks, (exam scores) |
| **Mood / state** | What condition are they in this session? | pick speed, passes, message length, abandoning, time of day |
| **Said** | What did they tell us? | stated preferences, profile, their edits to Versa's reading |

Each observation also carries: learner, session, topic, time, and
**steered or clean**.

---

## 3. Evidence inventory (2026-09-29)

### A. Used by thinking-style consolidation today — all of it

| Source | Lens | Clean? |
|---|---|---|
| `learner_facts` (situation → resolution, reason) | Style (weak) | steered once a style is confirmed (options get the hint) |
| `direction_events` — pick **order** only, via `directions.render_path` | Style | clean |

### B. Recorded, clean or mostly clean, not used

| Source | What it gives | Lens |
|---|---|---|
| `direction_events.kind = passed` | typed past all six — a miss | Style; the trigger for experimenting |
| `direction_events.elapsed_ms` | how fast they recognised it | Style (strength), Mood |
| `direction_cards.position` (shuffled) | picked for the words vs the top slot | position-bias control |
| `direction_sets.depth_level / breadth_level` | window each set was pitched in | Range |
| `direction_sets.presentation` | fork vs cards | control |
| `interaction_options` with `kind='approach'`, `axis`, `side` | picks on 10 named axes — **legacy only**: Guess mode, which produced them, was removed 2026-09-24 | Style (history) |
| `stated_preferences.label` | concrete_before_abstract, wants_analogies, prefers_brevity… | Said |
| `answer_versions` | rewrites after a slider move, with levels | Range |
| `interactions.entry_state / help_level / elapsed_ms / prior_turn_outcome` | stuck_repeat, topic_switch; help needed | Ability, Mood, Interest |
| `turn_outcomes` | matched / contradicted_intent / moved_on | did the answer land |
| `stage_checks.correct` | did the quick check land | Ability |
| `topic_explorations.source_kind`, `query` | searches vs brings a PDF/link; what they search | Interest |
| `topic_signals` (expand, selection, lesson_open, chapter_drift, check_result) | what they expand and tick, in what order | Interest, Style ⭐ |
| `topic_nodes.depth` of what they expand | deep-first vs wide-first | Range ⭐ |
| `lesson_task_events` | which task kinds (learn/practice/apply/check) go quickly | Style, Ability |
| `student_reviews` on a style candidate | approve / edit / archive Versa's reading | Said (strong check) |
| `claims`, `claim_evidence.axis` | claims on the same 10 axes | Style |
| `turns.text` | their own wording (why / how / example questions) | Style (extractable) |

The **10 axes** already defined for `interaction_options`, `claim_evidence`
and stated preferences — concrete↔general, scope narrow↔broad,
rigor↔intuition, mechanism↔procedure, worked steps↔result,
analogy↔formal, single example↔pattern, forward derivation↔backward
verification, brevity↔depth, structured↔narrative — are the candidate
common scale every lens reports on.

### C. Recorded, but lossy or walled off

| Source | Problem |
|---|---|
| sliders on `sessions` | overwritten in place — **fixed 2026-09-29**: every settled move now also goes to `knob_events` (migration 084, invariant 19) |
| exam prep (`exam_quizzes`, `exam_submissions`, `exam_answers`, `exam_plan_item_events`) | rich (which unit first, retakes, quiz vs mock, time) but walled off by invariant 13 — **open decision** |
| `learner_profiles` | not style evidence; the cohort key |

### D. Not captured

Reading time before the next action, scrolling past the directions,
leaving after an answer, stage replays/skips. Would need app-side events.

### E. Excluded by decision

Study with others (`room_*`) — group behaviour, not one person's (invariant 12).

---

## 4. The architecture (proposed — not built)

- **Layer 0 — raw record.** Everything above. Exists; append-only.
- **Layer 1 — observation ledger.** Each raw event → one or more tagged
  observations (lens, axis/slot, value, topic, session, steered/clean).
  Derived and stamped with a derivation version, so it can be rebuilt when
  the splitting improves, never by editing history.
- **Layer 2 — session reading.** Estimate this session's mood and this
  topic's ability first, then weight the style observations by them.
- **Layer 3 — cross-session matching.** A "thought" is a structured pattern
  ("new topic → first pick concrete"), not free text. It persists when seen
  in N sessions across M topics, from clean evidence, above the cohort
  default. It retires when it stops matching.
- **Layer 4 — use.** Confirmed patterns shape the answer (today
  `FinalAnswer` never gets the style), default slider positions and the
  ambiguity options — but must not displace a real topic ambiguity. **The
  directions strip stays unpersonalised** as the permanent measuring
  instrument (invariant 14).
- **Layer 5 — proof.** Before each directions set is shown, record a
  prediction of which card they'll pick. Track per learner over sessions:
  - **pick-prediction hit rate** vs chance (1/6) and vs the cohort default
    — rising = Versa is learning what's in their mind (claim A);
  - **first-answer acceptance** — no slider change, rewrite, typed-past or
    contradicted_intent — style-on vs style-off (claim B, later).

  As answers improve, picks may fall; the strip stays available, and fewer
  picks with rising acceptance counts as success.

**Experimenting on a miss** must be randomised (or planned in advance)
and logged — never chosen from Versa's current belief about the person, or
the picks only confirm what Versa already thought.

---

## 5. What is proven so far

| # | Claim | Status |
|---|---|---|
| 1 | detects a consistent style | staged ✅ (FF, 2026-09-27) — but fragments over 5 candidates (`limit=1` in consolidation) |
| 2 | doesn't invent a style | ❌ control reached 3/5 toward a false one |
| 3 | tells different styles apart | ❌ untested |
| 4 | adaptation reaches the student | partial — options/ambiguity check (old detector); **the answer too since 2026-09-29** (way in from direction picks), untested on real people |
| 5 | adaptation doesn't hurt | ❌ displaced a real ambiguity ("logs") |
| 6 | adaptation helps | ❌ unmeasured |
| 7 | not circular | ❌ steered and clean evidence are not separated |
| 8 | changes over time | ❌ `ThinkingStyleStore.retire()` has no callers; text frozen |
| 9 | works on real people | ❌ zero `organic_session` evidence |

Runs: `docs/verification-runs/thinking_style_*.md`.

---

## 6. Build order

1. ✅ This document; the core claim at the top of CLAUDE.md and the README.
2. ✅ Slider moves logged as events (`knob_events`, 2026-09-29).
3. ✅ Pick prediction (`pick_prediction.py`, migration 085, invariant 20,
   2026-09-29): a deterministic guess before every set, with its exact
   breakdown kept; after a pick the learner sees "Versa guessed you'd pick
   this · 7 of your last 10" (or what it expected instead) and can tap for
   why. No narrowing: the cards are untouched. v1 sets aside quick taps and
   first-card taps; ability and mood discounts are layer 2.
4. ✅ Better answers (2026-09-29): once a learner's **way in** is clear
   from their own picks — the first card they take after a question of
   their own, in ≥4 such fresh starts, one way in at ≥35% — FinalAnswer is
   told to start that way (and with the step they usually take next, when
   that is clear too) — **but only on a follow-up**: the first answer to
   anything new is always normal; the shaping applies when the question is
   the same as, or directly related to, one from the last few turns of this
   chat (same similarity and threshold as entry_state's `continuing`;
   resolving options for a question counts as its first answer). Your
   words: "let the first always be normal ... it must feel like mind
   reading only after he asks initially". Arithmetic, no extra model call. The learner sees
   "Shaped to how you explore: example → where it's used" above the answer,
   tap for why; the directive is in node_calls. Not on direction picks, fork
   continuations or lessons. `VERSA_ADAPT_ANSWERS=off` is the style-off
   switch. Known tension: an answer that already opens with an example may
   make the "example" card less wanted, so guess accuracy is read alongside
   it, not alone.
5. ✅ Better cards (2026-09-29): each set is also given the directions the
   learner took earlier in THIS chat, so it builds on where they are and
   never re-offers ground covered. Same for every slot; skeleton and shuffle
   unchanged; no learned belief (guess, way in, style) ever reaches the
   generator — invariant 14 reworded to allow exactly this. Richer cards
   (their interests) wait for the Interest lens and a guard.
6. ✅ Layer 1, the observation ledger (`observations.py`, 2026-09-29):
   direction picks/passes (style, mood), slider moves (range), stage
   checks, stuck repeats and help level (ability), topic switches and
   returns (interest), stated preferences (said) — derived on every read,
   nothing stored, stamped `obs-v1`. First use, the start of layer 2: a
   pick right after a question they were stuck on or a quick check they got
   wrong counts ×0.5, picks from a rushed session (≥3 picks, ≥half quick
   taps) ×0.6 — in the guess and in the answer's way in, each named in the
   "tap for why" breakdown. `versa observations --learner X` prints the
   ledger by lens, what counts for less, the guess record and the way in.
   Not yet read: topic-tree signals, lesson tasks, exam prep (invariant 13).
7. ✅ **A bigger space** (2026-09-29, migration 086): a library of 16 card
   types in four families (make it real / go deeper / make it simpler /
   go wider) with coordinates on four axes; each answer writes a pool (two
   per family + a random two-step path + one wild card, tagged to its
   nearest type by embedding); the learner sees a hand of 3, one per
   family, and "↻ other directions" deals the next (a `more` event: nothing
   matched). Every pick is read against the hand it came from
   (`choice.py`, Plackett–Luce) — the guess is always one of the cards on
   offer, and layer 3 rates are "taken when offered" against chance.
   Layer 3 gained family-level patterns (a family is in 3 of 4 hands, so it
   firms up fast) and `lean` patterns (which way picks lean on each axis).
   **The proof is now a real test**: at each pick the guess comes from
   earlier picks only, and the hits must beat other learners at p < 0.05,
   Bonferroni-corrected for every candidate tried. Measured on simulated
   learners: before the fix 10–22% of random choosers were told they had a
   style; after, 0–4%. A broad style (goes "make it real" 80%, or takes the
   most concrete card 70%) is confirmed in ~92–94% of cases by 10 chats and
   ~100% by 20; a preference for one single card type out of 16 takes far
   longer (it is in few hands) — that is the cost of the bigger space.
8. ✅ **Everything wired to one thinking style** (2026-09-29):
   - *In:* direction picks, passes and "other directions" (Sandbox and
     Learn-a-topic lesson chats); slider moves; **the Learn-a-topic tree**
     — how deep they expand and how much of a course they keep — as range
     patterns (`explore_depth`, `explore_breadth`); stuck / wrong-check /
     rushed as discounts. Not in, by design: stated preferences (said, not
     shown), ambiguity-option clicks (about meaning), Study with others.
     Not in, pending your call: exam prep (invariant 13).
   - *Out:* the confirmed layer-3 patterns are now what the ambiguity check,
     the options and Learn-a-topic (course building, lesson tutoring) are
     given — never the direction cards (invariant 14). The old free-text
     detector's confirmed styles are used only while a learner has no
     confirmed layer-3 pattern (a transition). Answer shaping falls back to
     the family level ("starts by making it real") when no single card
     type is clear — with random hands of 16 types it otherwise would
     rarely fire.
9. ✅ **No self-confirming shaping** (2026-09-29): a pick made under an
   answer that was shaped to the learner's way in is marked `steered` (the
   answer's FinalAnswer call carried an approach_directive) and left out of
   the style patterns and the answer's way in -- it would only confirm the
   shaping. It still helps the guess, which only predicts. Also fixed: the
   time from cards shown to tap was the app server's clock minus the
   database's, so a clock skew could turn a read-and-chosen pick into a
   "quick tap"; both ends are now the database's clock.
10. ✅ **The second layer** (2026-09-30; your words: "we still haven't
    found anything that is unique, it's all the top layer stuff").
    - *One fact, not four:* patterns pointing the same way in the card space
      merge -- the strongest is the fact, the rest are facets ("also seen
      as"). On sooraj's (simulated) data, 7 patterns became 1 fact. Only
      facts reach the prompts.
    - *Conditional facts:* the same way out split by situation -- a topic
      new to them vs one they've met before, stuck vs going fine, opening a
      chat vs further in. Confirmed only when each side passes every gate on
      its own (Bonferroni over all six sides) AND the family taken differs
      by at least 25 points between them. Simulated: stuck/fine and
      opening/later found 65-100% by 15-30 chats; new/familiar needs many
      topics (87% at ~50 chats over 25 topics); a learner who is the same
      everywhere, and a random one, never got one (0 of 60).
    - *The shape of a chat* (`shape`): how picks move as a chat goes on,
      on the depth and concrete axes (later picks minus the opening two,
      each read against its own hand), e.g. "their picks get deeper as a
      chat goes on (they start simpler)". Proven by guessing later chats
      from earlier ones: the opening as the card furthest one way, later
      picks as the card furthest the other. Simulated: found by 15 chats.
    - *What they pass over* (`passes_over`): a card or family taken at
      most half as often as chance, where others take it at least twice as
      often, and rarer than luck on later offers. Read with their clear
      favourite set aside (its whole family, at card level) -- otherwise
      someone who always goes to "make it real" would "pass over"
      everything else, which is their way in again. Simulated: a learner
      who skips one card out of 16 is found 60% of the time by 30 chats,
      every time by 50 (a single card is rarely on offer).
    - *Speed* (`speed`): which way out they choose fastest or slowest
      against their other picks, on a log scale; quick taps and rushed
      chats left out. Speed isn't a choice, so it is proven by a
      significance test (Bonferroni over the four families) and holding in
      both halves rather than by predicting a pick. Simulated (3x faster
      on "go deeper"): found in 93% by 15 chats.
    - Controls: 100 random learners got none of these three (0 of 100);
      an always-"make it real" learner got no extra shape or pass facts
      (0 of 30). They're compared with other learners' own chats, orders
      and timings (`StyleReader._cohort_style_picks`).
11. ✅ Layers 2–3 (2026-09-29).
   - **Layer 2** (`observations.read_sessions`): one reading per session —
     picks, passes, quick-tap share, rushed, stuck turns, checks right/
     wrong, help levels, slider moves. The discounts come from it.
   - **Layer 3** (`style_patterns.py`, `GET /api/learners/{id}/style-patterns`,
     "How you explore" on the Thinking-style page, `versa observations`):
     the thinking style as defined above, one gate per clause —
     `way_in` / `then` / `range` patterns; confirmed only with ≥4 picks in
     the situation, ≥35% going there, ≥3 sessions, ≥3 different topics
     (told apart by first-question similarity, no drift), ≥1.5× the
     cohort, clear in both the earlier and later half, and **right out of
     sample** (judged only on picks after it showed itself, ≥3 trials,
     beating the cohort). Otherwise `emerging` (clear, not through every
     gate) or `fading` (clear before, not lately). Range: set by them in
     ≥3 sessions, ≥15 from the cohort, within 30, earlier/later within 20.
     `tests/test_style_patterns.py` has one test per clause (one topic
     only, what everyone does, a random learner, quick/rushed taps, a
     changed way in, a range set once or scattered). Every gate is returned
     with its numbers. Derived on read, stamped `style-v1`.
   - **Live, 2026-09-29** (signed in as sooraj, real Gemini, simulated
     persona, `adaptation_check_20260929.md`): guesses went from 5/9 right
     in the first third to 8/9 in the last; no card set re-offered a card
     already taken; one pattern confirmed through every gate ("after
     'where it is used', goes to 'work through one concrete example'",
     6/6 right out of sample, 4 topics). It found two bugs, both fixed:
     pgvector `halfvec` rows need `.to_list()`, and raw cosine can't tell
     topics apart (see IDEAS known issues) -- follow-ups and topics now
     compare centred embeddings. Note the persona was written "example
     first", yet it went "use, then example" (its openers already asked for
     an example): the patterns follow what it did, not what it was told.
   - **Live follow-up run, 2026-09-29** (`adaptation_check_20260929_followups.md`,
     5 chats, each with the student's own follow-up and a switch to a new
     subject mid-chat): **every first answer to something new was normal —
     10 of 10** (5 chat openers, 5 mid-chat switches). Own follow-ups:
     **3 of 6 shaped**, the other 3 answered normally — the 0.40 follow-up
     bar errs toward "new" by design, so it misses about half of real
     follow-ups; lower it only with organic data showing different subjects
     stay below it. After the run both way-in patterns are confirmed
     through every gate ("goes first to 'where it is used'": 8 topics, 13
     sessions, 16 of 21 later picks right out of sample; "then 'work
     through one concrete example'": 6 topics, 8 of 11). Guesses over the
     whole account: 32 of 58. Staged: a model playing a persona.
   - The old free-text detector (`thinking_style_candidates`) was retired
     on 2026-09-30 (IDEAS.md decisions log): nothing writes it, nothing
     reads it into a prompt.
8. ✅ **Experimenting on a miss** (2026-09-30, migration 088). A miss is a
   hand passed by asking their own question -- the one time Versa sees
   what was in their mind when no card matched.
   - *Read, not guessed:* the question is kept (`direction_misses`, one row
     per passed set, append-only) with the card type it is nearest to by
     embedding against the library descriptions -- no model call, nothing
     about the learner -- and whether it stayed on the same subject (a new
     subject is an interest, not a way out; "can't tell yet" is kept as
     unknown). Only a clear match counts: similarity >= 0.84 and 0.02 ahead
     of the next type. Calibrated on real Gemini embeddings (16 typical
     follow-ups, 3 non-ones): 14 of 16 kept, all read right; none of the 3
     kept. Both top similarities are on every row, to recalibrate later.
   - *Randomised experiment:* the next answer's first hand in that chat is
     widened -- an extra (path or wild card, which one at random) swapped in
     for certain, logged as `direction_sets.experiment = 'after_miss'`.
     Never chosen from what Versa believes (invariant 14); "other
     directions" keeps dealing new hands as before.
   - *Does it persist* (`miss_follow_through`): for each read miss, the
     first later hand that offered that way -- taken (a match found)? --
     and taken again in a later chat (it held)? Shown on the Thinking-style
     page, in the API (`misses`) and in `versa observations`.
   - *A new fact, `asks_for`:* "When the cards miss, asks for why it works
     (7 of 10 times)" -- their misses keep asking for the same way, more
     than other learners' misses do (1.5x), over 3 chats and 3 topics, in
     both halves, and it predicts their later misses (Bonferroni over all
     20 card and family targets). Simulated: a learner whose misses ask for
     one way 70% of the time is confirmed by 12 chats; scattered misses
     were confirmed at most 2 of 40 times; what everyone asks for is never a
     fact about them.
   - *Misses the library can't place* (migration 089, 2026-09-30): when the
     embedding can't put a missed question on a type, one fast model call
     (`ReadMiss`, recorded in node_calls; given the question and the one
     before it, nothing about the learner) reads the kind of move it makes
     -- a topic-free phrase ("where the rule stops working") -- and whether
     that is one of the 16 after all. Checked live: the first prompt forced
     most new moves onto near types ("where it stops being true" -> "go
     further", "who decides" -> "the story"); the strict prompt (read-miss-v2)
     read 7 of 9 off-library questions as new moves, kept "why does it work"
     as `why`, and flagged the off-subject one.
   - *Discovering styles outside the library:* new moves are grouped across
     learners by their phrase's embedding (`discover_moves`, cosine >= 0.80:
     live, same-group phrases >= 0.808, different groups <= 0.795). A group
     asked for >= 5 times by >= 3 learners is a **candidate card type**,
     listed by `versa discovered-moves` for a person to add to the next
     library -- never added on its own (the library is the instrument). The
     same command lists cards whose wording missed (someone typed the very
     move a card on screen offered).
   - *A new fact, `asks_beyond`:* "When the cards miss, keeps asking for
     something they don't offer: 'where the rule stops working'" -- the same
     checks as `asks_for`, over every group of new moves.
9. Decide invariant 13 -- exam prep as evidence. Direction agreed
   2026-09-30: exam prep is the PRESSURE condition. Only choices the student
   makes (which unit first, order of revision, what they do after a wrong
   answer), never scores; every observation tagged with its pressure
   (days to the exam, timed or not, after a miss, late in a long session);
   facts are "under pressure" conditionals that never change the relaxed
   style; per-unit scores used only as a control, to tell pressure from
   struggling. Waiting on your go to change invariant 13.
10. The interest layer -- parked (IDEAS.md), in step with the thinking
    style and for teaching, not drifting.
