# Versa — ideas & deferred work

The parking lot for things we want to do **later**. When you have an idea, or
we decide to defer something, it goes here instead of getting lost in chat.
Newest items go at the top of their section. Each item says who raised it,
when, and what it would take, so it can be picked up cold.

Status: `idea` (not decided) · `parked` (decided, not now) · `ready` (clear
enough to build) · `done` (moved to the log at the bottom).

---

## 1. Ideas

### Convert each query to options ("Guess Mode") — `removed` 2026-09-24 (see decisions log; text below is history)
*Raised by you, 2026-09-23.* Your words: "what we have to do now is convert
each query from the student to options and go down in guessing only if
ambiguous, we can ask the student to play along saying that we shall guess
what you want to learn how you want to learn stuff, and create the output
after that, hence providing more personalization." Akinator was the named
comparison: a structured narrowing procedure that feels intelligent without
an LLM, except here the space of readings can't be predefined — it has to
be generated fresh, per query, per student — and the goal isn't landing on
one pre-existing answer, it's narrowing toward what to *generate*.

**Reading (confirmed by you):** every message already goes through
`AssessAndBranch` (is the TOPIC ambiguous?) — that stays exactly as it was.
What was missing: once the topic is clear, nothing ever asked whether HOW to
teach it was still open, even though `ApproachAxis`'s full ten-axis
vocabulary already existed for exactly this. A new opt-in knob
(`sessions.guess_mode`) adds that second narrowing round, framed openly as
an invited game (the empty-state copy changes to "Let's see if I can guess,
{name}." when it's on) rather than a hidden mechanism.

- **Deliberately deferred, your own words:** "at the end we will get rich
  data of the selected options which can be used further that is another
  story we can decide it later." This build does NOT feed picks back into
  claims/stated-preferences/thinking-style — it only shapes the CURRENT
  turn's answer (`branch_context`, same as an ordinary branch click). Step 3
  of the "richer options" entry above (the claims feedback loop, still
  blocked on its own circularity guard) is the natural place this rich data
  would eventually go.
- **Made continuous, 2026-09-23, your words:** "I want it to be more like
  akinator continuous gather what they want and give question." The first
  build only ever asked ONE round (subject, or approach, whichever fired)
  then committed to an answer — real Akinator asks a sequence, each one
  informed by everything answered so far, and only commits once it's
  actually confident. A click no longer finalizes unconditionally: it asks
  the same "still open?" question again against the reading just picked,
  and keeps going until nothing more is open. On the tension this raises —
  a real tutoring student doesn't want 15 rounds of questions before a
  homework answer — **your call:** that's what the knob is for. Guess Mode
  is opt-in; if someone doesn't want the game, they turn it off and get
  ordinary Sandbox behavior. The system doesn't need to guess intent or
  cap itself defensively for people who never turned it on — the round cap
  that exists (`_MAX_GUESS_ROUNDS`) is a technical ceiling against a
  pathological LLM loop, not a UX brake, and should essentially never bind
  in practice.
- **See the decisions log** for the mechanism (`disambiguate.GuessApproach`,
  a new `DisambiguationTurnKind.APPROACH_GUESS`), the design fix that
  shipped alongside it (equal-width stage/content panels, hideable nav rail
  and session-knobs rail), and two real bugs a live run caught (a DB CHECK
  constraint that would have silently rejected every approach-guess turn;
  a resumed chat that would have shown the offer as if nothing had been
  recorded).

---

### Ask for confirmation directly — `done` (built 2026-09-22, see decisions log)
*Raised by you, 2026-09-22.* Your words: "we must focus on making this as
continuous as possible, depending on the context, the reason detected, the
confirmation must be asked directly, making feedback as strong as possible."

**Reading (confirmed by you):** a REASON-based memory match (the "store the
reason" idea below) used to go through `ConfirmFactMatch` — an LLM silently
judging whether to trust it and skip branching, the student never seeing a
reason was even detected. This asks the student directly instead: "it looks
like this is because {reason} — is that right?", reusing the exact
click-option UI Versa already has, so their yes/no becomes the confirmation
rather than an LLM's proxy guess.

- **Why it's the right next move, not just a nice-to-have:** it directly
  answers the "how good is the reasoning" concern from when reason-retrieval
  was built (a direct yes/no is categorically stronger evidence than another
  LLM's opinion), and it's the actual non-circular confirmation mechanism
  step 3 of the "richer options" entry above was missing before it could
  safely exist — a belief a student explicitly confirmed is not the same as
  a click on an option that belief happened to shape.
- **Scope, deliberately:** only the LIVE, in-turn reason-memory match — not
  claims or thinking-style, which only evaluate in the background at
  session-end and would need a completely different UX moment (no turn to
  inject a question into). Extending "ask directly, don't infer" to those is
  a natural follow-on, not part of this build.
- **See the decisions log entry for the full mechanism** (reuses
  `disambiguation_turns`/`branches`/`options` with two fixed Yes/No
  branches instead of LLM-generated readings — no new tables, no extra LLM
  cost) and the real bug it surfaced along the way (a resumed chat would
  have shown different confirmation text than the live one did, until the
  branch's stored text and the live message were unified to the same
  source).

---

### Richer, context-aware options — and closing the options/claims/reason loop — `idea` (step 1 done, 2026-09-22)
*Raised by you, 2026-09-22.* Your words: "another idea to build better options,
option that feel more related to current context, also use options to
improve the claims and if the 'reason' of the reason idea is correct
inferring, overall making it feel more continuous and related."

**Reading (confirmed by you):** three linked pieces, agreed to build in this
order:

1. **Options anchored to what's known about this learner.** Today
   `DisambiguationOptions.run()` only ever gets `branches, message,
   recent_history, reference_binding_hint` — the least personalized node in
   the turn. `FinalAnswer`, by contrast, also gets `learner_history_block`,
   `claim_constraints_block`, `structural_requirement`, and a thinking-style
   hint. Thread that same context into option generation so a reading can be
   phrased already anchored to this student, not generic regardless of who's
   asking.
2. **Options as a richer claims signal.** `interaction_options` already tags
   `kind`/`axis`/`side`, and `select_extraction_candidates` (claims.py)
   already ranks selected episodes by prediction error / axis coverage. Push
   this further: generate option sets partly *to test* a live-but-unconfirmed
   claim or axis, not just read the pick back afterward.
3. **Close the loop with the reason-based memory built 2026-09-22** (see
   decisions log): reason-based memory informs option generation → the pick
   becomes claim evidence → claim/reason confidence grows → shapes the next
   option set.

- **Why it helps:** options that feel less generic; a faster, more direct
  route to claim evidence than waiting for a surprising episode; genuine
  continuity across turns and sessions, not a fresh start every time.
- **The real risk, named and agreed:** if an unconfirmed claim or reason
  shapes *which options get offered*, and the click is then read back as
  evidence *for* that same claim/reason, that's circular — the system nudged
  the choice, then counted the choice as proof. Same shape as
  `reference_bindings.py`'s own named worst failure ("a stale binding
  confidently applied produces a fluent answer about the wrong thing") and
  `claims.py`'s confabulation risk, but sharper here because options are
  Versa's actual choice architecture, not background prompt context.
- **Mitigation direction discussed, not built:** only let *confirmed/
  promoted* claims and *corroborated* reasons (never raw candidates) shape
  option phrasing; log which belief shaped a given option set (`node_calls`
  already would, since it's a real node call) so a later audit can tell
  "this option set was shaped by claim X" apart from "this click is
  independent evidence for claim X."
- **Agreed build order:** (1) richer, context-aware options — **built
  2026-09-22** (see decisions log); (2) reason-based retrieval — **built
  2026-09-22** (dual situation/resolution + reason search, `matched_via`
  visibility, still unmeasured against real usage — see decisions log); (3)
  the claims feedback loop last, now that (1) and (2) are both real, but only
  once the circularity guard above is actually designed, not just named.
- **What it would touch (step 3, not yet started):**
  `claims.select_extraction_candidates`, once option generation itself starts
  reading claim state to decide what to test, plus the circularity guard
  itself (which belief shaped which option set, recorded somewhere an audit
  can read it back).
- **Status:** steps 1 and 2 both done (split across two instances working in
  parallel, per your 2026-09-22 split); step 3 not started — it's the one
  piece with a real, named risk and no built mitigation yet, so it stays here
  rather than being picked up casually.

---

### Show options first, remember second ("oh wait…")  — `done` 2026-09-25 (see decisions log)
*Raised by you, 2026-09-22.* Your words: "embedding can be done later after
ambiguous and options generation are done. It can be made funny and
interesting — oh wait, and the options disappear and it can come back to the
direct answer."

**My reading (please confirm):** today every turn starts with an embedding +
memory search (0.55–1.1 s) *before* the ambiguity check. Instead: run the
ambiguity check and show the clickable options right away, and do the memory
lookup afterwards, in the background. If memory then finds that a past fact
already answers the question, the options **retract** with a playful "oh wait,
I remember what you meant" moment and the direct answer streams in.

- **Why it helps:** the options turn gets ~0.5–1 s faster, and the retract is
  a personality moment instead of a silent shortcut.
- **What it touches:** the loop's memory pre-check (`loop.py`), the
  `branching_skipped_by_memory` diagnostics (the ambiguity check would now have
  run, so its `disambiguation_turns` row must still be written — invariant 9),
  and options already shown must be marked `superseded`, never deleted
  (invariants 8/9). Needs a client event for "retract options".
- **Open questions:** does "come back to the direct answer" also mean
  drafting the answer *while* the options are showing (so it is ready if the
  student ignores them)? What should the playful copy sound like? How often is
  the retract worth the flicker (memory-confirmed hits are rare today)?

---

## 2. Product surfaces — parked for after the Sandbox chat

The first build is **Sandbox chat only** (decided 2026-09-21). Everything
below comes from your wireframes; the mock-up content generated by Claude
Design is placeholder, not a spec.

| Surface | What it would need (backend) |
|---|---|
| **Learn a topic** (upload syllabus, search & select, completion %) | `done` 2026-09-25 — see the decisions log (backend `topics.py`/`resources.py`, app `app/lib/topic/`). A plain course outline with progress derived from task events; no learner model on top (invariant 4). |
| **Exam preparation** (plan, quizzes, mock tests, weakness map, readiness) | Quiz/flashcard generation, spaced repetition, grading; a notion of exam date and syllabus. Nothing exists yet. |
| **Study with others** | Multi-user rooms, matching, moderation, voice/text. Backend has no users/auth today, only a learner label. |
| **Animations beside the chat** | `in progress` 2026-09-24: the character engine is built (see decisions log). Still missing: the LLM step that writes a script for whatever the chat is about. |
| **Home feed / recommendations** | **Built 2026-09-24** (see decisions log): one LLM call over the learner's own chats and facts, cached 6 h. No topic model needed. |
| **Thinking-style page** | `done` 2026-09-24 (see decisions log). Was: can be built on `claims.py` / `thinking_style_candidates`, but only shows what the system can actually support. Under the current confidence clamp no claim reaches "promoted". Mock-up findings (retention, time-of-day, …) are **not** measured by anything. |
| **Session knobs** (answer length, depth) | `done` (2026-09-24), reworked the same day into live sliders — see the decisions log. Tone removed. |
| **"Save this Sandbox chat as a topic"** | A session's mode is fixed at creation and Sandbox turns don't feed memory; converting means copying turns into a new tracked session. |
| **Settings → wipe "model of you"** | Conflicts with the append-only invariants. Needs archive/tombstone semantics or an explicit privacy exception. "Freeze learning" is easy (a flag on the write paths). |

---

## 3. Performance follow-ups (deferred — "this speed is enough")

Where we landed (2026-09-21, real Gemini): first words of an answer ~2.3–4.2 s,
options shown ~5–8 s, down from 13–20 s. Tools: `scripts/measure_turn.py`,
`scripts/eval_assess_thinking.py`.

- **Speculative parallel turn** — `parked`. Draft the answer while the ambiguity
  check runs; discard the draft if the message is ambiguous. Would bring
  direct-answer first words toward ~2–3 s. Cancelled drafts must still leave an
  audit row (invariant 2).
- **Lighter/faster model for the ambiguity check and option generation** —
  `parked`. Test what model ids are available; option generation alone is
  2.5–3.9 s.
- **Fuse ambiguity check + options into one call** — `parked`. ~3 s instead of
  ~5 s, but merges two judgments the design deliberately kept apart; re-run the
  ambiguity eval first.
- **Embedding timeout + retry** — `ready`. One embedding call stalled 15 s in a
  real run; the embedding client has no retry and a 30 s timeout.
- **Per-node thinking settings** — `idea`. Thinking is set per tier today
  (fast=256-token budget, best=minimal). Option generation likely doesn't need
  the budget the ambiguity check does.
- **Evaluate answer quality under `minimal` thinking** — `ready`. Only the
  ambiguity check has been evaluated; the answer tier is unmeasured.
- **Options within 2 s** — `idea`. Not reachable with sequential calls
  (~5–6 s today); would need the fused call plus a faster model, or a
  streamed/progressive options UI.

---

## 4. Server & app follow-ups

Built 2026-09-22: `versa serve` (REST + one WebSocket per chat), the Flutter app
with a working Sandbox chat, `scripts/start.ps1` (one-command launcher), and
per-session pending-options state in the loop. See the decisions log.

- **Chat history & resume** — `done` (2026-09-22). See the decisions log.
  The global History nav page is still a placeholder — it could now show the
  same per-mode list read across all modes; not built yet since Sandbox is
  the only live mode.
- **Authentication** — `parked`. Sign-in is a name only; the server has no auth
  and binds 127.0.0.1. Must exist before any deployment (the Dockerfile
  deliberately has no entrypoint).
- **Repeatable browser end-to-end check** — `ready`. The scripts that drove the
  real app (sign in → streamed answer → options → click → new chat; and
  separately the sidebar → new chat → resume flow) live in a scratch folder.
  Worth committing (puppeteer-core + Edge) so what was proved can be re-proved
  after every change — see the note below on their one real gotcha first.
- **Flutter web accessibility-tree lag (e2e-testing gotcha)** — `note`
  (found 2026-09-22). After a widget subtree swap that replaces a whole
  controller (e.g. switching to a resumed chat), the CANVAS repaints
  correctly almost immediately, but the `flt-semantics` accessibility DOM
  overlay a puppeteer script reads can lag several seconds, or not catch up
  in headless mode at all, even though the pixels are already right
  (confirmed by screenshot). A script that re-queries or waits on the
  semantics tree before/after clicking can flake; one that captures an
  element's coordinates once and clicks immediately is reliable. Not an app
  bug — screen readers hitting the same lag would be a real accessibility
  issue worth a proper look separately from this workaround.
- **Math in answers** — `idea`. Models write LaTeX (`$x^5$`); the app shows it raw.
- **Offline web build** — `idea`. The web build fetches CanvasKit and the Roboto
  font from Google's CDN on load. `flutter build web --no-web-resources-cdn`
  bundles CanvasKit.
- **Screen-reader labels** — `idea`. Message text is exposed as text fields
  (selectable text) and the option chips as buttons; worth a proper `Semantics` pass.
- **Android / iOS builds** — `parked`. Web and Windows desktop build and run;
  Android needs Android Studio + SDK, iOS needs a Mac. The layout already
  collapses to a phone (bottom navigation) and was checked in a real browser at 400 px.
- **Cross-session adaptation & reference checks** — `ready` (asked 2026-09-22).
  Verified so far: the memory lookup finds an earlier chat's fact (live, in the
  app) and references inside one chat resolve. NOT verified with a real model:
  a stated preference ("keep it short", "use analogies") carrying into a new
  chat; the learner-history block changing an answer; references to an earlier
  chat ("that example from last time", "my project"); reworded repeats
  skipping the options. Plan: a scripted two-chat scenario set run through
  `handle_turn` (like `scripts/measure_turn.py`), reading `turn_diagnostics`
  (`memory_match_*`, `history_block_used`) and comparing answers with/without
  the earlier chat. Everything else here (thinking style, claims) needs many
  sessions, and no claim can be promoted under the current clamp.
- **Memory and repeat questions** — `idea`. In a fresh chat the same ambiguous
  question found the earlier fact, but `ConfirmFactMatch` judged it not enough
  to skip the options, so they were offered again (working as designed). Product
  question: what should a recorded resolution capture so a repeat is recognised?
- **Streaming footgun** — `note`. A wrapper around the LLM client without a
  `stream` method silently turns streaming off.

---

## 5. Known issues & cleanup

- `versa --help` prints a garbled dict for `aggregate-patterns` (a `%` in its
  help string). One-character fix.
- `archive/instrument_layer/README.md` says removed work is in a git stash;
  `git stash list` is empty.
- `llm.py`'s stub still carries canned responses for the removed planner.
- `docs/verification-runs/` describes a grounding feature that isn't in the code.
- Ruff: 37 pre-existing findings (mostly import order) plus 2 in
  `scripts/bench_retrieval.py`.
- Options still take ~6-10 s to appear in the app (embedding, ambiguity check
  and option generation run one after another); "options first, memory second"
  (section 1) is the lever.
- The claim layer can't promote a claim under the current confidence clamp
  (threshold 0.8, clamp max 0.7) — by design until calibration exists.
- **Failed background node calls leave zero durable trace** (found
  2026-09-22, live, via a real free-tier quota exhaustion mid-session).
  `SessionLoop._call_node` only writes to `node_calls` AFTER `node.run()`
  returns; a raised exception (quota, network, anything transient) means
  the call never happened as far as any table is concerned — no
  `node_calls` row, no `turn_diagnostics` flag, only a process log line. In
  a real day of use this could silently stop `WriteLearnerFact` /
  `ClassifyStatedPreference` / etc. from ever running, with nothing
  queryable to show it happened. Not fixed — would need either a
  retry/dead-letter mechanism or a "this step failed" marker written
  BEFORE the call, which is more than a same-day fix.
  **Sharpened 2026-09-22 (learner "AS", see
  `docs/verification-runs/thinking_style_AS_20260922.md`):** the free
  tier's cap is **per-model, per-day, 20 requests** — both `gemini-3.6-flash`
  and `gemini-3.5-flash` hit it independently the same day, and a single
  rich multi-turn session alone can exceed it. A 5-session live check of
  thinking-style promotion (needs 5 independent session confirmations)
  could not be run at all as a result — not a mechanism failure, the
  mechanism was never reached. A real, hard blocker on further live
  multi-session evaluation until either the daily reset or a higher-quota
  key.

---

## 6. Decisions log

- **2026-09-25** — Built "show options first, remember second (oh
  wait…)", with the stage slime as its comic beat. The memory pre-check
  (`loop.py` `_memory_precheck`) now runs CONCURRENTLY with
  AssessAndBranch instead of before it. Resolution, by when memory lands:
  (1) an unambiguous message still waits for memory, so behaviour is
  unchanged; (2) ambiguous, and memory is back before the options exist:
  memory takes over as before (a confirmed fact is answered from, a
  reason is asked about). The assessment that ran alongside is still
  recorded, with its branches superseded (`_record_preempted_assessment`,
  invariant 9). (3) Memory lands after the options were generated but
  before they were shown: same as (2), and the unseen options are
  superseded. (4) The options were already on screen: a confirmed fact
  RETRACTS them (`_retract_options_for_memory`: superseded, never
  deleted, invariants 8/9; warning `options_retracted_by_memory`) and the
  answer streams in. A reason match at that point is skipped rather than
  swapping one question for another (warning
  `reason_confirmation_skipped_options_first`). New mid-turn events
  (`streaming.turn_events`, `server.py`): `options` may arrive before
  `done`, and `recalled` {retracted} carries the "I remember" beat. The
  chat drops retracted options and shows a small "Oh wait, I remember what
  you meant." line. The slime does a gag (a 💡, sparks, smoke where the
  options were, one of a few rotating lines), and the answer's
  performance queues behind it instead of wiping it.
  `DisambiguationStore.get_turn_for_index` now returns the LATEST row for
  a turn, because a turn can hold a superseded assessment plus the row
  that decided what was shown. Tests that pinned "AssessAndBranch never
  runs on a memory match" were rewritten to the new order, with explicit
  pacing (`_Paced`). Open: whether (4) is worth the flicker is still
  unmeasured. Memory (embedding + one fast call) usually beats
  AssessAndBranch + DisambiguationOptions, so a live retract should be
  rare; the everyday win is options no longer waiting on memory.

- **2026-09-25** — Learn a topic: the backend (`topics.py`, `resources.py`,
  migrations 072-074). Your plan: keyword search or a document/link becomes
  branches with "select this" checkboxes that can branch further; the result
  is chapters and lessons with progress bars; the tutor's job in a lesson is
  to move the completion % through tasks, stay on the chapter, and connect to
  other chapters in balance; lessons complete with questions at the end; no
  locking. The Akinator question game was dropped by you.
  - *Shape:* `topic_explorations`/`topic_nodes` (the tree, append-only:
    expanding appends children, "more" appends further ones), `topics` ->
    `topic_chapters` -> `topic_lessons` -> `lesson_tasks` (written once),
    `lesson_task_events` (progress is the latest event per task, never a
    stored number), `topic_signals`, `sessions.lesson_id`. Selection rule: a
    ticked branch with no ticked ancestor is a chapter; ticked branches under
    it are its lessons in tree order; a chapter with none gets 3-6 planned.
    Every lesson has 3-5 tasks ending in exactly one `check`.
  - *Tutoring:* a lesson chat is an ordinary session through `SessionLoop`
    (memory, options, stated preferences, claims, `/end` consolidation all
    apply unchanged). `LessonHooks` passes a lesson context to
    `AssessAndBranch` (so it doesn't branch on what the lesson settles) and
    `FinalAnswer` (course/chapter/lesson, task list with the CURRENT one,
    the other chapters with their %, and the rules: drive the current task,
    stay in the chapter, at most one named connection per answer, run the
    end-of-lesson questions last). Only passed when set, so Sandbox node
    inputs and prompts are byte-identical. After each answer
    `JudgeLessonProgress` (via `_call_node`) judges the current task from
    what the STUDENT did; a completion appends an event and pushes a
    `progress` WebSocket frame.
  - *Personalization in:* `build_learner_profile` reads confirmed or
    student-approved thinking styles, non-archived claims (student edits
    applied; "observed, not yet proven" kept separate and weighed lightly),
    the latest stated preference, related `learner_facts`, the learner's
    slider levels and their other courses. It shapes branch order, lesson
    plans and task wording; the tutor gets the thinking style and confirmed
    traits on top of FinalAnswer's existing blocks. Each generation records
    what was used, and the API returns it as `personalized_by` ("Shaped by
    your thinking style, ...").
  - *Personalization out:* `topic_signals` logs searches, resources,
    expansions, selections INCLUDING branches shown but not chosen, lesson
    open order (sequential vs jumping around — informative because nothing
    is locked), turns per task, end-of-lesson check results and drifting off
    the chapter. Episodic, same wall as invariants 6/8: nothing writes them
    into claims or thinking styles. **Next step (not built):** feed them to
    claim extraction as evidence — claims.py has no clean evidence hook for
    non-interaction episodes yet, so this needs a design decision first.
  - *Audit:* calls outside a chat (branches, resource outlines, lesson
    plans) have no session, so `topic_generations` keeps node name, full
    input (incl. prompt and personalization used) and parsed output — the
    same approach as `feed_generations`.
  - *Resources:* PDFs via `pypdf` (20 MB, 300 pages; scanned PDFs with no
    text are refused with a clear message), links via `httpx` (http/https
    only, every address the host resolves to must be public — checked again
    on each of up to 3 redirects — 5 MB, 15 s; a PDF link is read as a PDF).
    Known gap, accepted for a local-only server: DNS rebinding between the
    check and the connect. Revisit before any deployment.
  - *Dependencies:* `pypdf`, `python-multipart` (file upload) and `httpx`
    (was only transitive) added to `pyproject.toml`/`uv.lock`.

- **2026-09-25** — Learn a topic: the app side (`app/lib/topic/`). The
  Akinator question game was dropped by you; keyword search and PDF/link
  maps replace it.
  - *Ways in:* Modes → Learn a topic opens a topics home (your courses with
    progress bars) with three starts: keyword search, **Upload a PDF**
    (`file_picker`, read as bytes so web and Windows both work) and **Use a
    web link** (checked for http/https before sending).
  - *Explorer:* a vertical tree. Every branch has a **Select** checkbox and a
    **Branch further** control that fetches children and animates them in
    (AnimatedSize with staggered fade/rise). **More branches** appends new
    ones. Hiding and showing a branch doesn't call the server again.
    Ticking a parent doesn't tick its children. **Build my course** sends
    the ticked ids in the order they were ticked.
  - *Course and path:* the topic page shows an overall bar and a card per
    chapter (bar + %, lessons with status). Tapping the overall bar opens
    the Duolingo-style path: chapter banners and round lesson nodes on a
    winding road (done filled, in progress with a % ring, not started
    outlined, a START/CONTINUE tag on the next one). **Nothing is locked.**
  - *Lesson chat:* the normal chat (same WebSocket, so the stage works here
    too) plus a task checklist whose last item is the end-of-lesson
    questions, lesson/chapter/topic bars updated live from `progress` frames
    (`ChatController.progressEvents`, never chat content), and the
    length/depth sliders. On a phone the tasks fold into a strip.
  - *History:* a topic chat opens into its lesson when the row carries
    `lesson_id`. Personalization shows as a "Shaped by: …" note wherever
    the server sends `personalized_by`.

- **2026-09-24** — Session knobs reworked into live sliders; tone removed
  (your words: "remove the tone option as well, make the knobs depth and
  size be scroll bars such that the answer expands and increases in depth
  as scroll wheel moves in real time").
  - *Sliders:* length and depth are 0-100 levels (migration 071,
    `sessions.answer_length_level` / `depth_level`, default 50). Drag, or
    scroll the mouse wheel over a slider (down = more, 5 per notch).
    `session_knobs.render_knob_directive` maps length to a target word count
    on a log scale (0 → ~20 words, 50 → ~167, what an untouched answer
    measured live, 100 → ~1400) and depth to five gist→rigorous bands, and
    renders the level itself so every move changes the prompt. 50/50 still
    renders '' (an untouched chat's prompt is byte-identical).
  - *Live rewrite:* ~600 ms after the slider rests, the app saves the levels
    and sends `{"type": "regenerate"}` on the chat WebSocket; the server
    re-runs ONLY `FinalAnswer` for the latest answered turn, with that turn's
    recorded inputs except the new directive, and streams it in place
    (`regen_start` / `regen_delta` / `regen_done`, tagged with the client's
    request id). A newer move cancels a rewrite still in flight. If the
    latest turn offered options, the levels just apply to the next answer.
  - *Append-only:* the original answer is never overwritten (it stays the
    turn's `FinalAnswer` row in node_calls). Each rewrite is its own
    `RegenerateAnswer` node_calls row (invariant 2) and a new
    `answer_versions` row (version 1, 2, …); a resumed chat and the next
    turn's recent-history context show the latest version.
  - *Tone:* removed from the API, the prompt and the app. The migration-063
    text columns (`answer_length`, `depth`, `tone`) stay on `sessions`,
    dormant.
  - *Dev environment note:* on this machine, new connections to
    `localhost:5435` hang (Docker publishes the port on IPv6 as well, and
    `localhost` tries `::1` first) while `127.0.0.1:5435` connects in 20 ms.
    Tests pass with `DATABASE_URL`/`VERSA_TEST_DATABASE_URL` pointed at
    127.0.0.1; `.env` itself was left untouched.

- **2026-09-24** — Stage character engine (wireframe 4, the user's
  direction: a slime with an expressive face that acts the topic out with
  some humor, spawning things as it explains). `app/lib/stage/`: a fixed
  JSON action vocabulary (`script.dart`: spawn, move, approach, push, emote,
  say, look, jump, shake, remove, wait, together, ask), an engine with its
  own clock (`engine.dart`), the painter (`painter.dart`: the blob with nine
  moods, blinking, eyes that follow things, squash and stretch, leaning,
  sweat, sparkles, and a "?" over its head while asking) and the view
  (`stage_view.dart`). The engine knows nothing about any topic. The one
  hand-written skit (`skits.dart`, force) only tests the engine's feel.
  The end goal is an LLM emitting the same JSON for whatever the chat is
  about, and an unknown action degrades to a no-op instead of crashing the
  stage. **Options moved out of the chat:** while the stage is open, the
  blob asks the ambiguity question and the options appear as reply bubbles
  beneath it (`sandbox_screen.dart` drops the options turn from the message
  list). A click goes through the same `ChatController.pickOption`. When
  the stage is minimized or off, the options return inline. Not built yet:
  the backend node that generates scripts (it must go through
  `_call_node`, with scripts stored in an append-only table, migration
  072+), and letting the performance run in step with the streamed answer.
  Same day, per the user: the body now follows the slime in "That Time I
  Got Reincarnated as a Slime": a round top swelling into a bulge that
  rolls under where it sits, in light green (the user's choice, not the
  anime's blue). The vocabulary also grew so a script can show "anything":
  `emoji` (any object the emoji font has), drawing primitives (`circle`,
  `rect`, `triangle`, `line`, `path`, a moving `wave`), prop actions
  (`scale`, `spin`, `recolor`, `relabel`, `carry`, `drop`, `throw`), a
  `wear` hat, and eleven particle `effect`s (fire, rain, smoke, sparks,
  confetti, ...). An unknown prop kind renders as its name. Second demo
  skit: gravity.
  Then the real LLM was wired in (`src/versa/stage.py`, `StageDirector`,
  fast tier, prompt `STAGE:DIRECT`). The split is the user's: the chat
  holds the long explanation; the slime acts out what the topic is about
  and asks the ambiguity options. One streamed call per turn returns JSON
  Lines, one action per line. Each action is validated and clamped
  (`sanitize_action`: no `ask`, numbers kept on the stage) and forwarded
  as soon as its line is complete. The call starts when the message
  arrives, in parallel with AssessAndBranch. Its actions are HELD until
  the answer's first word (`server.py` `_Performance`), so an options
  turn never shows a performance. That turn's call still runs to the end
  and is recorded to `node_calls` (invariant 2). Measured live: when the
  performance only started at the first word, its first action lagged the
  finished answer by about 3.5 s; with the early start, the whole script
  was ready at the first word. Only runs when the app says the stage is
  showing (`"stage": true` on the message frame), so a hidden stage costs
  no calls. No new table: `node_calls` holds each script.

- **2026-09-24** — History page, Thinking-style page, and session-end
  consolidation built (wireframe 1 nav items).
  - *Consolidation:* `versa serve` never consolidated, so thinking-style and
    claims never ran for app users. Now `POST /api/sessions/{id}/end` (called
    by the app whenever a chat is left) and a sweep of the learner's older
    unconsolidated chats on every new session. Gate reuses
    `min_turns_for_cli_auto_consolidation` (6); `sessions.consolidated_at`
    (migration 064) makes it run once. `ThinkingStyleStore.confirm` now
    ignores a session already counted for that candidate.
  - *History:* `GET /api/learners/{id}/sessions/all`; page groups by day with
    client-side search; Sandbox chats reopen.
  - *Thinking-style page:* shows EVERY candidate/claim status (your call:
    "show everything with edit, view, delete options"). View / Approve / Edit /
    Delete / Restore / Undo via append-only `student_reviews` (migration 065).
    **"Delete" = archive**, because of the append-only invariants: nothing is
    removed; archived items hide behind "Show archived". Approve is recorded but
    never changes confidence or confirmation counts (only real evidence does).
  - *"Why Versa thinks this":* one fast-tier `ExplainItem` call over stored
    evidence only, cached per item + evidence count (`explanation_cache`, 066).
  - *Ask about it:* per-item Q&A (`item_qna`, 067), grounded in the same
    evidence; an explicit correction ("no, I like theory first") is applied as
    a chat-sourced review with the server writing the "Updated: A -> B" line;
    plain questions change nothing; Undo appends the inverse review.
  - *Sandbox chat updates claims:* when `ClassifyStatedPreference` (already a
    background call) finds an explicit preference, `MatchStatedPreferenceToClaim`
    revises a same-label claim (review, `source='sandbox_chat'`, migration 068)
    or creates one through `claims.reconcile_candidate` (`ClaimSource.STATED`).
    Pushed as a `claim_update` WebSocket event, replayed on resume; the chat
    shows "Noted: … · View · Undo". Claims only — a single stated preference
    doesn't map onto an order-of-reasoning thinking-style pattern.
  - README corrected: a confirmed thinking style shapes which options are
    offered; it does not reach `FinalAnswer`.

- **2026-09-24** — Home feed built (wireframe 1: "recommendations, topics to
  learn just to learn, similar topic from past, just as youtube feed").
  `GET /api/learners/{id}/feed` (`src/versa/feed.py`, its own router) returns
  three sections: **Continue** (recent Sandbox chats with at least one turn,
  read live every time), **For you** (topics next to what the learner asked,
  each with a reason naming its source, e.g. "Because you asked about
  recursion") and **Just to explore** (topics outside their history). The two
  topic sections come from ONE fast-tier call (`GenerateFeed`, prompt
  `FEED:RECOMMEND`) over the opening messages of the last 8 chats plus the
  last 12 `learner_facts`. Someone with no history gets Explore only; the
  parser drops any related items for them rather than invent a link.
  Generations are cached in the append-only `feed_generations` table
  (migration 070) and reused for 6 h, or regenerated on the Refresh button or
  when a learner who had no history at generation time now has some; a
  failed call is recorded with `error` and the last good feed is served.
  **Audit trail:** `node_calls.session_id` is a NOT NULL FK to `sessions`,
  and a feed belongs to a learner, not a chat (a new learner has no
  session), so `feed_generations` itself keeps the node name, full input
  (history + prompt) and parsed output for every call instead. App: Home is a
  grid of cards with All / Continue / For you / Explore chips; a Continue
  card reopens that chat, a topic card opens a new Sandbox chat with its
  starter message typed but not sent (`composer_draft.dart`). Live check for
  learner AS: 6 related items tied to their actual chats (Ohm's law practice
  from the circuits chat, recursive base cases from the recursion chat, the
  Reign of Terror from the French Revolution chat, …), 6 explore items across
  fields, 7.6 s to generate, 0.03 s from cache.
- **2026-09-24** — Session knobs are real (wireframe 3's right rail).
  Per-session, changeable mid-chat: `answer_length` (terse / balanced /
  full), `depth` (gist / standard / rigorous), `tone` (direct / socratic),
  stored on `sessions` (migration 063, CHECK-constrained, defaults
  balanced/standard/direct). `PATCH|GET /api/sessions/{id}/knobs`;
  `session_knobs.render_knob_directive` turns them into a requirement
  block passed to `FinalAnswer` (and only it) as `knob_directive`, so it is
  recorded in `node_calls.input_json`; all-default renders '' so untouched
  sessions' prompts are byte-identical to before. Semantics: length caps or
  expands the answer, depth trades gist vs mechanism/edge cases, socratic
  guides with a question instead of handing over the conclusion. They
  override the default 'no structure' wording, and apply from the NEXT
  answer. The app shows them as segmented controls in the Sandbox rail and
  reloads them on resume. Live check (real Gemini, same question): default
  168 words, terse+gist 65, full+rigorous 679. Not built: a global default
  for new chats; the rail is wide-screen only (as before).
- **2026-09-24** — Removed Guess Mode entirely, by the user's decision
  ("not built according to my expectation"). Deleted: the `GuessApproach`
  node and its `ApproachGuess`/`GuessedApproachOption` models, the
  `GUESS:APPROACH` prompt/stub/schema, every `guess_mode` branch and
  per-session round/axis/chain state in `loop.py`, `TranscriptStore`'s
  `guess_mode` parameter and getter, `SessionIn`/`SessionOut.guess_mode`,
  `tests/test_guess_mode.py`, and the Flutter knob, Settings switch,
  preference, API field and empty-state copy. Kept, deliberately dormant:
  migrations 061/062 (the `sessions.guess_mode` column is simply unused;
  no rows were deleted, per the append-only invariants),
  `DisambiguationTurnKind.APPROACH_GUESS` (so legacy rows load) and a
  read-only path in `session_history.py` so chats already recorded in
  Guess Mode still open. Reason confirmation is untouched. The earlier
  entries below describe the removed feature and are kept as history.

- **2026-09-23** — Fixed a more serious bug than round-repetition, found
  from the very next screenshot ("what is wrong now?"): after a 4-round
  chain, the student explicitly clicked "rigorous mathematical treatment"
  and the final answer OPENED with "No, we should not focus on a rigorous
  mathematical treatment" — arguing against the very thing just confirmed.
  Root cause, two compounding parts: (1) `FinalAnswer` only ever received
  the LATEST pick as `branch_context`; every earlier pick in the chain
  (e.g. "broad survey") was visible only through `recent_history`'s
  explicitly weaker "for continuity only, do not restate" framing, not
  the "this is confirmed, answer accordingly" one `branch_context` gets
  — so an earlier, weakly-framed pick could out-weigh the latest,
  strongly-framed one. (2) `branch_context` is frequently a
  QUESTION-phrased string (options are written as questions throughout
  this codebase, e.g. "Should we focus on..."), and the prompt's own
  wording ("they confirmed they meant this specific reading: '...'")
  apparently wasn't forceful enough to stop the model from treating an
  embedded question as something to actually weigh and answer, rather
  than a settled fact to build from.
  Fix for (1): new `SessionLoop._combine_guess_chain` joins every pick in
  the CURRENT chain (`_guess_chain_picks`, a new per-session list, same
  reset point as the other two Guess Mode counters) into one
  `branch_context`, so every confirmed choice gets the strong framing,
  not just the last one. A single pick (the ordinary, non-chained case)
  passes through unchanged -- zero behavior change outside Guess Mode.
  Fix for (2): `FinalAnswer`'s own branch_context framing (disambiguate.py)
  now explicitly states it is a SETTLED FACT, not a question to weigh,
  answer, reconsider, or argue against, and explicitly forbids opening by
  addressing it as a proposal or presenting the alternative not chosen —
  applies to EVERY branch_context use, not just Guess Mode's, since nothing
  about the original weak framing was Guess-Mode-specific either.
  2 new tests (the prompt actually contains the stronger framing; a
  2-round chain's branch_context is the full "pick1 AND pick2", not just
  the latest) plus 1 existing test's assertion corrected to the new,
  intended combined-chain value. Full suite green.
- **2026-09-23** — Fixed a real bug the continuous version immediately
  surfaced, your words: "its dumb, its not good at all." A live screenshot
  showed round 2 and round 3 of a chain both asking the SAME axis
  (narrow/topic-by-topic vs. broad/all-in-one) reworded — `GuessApproach`
  had no memory of which axis it already asked about, so nothing stopped
  it re-picking one. Fixed with a hard, code-level guarantee, not just a
  prompt instruction that can be ignored: `GuessApproach.run` now takes
  `excluded_axes`, threaded from a new per-session
  `SessionLoop._guess_used_axes` (appended to every time an offer is
  built, reset alongside `_guess_round_counts` the instant a real answer
  is given). If the model names an excluded axis anyway, it's retried
  once (same discipline as a malformed response), and if it repeats even
  then, the result is forced to "nothing open" — a repeated axis is worse
  than under-asking. Also tightened the prompt itself: a live screenshot
  showed options phrased as "would you like A, or B?" — restating both
  sides inside one button instead of committing to a stance — now
  explicitly forbidden. 3 new tests (prompt names the excluded axes; a
  picked-anyway excluded axis is forced to nothing-open at the node
  level; a full loop-wiring test reproducing the exact reported bug shape
  and proving round 2 no longer repeats round 1's axis). Full suite green.
- **2026-09-23** — Made Guess Mode continuous: a click no longer finalizes
  unconditionally. New `SessionLoop._continue_narrowing` runs after every
  click (except REASON_CONFIRMATION's fixed yes/no, unaffected) — the same
  "is anything still open" question a fresh message gets, using the
  reading just picked as the message and the accumulated `recent_history`
  (which already carries the chain of prior picks, no new tracking needed)
  as context. Deliberately a SEPARATE method from the fresh-message flow,
  not a shared refactor of it — lower risk to that already-tested code,
  and it intentionally skips the same interactions/claims wiring
  `APPROACH_GUESS` already skips, applied consistently to every
  continuation round now, not just approach ones. A new
  `_MAX_GUESS_ROUNDS` (5) per-session counter, reset the instant a real
  answer is given, caps a pathological "always finds something to ask"
  loop — a technical ceiling, not a UX brake (see the "Ideas" entry above
  for why that distinction is deliberate, per your own call).
  Two things this surfaced that needed fixing: (1) a resumed chat's
  reconstruction (`session_history.py`) only ever suppressed the echoed
  "you said" bubble on a turn that led to an ANSWER — a click that instead
  led into another round of options had no such check, so it would have
  shown the previous round's clicked option text as if the student had
  typed it. Fixed with a new `_was_click_continuation` check (a MATCHED
  branch, from the turn right before, whose statement matches this turn's
  text — the same kind of durable audit-trail signal the existing
  "answer" check already relies on, not text-guessing). (2) The server's
  WebSocket handler and the Flutter client turned out to need NO changes
  at all — both already just ask "are there pending options after this
  turn" generically, whether the turn was a fresh message or a click, so
  chaining multiple rounds over the same connection already worked once
  the backend supported it.
  9 new/changed tests (2 continuous-chaining, 1 round-cap, 1 resumed-chain
  reconstruction, plus fixing 1 existing test whose static stub couldn't
  express "offers once, then nothing further open" under the new
  behavior). Full suite green (472 passed).
  Live verification was inconclusive today: hit the same free-tier daily
  quota (20 requests/day/model) already documented earlier — a live
  five-round chain attempt returned real `429`s partway through. The
  underlying mechanism (`GuessApproach`, click resolution, chained DB
  persistence) was already proven live earlier today for a single round;
  this change is a comparatively small, fully-tested addition on top of
  that — worth a clean live re-run once the quota resets, not re-attempted
  further today.
- **2026-09-23** — Built "Guess Mode" (`sessions.guess_mode`, migration 061)
  and, first, the design fix it was asked to build on top of: the stage
  panel (2026-09-22's "Animations" knob) now takes EQUAL width against the
  chat instead of a fixed 260px column, and both the app's own main nav
  rail (shell.dart) and the session-knobs rail now minimize to a thin strip
  the same way the chat-history rail and stage panel already did — every
  side panel in the app now uses the identical minimize language
  (`widgets/collapsed_rail.dart`, generalized to a `RailSide` for the two
  new right-side cases).
  Guess Mode itself: `disambiguate.GuessApproach` (new node, fast tier) —
  once a message is judged NOT topically ambiguous by the EXISTING
  `AssessAndBranch`, this asks a second, narrower question: is there still
  something genuinely open about HOW to teach it? Reuses the full
  `ApproachAxis` vocabulary `DisambiguationOptions` already has, but
  generates the axis and both options directly from the message — there
  are no pre-existing branches to pick an axis FROM, unlike the ordinary
  ambiguity path. A new `DisambiguationTurnKind.APPROACH_GUESS` (migration
  062) marks the turn; a click resolves through the SAME generic
  branch-click path an ordinary ambiguity click already uses (the picked
  option genuinely is a reading, just of approach instead of subject) — no
  special-casing needed, unlike REASON_CONFIRMATION's fixed yes/no. A
  no-axis result ("nothing open here") is the common, correct case, not a
  degrade — most messages have nothing worth asking about, and forcing a
  choice every turn would make the game friction instead of a game.
  Client: a new "Guess mode" knob (Sandbox's session knobs and Settings,
  same live-switch pattern "Animations" already established), threaded
  through session creation (`sessions.guess_mode`, fixed at creation like
  `ablation_config` — a setting for the NEXT chat, not a mid-conversation
  switch), and an empty-state framing change ("Let's see if I can guess,
  {name}.") so the mechanism reads as an invited game, per your own
  framing, not a hidden one.
  Two real bugs a live run caught, neither exercised by the stub-based test
  suite: (1) `disambiguation_turns.kind` has a Postgres CHECK constraint
  (migration 058) enumerating exactly two allowed values — every real
  approach-guess turn would have failed outright with a `CheckViolationError`
  until migration 062 widened it to a third. (2) `session_history.py`'s
  reconstruction only checked for `DisambiguationOptions` node calls or
  `REASON_CONFIRMATION` kind when deciding whether an offer turn was
  "options" or "pending" — an approach-guess offer (which calls neither)
  would have shown a resumed chat as if the turn had recorded nothing at
  all; fixed with the same kind of check REASON_CONFIRMATION already
  needed. Both caught before commit, by a real end-to-end run against live
  Gemini, not by the deterministic suite (which had to be extended
  afterward to cover them). Also caught, separately: the running `versa
  serve` process doesn't hot-reload — every one of these backend changes
  was invisible to the live server until it was restarted, which briefly
  looked like a code bug (`guess_mode` silently coming back `false` no
  matter what the client sent) before the real cause was found.
  10 new node-level tests, 6 new loop-wiring/resume tests, 6 new Flutter
  tests. Full suite green. Verified live end to end against real Gemini,
  outside the browser and in it: a genuinely topic-ambiguous message
  ("explain the chain rule") still correctly took the ordinary
  subject-disambiguation path unchanged; a topic-clear one ("explain what a
  stack data structure is") correctly triggered `APPROACH_GUESS`, offered a
  formal-definition-vs-analogy choice, and the follow-up answer visibly led
  with the picked approach.
- **2026-09-22** — Fixed two real bugs in "ask for confirmation directly",
  found by two adversarial live sessions run against the SAME throwaway
  learner (real Gemini, not committed scripts): (1) an explicit reversal of
  a stated preference ("forget examples, just give me the abstract
  definition") still cleared the reason-embedding similarity bar and was
  offered back as "is that right?" — pure cosine similarity cannot tell
  "still wants X" apart from "just said to stop doing X," since the two are
  topically near-identical text; this fully swallowed the reversal turn
  (never answered, never reached `CLASSIFY:STATED_PREFERENCE`), so the
  stale preference kept being enforced afterward. (2) A declined reason
  confirmation was offered again on the very next relevant message —
  nothing recorded that this learner had already said no to it.
  Fix for (1): `memory.ConfirmReasonRelevance` (new node, fast tier,
  `CONFIRM:REASON_RELEVANT` schema) — a cheap gate in front of the offer
  that says whether the CURRENT message already contradicts the matched
  reason; if so, the match is treated as if nothing had matched at all, and
  the turn proceeds through the normal `AssessAndBranch`/`FinalAnswer`
  path, so the real message finally gets answered and classified. Does NOT
  reintroduce `ConfirmFactMatch` for reason matches generally — a genuine
  match still goes straight to the student with no LLM standing in for
  their answer; this only screens the one failure mode where the message
  has already answered the question itself.
  Fix for (2): `diagnostics.TurnDiagnosticsStore.declined_fact_ids_for_learner`
  — a JOIN against the EXISTING `reason_confirmed_by_student`/
  `matched_fact_id` columns, no new schema, checked before offering a
  confirmation. Along the way, found and fixed a third, smaller bug this
  surfaced: the click-resolution turn that records a "no" never actually
  stored WHICH fact was declined (`matched_fact_id` was never threaded
  through that diagnostics call) — without that, (2)'s fix could not have
  worked at all. Both fixes are independent and address different
  scenarios: (1) catches a contradiction even on a fact that was never
  previously offered; (2) catches a repeat offer of a fact regardless of
  whether the new message merely echoes it or contradicts it.
  9 new/changed tests (2 in test_disambiguate_nodes.py-adjacent
  test_reason_confirmation.py, 1 in test_diagnostics_store.py), full suite
  green. NOT fixed (flagged, out of scope for today): a failed background
  node call (confirmed live — the free tier's daily quota on the fast-tier
  model was exhausted mid-session) leaves ZERO durable trace anywhere
  (`_call_node` only writes to `node_calls` after `.run()` returns) — a
  real tension with the audit-trail-is-the-product premise, worth its own
  entry rather than a rushed fix here.
- **2026-09-22** — Applied migrations 056-060 to the dev database and tried
  "ask for confirmation directly" against real Gemini (throwaway learner,
  not committed script). Confirmed end to end: the reason-writing prompt
  produced natural, second-person text ("you tend to want a concrete
  example first"); the confirmation message and Yes/No options rendered
  correctly; clicking "Yes" wrote a real `reason_confirmed` fact and set
  `reason_confirmed_by_student=True`; FinalAnswer's answer actually honored
  the confirmed reason (led with a fully worked concrete example before the
  general rule, unprompted). A real, honest finding for the still-open
  "unmeasured against real usage" question: a natural two-turn, different-
  topic-same-preference attempt did NOT organically clear the 0.72
  similarity bar — inconclusive rather than negative, since only two turns
  were sent before switching to a controlled trigger, so a later turn was
  never given a chance to match the reason turn 1 itself wrote. Worth a
  longer, more turns natural-usage trial before concluding anything about
  the threshold.
- **2026-09-22** — Built "ask for confirmation directly": a REASON-based
  memory match (`matched_via="reason"`) no longer goes through
  `ConfirmFactMatch`'s silent LLM judgment — it's offered to the student as
  a direct yes/no, reusing the exact `disambiguation_turns`/`branches`/
  `options` click-resolve machinery an ordinary ambiguity turn uses, just
  with two fixed Python-literal branches (`disambiguate.REASON_CONFIRM_
  YES_TEXT`/`REASON_CONFIRM_NO_TEXT`) instead of LLM-generated readings — no
  new tables, and actually CHEAPER than before (no ConfirmFactMatch call).
  New `DisambiguationTurn.kind` (migration 058) distinguishes this from an
  ordinary ambiguity turn, since a click on it resolves completely
  differently (a yes/no about a claimed reason, never a reading choice).
  "Yes" writes a new `LearnerFactType.REASON_CONFIRMED` fact (migration
  059) — durably, queryably stronger evidence than an LLM-inferred
  `DIRECT_ANSWER`; "no" falls back to a plain direct answer, no memory
  context used, no re-disambiguation attempted (a deliberate simplification
  — see the idea entry). `turn_diagnostics.reason_confirmation_offered`/
  `reason_confirmed_by_student` (migration 060) make the whole exchange
  auditable. Typing past the confirmation instead of clicking reuses the
  EXISTING typed-past mechanism for free, no new code needed. `session_
  history.py` reconstructs a resumed chat's view of both the offer and the
  click correctly.
  Caught one real bug along the way, not just a test-writing slip: the
  branch's stored text and the live confirmation message were initially two
  DIFFERENT strings (situation/resolution format vs. the actual reason) —
  a resumed chat would have shown different confirmation wording than what
  the student actually saw. Fixed by using the same reason text for both,
  which also makes it a better `memory_context` for FinalAnswer on a "yes"
  than the old situation/resolution format was. 5 new tests
  (`test_reason_confirmation.py`) plus one updated (the old reason-match
  test asserted the now-replaced silent-answer behavior). Full suite green
  (456 passed).
  Deliberately NOT wired into `interactions`/`claims` — no `kind`/`axis`
  fits a yes/no reason confirmation, and that integration is explicitly
  step 3 of the "richer options" entry, still blocked on its own
  circularity guard.
- **2026-09-22** — Built "richer, context-aware options" step 1 (the other
  half of the options/claims/reason split — this instance took step 1 while
  step 2, reason, was built in parallel elsewhere): `DisambiguationOptions`
  was the only reasoning node in the turn that never saw the learner's own
  history, a stated preference, or a promoted claim, despite phrasing what
  the student actually clicks. `disambiguate._options_prompt` and
  `DisambiguationOptions.run()` gain `thinking_style_hint`,
  `learner_history_block`, `structural_requirement`, and
  `claim_constraints_block` — the identical four kinds of context
  `FinalAnswer` already gets, placed in the identical order (structural
  requirement and claim constraints first, then the thinking-style note,
  then the history block). `loop.py`'s block assembly (history-block
  retrieval, the stated-preference lookup, the promoted-claims lookup) was
  extracted out of `_run_final_answer` into a shared
  `_assemble_personalization_blocks` so the options call site could reuse it
  without duplicating the logic; called fresh before `DisambiguationOptions`
  since FinalAnswer never runs on a turn that ends in showing options (there
  is nothing to reuse). `thinking_style_hint` was already computed earlier
  in the turn for `AssessAndBranch` and just needed threading through.
  Deliberately does NOT touch `reference_bindings_block` — the options call
  already has its own, differently-framed `reference_binding_hint`, so
  adding the other would duplicate rather than add context. Step 2/3 (reason
  feedback, the claims loop) are untouched — this is personalization context
  only, no claim or reason yet shapes which options get generated. Two new
  node-level tests (context blocks actually reach the prompt; absent when
  not given) plus a full-loop test proving a real stated preference reaches
  a live `DisambiguationOptions` node_calls row on an ambiguous turn, not
  just a `FinalAnswer` one. Full suite green.
- **2026-09-22** — Built the "store the reason" idea (step 2 of the
  options/claims/reason split — the other instance took step 1, richer
  options, in parallel): `learner_facts` gains `reason` + `reason_embedding`
  (migration 056), both nullable and ADDITIVE alongside the existing
  situation+resolution `embedding`, never replacing it, per the mitigation
  plan this idea settled on. `WriteLearnerFact`'s prompt asks for a reason
  only when THIS specific exchange actually suggests one — the schema itself
  (`llm.py`'s `_SCHEMA_BY_PREFIX["WRITE:FACT"]`) marks it optional and
  non-required, so abstaining is a real, unpenalized output, not something
  bolted on after the fact — a missing reason costs no extra embedding call.
  `EmbedAndSearchFacts` now runs both searches (situation/resolution and
  reason, reusing the one query embedding already computed — no extra API
  call) and returns whichever real match is stronger; `matched_via`
  ("situation_resolution" | "reason") is threaded through to
  `turn_diagnostics.memory_match_via` (migration 057) purely for
  measurement — it changes nothing about whether a match is trusted,
  `ConfirmFactMatch`'s judgment call still gates every skip either way.
  `MemoryConfig.reason_similarity_threshold` is its own knob, starting equal
  to `fact_similarity_threshold` but independently tunable once real data
  exists. Five new tests cover the reason/reason_embedding round-trip, the
  dual search (ranks by reason, excludes reason-less facts even with a
  perfect situation/resolution match), the embed-only-when-present behavior,
  and an end-to-end reason-only match through `handle_turn`. Full suite green
  (448 passed). NOT yet done: the actual measurement the mitigation plan
  calls for (comparing `ConfirmFactMatch` agreement rate by `memory_match_via`
  once real reason-tagged facts accumulate) — needs real usage first.
- **2026-09-22** — Added the chat-history sidebar: `sessions.app_mode` (migration
  055, separate from `ablation_config` — product mode vs. reasoning
  architecture), `TranscriptStore.list_session_summaries` (per-mode, ordered by
  last activity, with a preview), `session_history.py` (replays a session's real
  `turns`/`node_calls`/`disambiguation_*` rows into the same turn-by-turn shape
  the live chat renders — no new write path), two endpoints
  (`GET /api/learners/{id}/sessions`, `GET /api/sessions/{id}/history`), and a
  resumable `ChatController` (`resumeSessionId`). Shown only inside a mode's own
  chat screen (a persistent left rail on wide, a header icon + sheet on
  narrow), never on the Modes picker — per your request. Fixed two real bugs
  found along the way: typing past open options left the old buttons looking
  clickable (now closed locally, mirroring the server); a double-tap or a
  typed-past button could replay/error confusingly (now refused server-side
  and reflected in the UI via a new `optionsOpen` flag). Checked end to end in
  a real browser against live Gemini: a real answer, "New chat", the old chat
  still listed with its real preview, clicking it back in replays the exact
  original conversation.
- **2026-09-22** — Built the first app: `versa serve` (FastAPI; REST + a WebSocket
  per chat), Flutter Sandbox chat with plain fonts, labelled placeholders for the
  rest. Checked end to end in a real browser against live Gemini (answer streams,
  options appear, a click answers in ~2 s, phone layout, reconnect).
- **2026-09-22** — Chat transport is a WebSocket (works the same on web, desktop and
  phones); identity is a name (no password yet); the app shows per-answer timing
  (measured on the device) with a switch in Settings.
- **2026-09-22** — The web app is served by `versa serve` itself at
  http://localhost:8000; `scripts/start.ps1` starts everything.
- **2026-09-22** — Tests use their own database (`VERSA_TEST_DATABASE_URL`, `versa_test`);
  the dev database's port is configurable (`VERSA_DB_PORT`, 5435 locally).
- **2026-09-22** — Current turn speed is enough; latency work paused.
- **2026-09-21** — App built with **Flutter** (desktop first, mobile later).
- **2026-09-21** — First build is **Sandbox chat only**; other modes are
  chats-with-a-label for now.
- **2026-09-21** — Target: first words on screen within ~2 s of sending
  (aspiration; measured 2.3–4.2 s).
- **2026-09-21** — Model thinking is limited per tier: fast = 256-token budget
  (keeps reference resolution correct, +0.3 s), best = `minimal`.
- **2026-09-21** — Project renamed `probe` → `versa`; old web UI traces removed.
