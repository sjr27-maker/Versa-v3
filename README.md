# Versa

**Learn, how you think.**

People often can't say what they want — the thought is there before the words for it. So Versa doesn't ask; it offers. After every answer it lays out where the idea could go ("where this could go": an example, the intuition, why it works, where it's used, a harder version, what comes next), and the student taps the one that matches what was already in their mind. Across the modes, the order and pattern in how they approach a topic — which directions they choose, in what order, and what they achieve — is what we call their **thinking style**; for now it is nothing more than that. Versa records it from their own chats and sets the noise aside (ability and mood), and the aim is answers that get better over time. Whether it does is to be shown with real learners: every check so far was run by machines on simulated students, and those runs are set aside.

Everything else feeds that or uses it: the length/depth/breadth sliders (the student setting their own range), stated preferences, clickable options when a message is ambiguous, and three of the app's modes. The definition, the evidence inventory, the design and where it stands are in [`docs/THINKING_STYLE.md`](docs/THINKING_STYLE.md).

The Python package and CLI are both named `versa`. This README describes the codebase as it currently stands.

---

## What it does

When a student's message is genuinely ambiguous, Versa generates a few distinct interpretations, turns them into natural clickable options, and lets the student resolve it in one tap — no guessing, no wrong assumptions baked into the answer. If the message is clear, it answers directly.

Every resolution is written down in plain English as a searchable memory: what was unclear, and what was chosen. Before answering anything new, Versa searches that memory first — if it has seen this kind of ambiguity from this student before, it already knows the answer and skips the question.

After every answer it offers three directions the idea could go, and reads the student's picks — where they start, what they take next, what they pass over, how fast they recognise a direction, what they ask for when none of the cards fit — as their thinking style. A pattern is only called theirs once it holds across topics and over time and predicts their later picks better than chance. It is built **only from that student's own chats and sessions**: nothing another learner does contributes to what Versa concludes about them.

Underneath the live tutoring loop, Versa also records a rich, append-only audit trail of everything it observed and every belief it formed. The audit trail is the point of the project: nothing is trusted just because it sounds right, and every claim about a learner traces back to stored, readable evidence.

---

## How we got here

The project began as a much larger architecture — a full learner model with hypotheses, a concept graph, a planner scoring six value terms, and a branching prediction tree — then tested it head-to-head against a single plain-LLM call on identical conversations. The comparison showed the heavy machinery wasn't earning its cost (it lost to a plain baseline at 20–40× the cost), so the concept graph and the full reasoning path were removed.

What runs today is the lean core that survived that measurement:

- a three-call **disambiguation flow** (`ReasoningMode.DISAMBIGUATE`, also called *minimal_branch* mode) that replaces the old branch-tree/planner machinery outright;
- a **memory layer** over pgvector that recalls how past ambiguity was resolved and reads the order and pattern of a learner's choices across sessions, gated so it never names a pattern on a hunch;
- an **interaction/retrieval pipeline** plus a **claim layer** that builds a durable, evidence-backed model of a learner's traits — currently recorded and wired into the answer prompt, while the predictive scoring it produces feeds nothing back into what the student sees yet;

---

## Architecture

A turn, as it runs today:

1. **Memory first.** The student's message is checked against what they resolved before (`learner_facts`); a past answer to the same ambiguity skips the question.
2. **Ambiguity check.** If the message could mean different things, Versa offers 2–4 readings as options (free — no Sparks); otherwise it answers straight away.
3. **The answer**, streamed as it is written. On a follow-up, it is shaped to the way this student usually goes into an idea (from their own picks); the first answer to anything new is always a normal one.
4. **"Where this could go."** Three cards, dealt at random from a library of 16 card types in four families (make it real, go deeper, make it simpler, go wider). The cards are never shaped by what Versa believes about the student (invariant 14) — they are the measuring instrument. Before they are sent, Versa records its guess of which one the student will take (invariant 20); after the pick it shows whether it was right.
5. **Reading the style.** Picks, passes, "other directions", slider moves and misses (the question they typed when no card fit) become the student's thinking-style patterns, derived fresh on every read (`style_patterns.py`) from their own data only, and the confirmed ones feed the ambiguity check, the options and Learn a topic.

A single reasoning mode is live: `minimal_branch` (`ReasoningMode.DISAMBIGUATE`). A plain-LLM `BASELINE` mode also exists as the measurement control.

### The layers, and what each is for

- **Disambiguation flow** (`disambiguate.py`) — the live reasoning mode: assess ambiguity, offer 2–4 distinct readings as options, answer once one is chosen. Every assessment is persisted whether or not it decides to branch.
- **Memory layer** (`memory.py`) — `learner_facts`: recall of how this student resolved past ambiguity, which can skip branching entirely when a past fact resolves the current message. (The old free-text thinking-style detector and its `thinking_style_candidates` were retired on 2026-09-30; their rows stay on record.)
- **Where this could go** (`directions.py`, `choice.py`) — the three cards after every answer, dealt at random from the card library; every pick is read against the hand it was taken from, so a card shown more often isn't mistaken for a preference. A pass (the student typing their own question instead) is kept as a miss, read against the library by embedding, and the next hand is widened at random.
- **Thinking style** (`style_patterns.py`, `observations.py`, `pick_prediction.py`) — the patterns: way in, what comes next, lean, range, conditional (e.g. stuck vs going fine), the shape of a chat, what they pass over, speed, and what they ask for when the cards miss. Each must pass every gate (evidence, sessions, topics, both halves of their history, and predicting later picks better than chance) before it is called confirmed. **Only the learner's own data** (2026-10-01): baselines are chance, never other learners. The guess before each set starts even and learns only from their picks. Shown on the Thinking-style page and `versa observations`.
- **Interaction / retrieval pipeline** (`interactions.py`, `retrieval.py`, `history_block.py`) — an append-only log of every exchange, with deterministic three-stage retrieval over a learner's own history only (never other learners'). Feeds the history block into the final-answer prompt; its LLM-based selection *predictions* are recorded but do not yet influence the student's response.
- **Claim layer** (`claims.py`) — a durable, cross-session model of a learner's standing preferences, extracted **only** from episodes that were actually surprising (high prediction error), each claim carrying a falsifiable `test` and promoted only past an evidence/topic-spread gate.
- **Parked: capability & instrument layers** (`archive/instrument_layer/`) — a separate capability-claim store plus purpose-built interactions (`locate`, `predict`) whose event streams were interpreted by hand-written deterministic contracts. Removed from the live architecture; nothing imports it and `pytest` never collects it. Restore steps are in that directory's README. Their migrations and tables remain in the schema, dormant.
- **Domain switch** (`domain_config.py`) — a prompts-only knob (`education` vs `general`) for testing whether the architecture is genuinely domain-independent.
- **Reference bindings & stated preferences** (`reference_bindings.py`, in `interactions.py`) — exact-match memory of a learner's recurring phrases and explicitly stated preferences, threaded into the prompt.
- **Exam preparation** (`exams.py`) — the third live app mode. An exam is a title, an optional date and syllabus units, built from a search, a PDF or link, or one of the learner's courses. The student takes a 5-question quiz per unit (retakes ask new questions) and timed mock tests across every unit; multiple choice is marked exactly and short answers by one grading call. Scores are derived from stored answers, never stored themselves, and exam prep is walled off from the personal learner model (invariant 13).
- **Sparks** (`sparks.py`) — learning credits: *pay for learning, never for confusion*. An answer costs 1 Spark, exploring a topic 2, building a course 5, creating an exam 3, a unit quiz 3, a mock test 8; a clarifying-options turn is always free. Passing a quiz (+2) or mock (+4), finishing a lesson (+3) and a 5-day study streak (+5) earn Sparks back. Balances refill every 12 hours up to a cap (Free: +10 up to 20; Plus: +50 up to 100; new learners start with 20). One append-only ledger (`spark_events`, invariant 16), balances derived, every write idempotent; `GET /api/learners/{id}/sparks` shows the balance, and an unaffordable action answers HTTP 402 / a `paywall` chat event. `VERSA_SPARKS=off` turns charging off. Plans come from billing (below).
- **Billing** (`billing.py`) — connects RevenueCat. The RevenueCat customer id is the learner id. Plus comes from RevenueCat's `versa_plus` entitlement (cached briefly; a RevenueCat outage keeps the last known plan). A bought Spark Pack adds its Sparks once per store transaction. An Exam Pass becomes Plus-level access until the day after the exam it was bought for (or the nearest upcoming exam, else 30 days). Purchases arrive via `POST /api/learners/{id}/billing/sync` (the app, right after buying) or RevenueCat's webhook `POST /api/billing/revenuecat/webhook` (needs a public URL), applied once whichever comes first; `GET /api/learners/{id}/billing` shows the plan. Append-only (invariant 17). Configure with `REVENUECAT_SECRET_KEY`, `REVENUECAT_PROJECT_ID`, `REVENUECAT_WEBHOOK_AUTH`; without the key billing is off and everyone is Free.
- **Accounts** (`accounts.py`) — sign-in with Google or email/password (Firebase; the server verifies the ID token and issues its own session token), invite-only sign-up (`versa invite create|list|revoke`, links at `/invite/CODE`), name-only sign-in for the two testers (on a laptop; a deployed server also wants `VERSA_DEV_LOGIN_CODE`), and a judge sign-in (any name plus `VERSA_JUDGE_CODE`, shown as "Judging Versa?" when the server has one). A guard on every route checks the token and that every learner, session, exam, topic, claim… a request names belongs to the signed-in learner. Append-only (invariant 18).
- **Sign-up profile** (`profiles.py`) — asked once after the first sign-in: name, age, what they're doing (school / university / working / other), country and state, board and class or institution, course and year, subjects, goals, and consent (a guardian's too under 18). One model call reads it into a level and a likely starting point; age that doesn't fit what they said is ignored rather than trusted. It shapes the ambiguity check, the options, the answer, Learn-a-topic and exam syllabus search — never "where this could go".
- **Pictures** (`images.py`) — a photo attached to a message is read once, on upload, by one model call; only that written reading goes further (invariant 21). A photo of a page, a syllabus or notes can also be what a course, an exam or a study room is built from, like a PDF: it is read in full and the reading is the material.
- **Revision notes** (`notes.py`) — notes for a chat, made only when the student asks, with a PDF to download or share.
- **Study with others** (`rooms/`) — group study chats with Versa as a member; a record of how a group learned, walled off from each person's own learner model (invariant 12).
- **The stage** (`stage.py`, `app/lib/stage/`) — a short scene acted out by the app's slime character while the answer is written, with a quick check (invariant 15).
- **Home feed** (`feed.py`, `chat_titles.py`) — what to pick up again and what to explore next. A chat to pick up again is shown by what it is about (a title and one sentence, one fast model call after its first answer, kept in `node_calls`) and by its own latest stage animation.
- **Learn a topic** (`topics.py`, `resources.py`) — the second live app mode. A keyword search, an uploaded PDF or a web link becomes a tree of branches the student can expand and tick; ticked branches become a course of chapters and lessons, each lesson a list of tasks ending in end-of-lesson questions. A lesson chat is an ordinary session run through the same loop, with the lesson's context added to the ambiguity check and the answer, and a background `JudgeLessonProgress` step that marks tasks done (progress is derived from those append-only events, never stored). What the system knows about the learner (thinking style, confirmed claims, stated preference, related past chats, sliders, other courses) shapes the branches, the lesson plans and the tutoring; what the student searches, expands, picks or skips, and how lessons go, is logged to `topic_signals` as episodic evidence. A plain course outline, not a learner model (invariant 4).

