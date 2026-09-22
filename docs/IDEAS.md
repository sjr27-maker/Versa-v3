# Versa — ideas & deferred work

The parking lot for things we want to do **later**. When you have an idea, or
we decide to defer something, it goes here instead of getting lost in chat.
Newest items go at the top of their section. Each item says who raised it,
when, and what it would take, so it can be picked up cold.

Status: `idea` (not decided) · `parked` (decided, not now) · `ready` (clear
enough to build) · `done` (moved to the log at the bottom).

---

## 1. Ideas

### Store the *reason*, make it the primary retrieval key — `parked`
*Raised by you, 2026-09-22.* Your words: "storing reasons of why this
response was given, as in the reason the situation will be explained, so in
semantic retrieval the reason becomes a main thing" — and, on why it matters:
"this will contribute to thinking style detection too."

**Reading (confirmed by you):** today `WriteLearnerFact` (`memory.py`) writes
`situation` + `resolution` and embeds their concatenation — a description of
*what* was unclear and *what* was chosen. This adds a third field, `reason` —
the causal justification for why that response fit this specific student —
and makes retrieval match on the reason (instead of, or alongside, the
surface situation/resolution). The bet: two episodes with nothing in common
on the surface can share the same underlying reason, so reason-based
retrieval could surface a relevant memory that situation/resolution
similarity would miss entirely — which is also why it could feed `claims.py`
and thinking-style detection richer signal than raw episodes do today.

- **Why it helps:** richer semantic retrieval (matches on *why*, not just
  *what*); a natural stepping-stone toward claim extraction and thinking-style
  detection, which are already trying to detect recurring reasons, just
  slowly and only after independent confirmation.
- **The open concern (yours):** how good will the LLM-generated reasoning
  actually be? `WriteLearnerFact` runs *after* `FinalAnswer` already
  answered — a `reason` is a reconstructed causal story, not a trace of what
  actually happened, and a fluent model produces a plausible-sounding story
  on every call whether or not it's true (the same confabulation risk
  `claims.py`'s own docstring names). There's also a drift risk in the same
  shape as the topic-centroid cascade `interactions.py` already killed
  (`topics_removal`): reason text is more abstract than situation/resolution
  by construction, so it's more exposed to genericizing if future reasons get
  seeded from matched-retrieval context.
- **Mitigation plan discussed, not built:** don't replace the existing
  embedding outright — add `reason` retrieval alongside it and measure
  whether a reason-based match gets confirmed by `ConfirmFactMatch` more
  often than a situation/resolution-based match before trusting it as
  primary. Treat a single-episode `reason` as a candidate needing
  corroboration, the same posture `claims.py` and `thinking_style_candidates`
  already take, rather than something asserted once and immediately weighted.
  Once there's enough data, run reason-driven retrieval hits through
  `score_predictions`-style calibration (bucket by confidence, check observed
  hit rate) the same way claim confidence is checked today.
- **What it would touch:** `memory.py` (`WriteLearnerFact`'s prompt + which
  text gets embedded), `models.py` (`LearnerFact` gets a `reason` field —
  additive, doesn't conflict with invariant 10's append-only rule), a new
  migration, `loop.py` only if more context needs threading into the write
  call, plus the memory test files.
- **Why parked, not building yet:** another instance was live-editing
  `src/versa/models.py` in this same working directory (uncommitted, for the
  chat-history-per-mode feature) when this came up — same file this idea
  needs to extend (`LearnerFact`). Revisit once that lands.

---

### Show options first, remember second ("oh wait…")  — `idea`
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
| **Learn a topic** (upload syllabus, search & select, completion %) | A way to turn a document into a structure to track. The concept graph was removed on evidence (CLAUDE.md invariant 4) — progress needs a different mechanism (e.g. a plain syllabus outline with no learner model on top). |
| **Exam preparation** (plan, quizzes, mock tests, weakness map, readiness) | Quiz/flashcard generation, spaced repetition, grading; a notion of exam date and syllabus. Nothing exists yet. |
| **Study with others** | Multi-user rooms, matching, moderation, voice/text. Backend has no users/auth today, only a learner label. |
| **Animations beside the chat** | LLM-generated interactive widgets per concept; new capability, biggest build. |
| **Home feed / recommendations** | A topic model (topic clustering was removed) + a recommender over history. |
| **Thinking-style page** | Can be built on `claims.py` / `thinking_style_candidates`, but only shows what the system can actually support. Under the current confidence clamp no claim reaches "promoted". Mock-up findings (retention, time-of-day, …) are **not** measured by anything. |
| **Session knobs** (answer length, depth, tone) | No such parameters in `FinalAnswer` yet; the stated-preference labels (brevity, steps, analogies) are the closest hook. |
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

---

## 6. Decisions log

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