Every entry point builds the loop through the single assembly point `session_builder.build_session_loop`, so no two entry points can silently diverge in which stores they wire in. `versa chat` and `versa serve` (the API the app talks to) both build their loop through it.

---

## Built with

**Language:** Python 3.12+

**Server:** FastAPI + uvicorn (`src/versa/server.py`), started with `versa serve`: REST for sign-in/sessions and one WebSocket per chat that streams the answer as it is written.

**App:** Flutter (Dart), one codebase for web, Windows, Android and iOS, in `app/`. Live: Sandbox chat, Learn a topic, Exam prep, Study with others, the Thinking-style page and Plans (RevenueCat). Shipped as an Android APK and as the web app the server hosts.

**Database:** PostgreSQL 16 with the [`pgvector`](https://github.com/pgvector/pgvector) extension (Docker locally, Cloud SQL in production). Schema is managed by 94 ordered SQL migrations (001–095; plus two for rooms) in `src/versa/migrations/`, applied via `versa migrate` and tracked in a `schema_migrations` ledger.

**LLM / embeddings:** Google Gen AI SDK (`google-genai`), Gemini API. Every LLM-calling command accepts `--stub` to run against an in-memory stub client that needs no key and costs nothing.

**Tooling:** [`uv`](https://docs.astral.sh/uv/) for packaging and environments, `pytest` (+ `pytest-asyncio`), `ruff`.

**Deployment:** Google Cloud Run, backed by Cloud SQL and Secret Manager.

### Gemini model tiers

The tier→model mapping lives in `src/versa/model_config.py` and can be overridden by environment variable without a code change (Gemini preview model ids drift over time).

| Tier | Default model | Used by |
|---|---|---|
| `fast` | `gemini-3.6-flash` | ambiguity assessment, option generation, fact writing, semantic confirmation checks |
| `capable` | `gemini-3.5-flash` | (currently unused since the full reasoning path was removed) |
| `best` | `gemini-3.5-flash` | the final answer the student reads |
| `embedding` | `gemini-embedding-001` | the memory/retrieval vector layer |

Both `capable` and `best` are pinned to flash-class models because Pro-class models are quota-blocked on the free tier; they remain separate fields so either can be upgraded later via `GEMINI_MODEL_CAPABLE` / `GEMINI_MODEL_BEST` alone.

---

## Getting started (local)

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Docker Desktop (for the local Postgres + pgvector container)
- A Gemini API key ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)) — optional if you only run the stub

### Setup

1. Clone and enter the repo:

   ```bash
   git clone <repo-url>
   cd <repo-dir>
   ```

2. Start Postgres (with pgvector) on port 5434:

   ```bash
   docker compose up -d
   ```

3. Install dependencies:

   ```bash
   uv sync
   ```

4. Configure environment:

   ```bash
   cp .env.example .env
   # then edit .env and set:
   #   DATABASE_URL=postgresql://versa:versa@localhost:5434/versa
   #   GEMINI_API_KEY=<your key>        # only needed for non-stub runs
   ```

5. Apply migrations, then confirm:

   ```bash
   uv run versa migrate
   uv run versa migrate --status
   ```

6. Start a session (no key needed with `--stub`):

   ```bash
   uv run versa chat --learner <label> --stub
   ```

### Run the app

The quickest way, on Windows:

```powershell
.\scripts\start.ps1            # real Gemini (needs GEMINI_API_KEY in .env)
.\scripts\start.ps1 -Stub      # no key, no cost
```

It starts Docker + Postgres, applies migrations, builds the web app the first time, serves everything and opens **http://localhost:8000**. Sign in with any name (the same name next time = the same memory).

By hand:

```bash
docker compose up -d
uv run versa migrate
(cd app && flutter build web --release)     # once, and after changing app/lib
uv run versa serve                           # API + the built app at http://localhost:8000
```

Flutter is not on your PATH by default here: use `C:\src\flutter\bin\flutter`. Other targets: `flutter run -d windows`, or `flutter run -d edge --dart-define=VERSA_API=http://localhost:8000` for hot reload against a running `versa serve`.

Sign-in is on by default. On a laptop the two testers can sign in by name (`sooraj`, `adithya`); `VERSA_AUTH=off` gives an open server, allowed only on 127.0.0.1.

---

## CLI reference

Every command is available as `uv run versa <command>`. Commands that call an LLM accept `--stub` to run with no key and no cost.

| Command | What it does |
|---|---|
| `versa serve [--port 8000] [--stub]` | Run the API the app talks to, and serve the built web app (`app/build/web`) at `/`. |
| `versa invite create\|list\|revoke` | Invite codes for new accounts (Versa is invite-only); `create` prints each code with its `/invite/CODE` link. |
| `versa chat --learner <label\|uuid>` | Start an interactive disambiguation-mode session. A label resumes a matching learner or creates one; a UUID must already exist. Accepts `--stub`. |
| `versa migrate` | Apply pending SQL migrations in order, once each (idempotent). |
| `versa migrate --status` | Show applied/pending migrations without changing anything. |
| `versa migrate --baseline` | Stamp every migration as already-applied without running it — for a DB that already has the full schema but no ledger. |
| `versa consolidate-session <session-id>` | Run session-end consolidation for one completed session on demand. Accepts `--stub`. |
| `versa seed-demo-fixture` | (Re)apply two hand-authored, opposite-portrait demo learners plus a fixed question set. Idempotent; no LLM/embedding call. |
| `versa compare-portraits [--question]` | Three-column wrong-portrait control: the same question run against the concrete portrait, the abstract portrait, and a zero-claims control, side by side. Accepts `--stub` (under a stub all three columns are identical by construction). |
| `versa review-claims --learner <label\|uuid>` | Read-only listing of one learner's claims: statement, test, status, confidence, evidence count, topic spread, and the interactions behind each evidence row. |
| `versa observations --learner <label\|uuid>` | Read-only: one learner's observation ledger by lens, what counts for less, the pick guesser's record and their way in. |
| `versa discovered-moves --learner <label\|uuid>` | Read-only: the moves one learner asked for that no card type covers, grouped among their own readings. |
| `versa score-predictions [--exclude-contaminated]` | Read-only operator report on the claim-confidence formula: observed-vs-predicted hit rate per confidence bucket plus an overall Brier score, pooled across all learners. Nothing it computes feeds back into any learner's model. |

---

## Testing

The automated suite runs entirely against a stub LLM/embedding client with **no external calls and no API key required**. It wipes and rebuilds the database named by `VERSA_TEST_DATABASE_URL` (a separate `versa_test` database, so your dev data is safe). **Set it**: if it is unset the suite falls back to a default that can be your dev database.

```bash
docker compose exec postgres createdb -U versa versa_test     # once
# .env:  VERSA_TEST_DATABASE_URL=postgresql://versa:versa@localhost:5434/versa_test
uv run pytest
(cd app && flutter test)                                       # the app's tests
```

Two tools measure the real thing (a few cents of Gemini calls each):

```bash
uv run python scripts/measure_turn.py            # where a real turn's time goes, call by call
uv run python scripts/eval_assess_thinking.py    # does limiting model thinking change when options appear?
```

Try a live session against the real API:

```bash
uv run versa chat --learner test-user
```

Or run the same thing with no API calls or cost:

```bash
uv run versa chat --learner test-user --stub
```

| Credential | Required for | Where to get it |
|---|---|---|
| `DATABASE_URL` | All local runs and the test suite | Provided by `docker compose up` |
| `GEMINI_API_KEY` | Any real (non-`--stub`) session | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |

---

## Design invariants

This codebase is a research artifact whose whole premise is auditing how beliefs evolved over time, so its stores are governed by a set of hard invariants documented in `CLAUDE.md`. In short:

- **Every reasoning store is append-only.** No store deletes rows or overwrites evidence — the hypothesis, world-model-revision, branch, option, disambiguation, memory, evidence, turn-diagnostics, and tier-change stores all model "retirement" as a status change (archived, superseded, rejected, retired), never a delete. An AST-based check enforces the no-`delete`/no-`DELETE` rule.
- **Every node call is persisted.** Every invocation of a node's primary method is recorded to `node_calls` with its inputs, outputs, and timestamp, by routing all node calls through `SessionLoop._call_node`. If a node ran and its inputs+outputs aren't on disk, the audit trail is broken.
- **Only a learner's own data shapes what Versa concludes about them** (decided 2026-10-01). No pattern, guess, retrieved history or prompt draws on another learner's chats; the one pooled report (`score-predictions`) is for operators and feeds nothing back.
- **The concept graph is retired.** It was removed on measured evidence (it lost to a plain-LLM baseline at 20–40× the cost); nothing in the current architecture reads one, and it should not be restored to satisfy a task that assumes it exists.

See `CLAUDE.md` for the full, numbered set with the rationale behind each one.

---

## Project layout

```
src/versa/
  cli.py                 # `versa` command entry point (argparse)
  server.py              # HTTP + WebSocket API over SessionLoop (`versa serve`)
  streaming.py           # per-turn answer streaming plumbing
  session_builder.py     # the one place a fully-wired SessionLoop is built
  loop.py                # SessionLoop — the turn orchestrator
  disambiguate.py        # the live three-call disambiguation mode
  memory.py              # learner_facts + thinking_style detection
  interactions.py        # append-only interaction log + recorder
  retrieval.py           # deterministic 3-stage retrieval (no LLM)
  claims.py              # durable preference-claim layer
  directions.py          # "where this could go": card library, hands, misses
  choice.py              # reading a pick against the hand it came from
  style_patterns.py      # the thinking style (own data only, against chance)
  observations.py        # the observation ledger + session readings
  pick_prediction.py     # the guess before each set, and the answer's way in
  accounts.py, profiles.py  # sign-in, invites, judge/tester sign-in; sign-up profile
  images.py, notes.py    # attached pictures; revision notes
  stage.py, feed.py      # the slime's stage; the Home feed
  rooms/                 # Study with others
  domain_config.py       # education/general prompts-only switch
  model_config.py        # Gemini tier→model mapping
  llm.py, embeddings.py  # Gemini + stub clients
  topics.py              # Learn a topic: explore -> course -> lessons, progress
  exams.py               # Exam prep: syllabus units, unit quizzes, timed mocks
  sparks.py              # Sparks: learning credits -- costs, rewards, refills (append-only ledger)
  billing.py             # RevenueCat: plans, Spark packs, Exam Passes (sync + webhook)
  resources.py           # PDF / web-link reading (SSRF-guarded) for Learn a topic
  chat_titles.py         # what a chat is about, for the Home feed's cards
  migrations/*.sql        # 94 ordered schema migrations, 001-095 (+ rooms_001, rooms_002)
tests/                    # pytest suite (stub-backed, no external calls)
app/                      # the Flutter app (web, Windows, Android, iOS)
scripts/                  # start.ps1 (one-command launcher), deploy_gcp.ps1, build_apk.ps1, measure_turn.py, eval_assess_thinking.py, bench_retrieval.py
docs/verification-runs/   # saved output from past staged/grounding verification runs
archive/instrument_layer/ # parked capability + instrument code (not imported; see its README)
docker-compose.yml        # local Postgres 16 + pgvector (port from VERSA_DB_PORT, default 5434)
Dockerfile                # the server image: API + the web app (Cloud Run), and the Flutter SDK stage the APK build reuses
docs/IDEAS.md             # parked ideas, deferred work, known issues, decisions log
```

---

## License

[MIT](LICENSE). You may use, modify and distribute this code, including commercially, as long as the copyright and license notice stay with it.

---

## Deployment

Deployed on Google Cloud Run, backed by Cloud SQL (Postgres + pgvector) and Secret Manager for credentials; the Android app talks to it. `scripts/deploy_gcp.ps1` does the whole thing (database, secrets, Cloud Build image with the web app, migrations as a job, the service) and `scripts/build_apk.ps1` builds the APK in Docker. Step by step, including Firebase setup, invites and showing the phone on a laptop screen: [docs/DEPLOY.md](docs/DEPLOY.md).

Getting the app: the web app shows a "Get the Versa app" popup on every visit (not on iPhone/iPad), whose button goes to `/download/android` — a redirect to the current APK (`VERSA_ANDROID_URL`, a public Cloud Storage file). Publishing a new version is a version bump in `app/pubspec.yaml`, `scripts/build_apk.ps1`, and one upload over that file; no server redeploy. The Firebase API keys are restricted to the app's package + signing key and to the site's own domains.
