# Versa

**A tutor that stops guessing.**

Instead of quietly assuming what a student meant, Versa recognizes uncertainty, asks one sharp clarifying question with real, clickable options — not vague "what do you prefer" quizzing — and remembers how every past ambiguity was resolved. Over time it starts recognizing a student's own thinking style, not from a survey, but from accumulated evidence.

The Python package and CLI are both named `versa`. This README describes the codebase as it currently stands.

---

## What it does

When a student's message is genuinely ambiguous, Versa generates a few distinct interpretations, turns them into natural clickable options, and lets the student resolve it in one tap — no guessing, no wrong assumptions baked into the answer. If the message is clear, it answers directly.

Every resolution is written down in plain English as a searchable memory: what was unclear, and what was chosen. Before answering anything new, Versa searches that memory first — if it has seen this kind of ambiguity from this student before, it already knows the answer and skips the question.

Across many sessions, it looks for a repeating *order* in how a student reaches understanding — concrete before abstract, or the reverse — and only names that as a real trait once it has been confirmed independently, many times, never from a single guess.

Underneath the live tutoring loop, Versa also records a rich, append-only audit trail of everything it observed and every belief it formed. The audit trail is the point of the project: nothing is trusted just because it sounds right, and every claim about a learner traces back to stored, readable evidence.

---

## How we got here

The project began as a much larger architecture — a full learner model with hypotheses, a concept graph, a planner scoring six value terms, and a branching prediction tree — then tested it head-to-head against a single plain-LLM call on identical conversations. The comparison showed the heavy machinery wasn't earning its cost (it lost to a plain baseline at 20–40× the cost), so the concept graph and the full reasoning path were removed.

What runs today is the lean core that survived that measurement:

- a three-call **disambiguation flow** (`ReasoningMode.DISAMBIGUATE`, also called *minimal_branch* mode) that replaces the old branch-tree/planner machinery outright;
- a **memory layer** over pgvector that recalls how past ambiguity was resolved and detects a thinking style across sessions, evidence-gated so it never asserts a pattern on a hunch;
- an **interaction/retrieval pipeline** plus a **claim layer** that builds a durable, evidence-backed model of a learner's traits — currently recorded and wired into the answer prompt, while the predictive scoring it produces feeds nothing back into what the student sees yet;

---

## Architecture

The live tutoring turn:

```mermaid
flowchart TD
    A[Student message] --> M[EmbedAndSearchFacts + ConfirmFactMatch]
    M -->|known ambiguity, branching skipped| E[FinalAnswer]
    M -->|not resolved by memory| B[AssessAndBranch]
    B -->|unambiguous| E
    B -->|ambiguous| C[GenerateOptions]
    C --> D{Student clicks<br/>or types}
    D -->|click| E
    D -->|types past options| B
    E --> F[Response shown to student]
    F --> G[WriteLearnerFact]
    G --> H[(learner_facts<br/>pgvector)]
    H -.->|semantic pre-check,<br/>every turn| M
    F --> I[Session end]
    I --> J[Consolidate: label the<br/>session's order-of-moves]
    J --> K[(thinking_style_candidates)]
    K -.->|after many confirmed independent sessions:<br/>shapes which options are offered| B
```

A single reasoning mode is live: `minimal_branch` (`ReasoningMode.DISAMBIGUATE`). At most three LLM calls fully resolve one exchange — an ambiguity assessment, one clickable option per interpretation, and a final answer once the student has resolved which reading they meant. A plain-LLM `BASELINE` mode also exists as the measurement control.

### The layers, and what each is for

- **Disambiguation flow** (`disambiguate.py`) — the live reasoning mode: assess ambiguity, offer 2–4 distinct readings as options, answer once one is chosen. Every assessment is persisted whether or not it decides to branch.
- **Memory layer** (`memory.py`) — `learner_facts` (within-session recall that can skip branching entirely when a past fact resolves the current message) and `thinking_style_candidates` (a cross-session order-of-reasoning pattern; once confirmed by enough independent sessions it is fed to `AssessAndBranch` and `DisambiguationOptions` — it shapes which options are offered, not the answer text itself). Session-end consolidation runs from the app too (`POST /api/sessions/{id}/end`, plus a sweep of older unconsolidated chats when a new one starts).
- **Interaction / retrieval pipeline** (`interactions.py`, `retrieval.py`, `history_block.py`) — an append-only log of every exchange, with deterministic three-stage retrieval over a learner's own history and population-level patterns. Feeds the history block into the final-answer prompt; its LLM-based selection *predictions* are recorded but do not yet influence the student's response.
- **Claim layer** (`claims.py`) — a durable, cross-session model of a learner's standing preferences, extracted **only** from episodes that were actually surprising (high prediction error), each claim carrying a falsifiable `test` and promoted only past an evidence/topic-spread gate.
- **Parked: capability & instrument layers** (`archive/instrument_layer/`) — a separate capability-claim store plus purpose-built interactions (`locate`, `predict`) whose event streams were interpreted by hand-written deterministic contracts. Removed from the live architecture; nothing imports it and `pytest` never collects it. Restore steps are in that directory's README. Their migrations and tables remain in the schema, dormant.
- **Population patterns** (`population_patterns.py`) — clusters interaction abstracts across many learners, surfacing only patterns backed by ≥20 distinct learners with no single learner dominating.
- **Domain switch** (`domain_config.py`) — a prompts-only knob (`education` vs `general`) for testing whether the architecture is genuinely domain-independent.
- **Reference bindings & stated preferences** (`reference_bindings.py`, in `interactions.py`) — exact-match memory of a learner's recurring phrases and explicitly stated preferences, threaded into the prompt.
- **Exam preparation** (`exams.py`) — the third live app mode. An exam is a title, an optional date and syllabus units, built from a search, a PDF or link, or one of the learner's courses. The student takes a 5-question quiz per unit (retakes ask new questions) and timed mock tests across every unit; multiple choice is marked exactly and short answers by one grading call. Scores are derived from stored answers, never stored themselves, and exam prep is walled off from the personal learner model (invariant 13).
- **Learn a topic** (`topics.py`, `resources.py`) — the second live app mode. A keyword search, an uploaded PDF or a web link becomes a tree of branches the student can expand and tick; ticked branches become a course of chapters and lessons, each lesson a list of tasks ending in end-of-lesson questions. A lesson chat is an ordinary session run through the same loop, with the lesson's context added to the ambiguity check and the answer, and a background `JudgeLessonProgress` step that marks tasks done (progress is derived from those append-only events, never stored). What the system knows about the learner (thinking style, confirmed claims, stated preference, related past chats, sliders, other courses) shapes the branches, the lesson plans and the tutoring; what the student searches, expands, picks or skips, and how lessons go, is logged to `topic_signals` as episodic evidence. A plain course outline, not a learner model (invariant 4).

Every entry point builds the loop through the single assembly point `session_builder.build_session_loop`, so no two entry points can silently diverge in which stores they wire in. `versa chat` and `versa serve` (the API the app talks to) both build their loop through it.

---

## Built with

**Language:** Python 3.12+

**Server:** FastAPI + uvicorn (`src/versa/server.py`), started with `versa serve`: REST for sign-in/sessions and one WebSocket per chat that streams the answer as it is written.

**App:** Flutter (Dart), one codebase for web, Windows, Android and iOS, in `app/`. Today: a working Sandbox chat plus labelled placeholders for everything else.

**Database:** PostgreSQL 16 with the [`pgvector`](https://github.com/pgvector/pgvector) extension (Docker locally, Cloud SQL in production). Schema is managed by 73 ordered SQL migrations in `src/versa/migrations/`, applied via `versa migrate` and tracked in a `schema_migrations` ledger.

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

The server has **no authentication** and binds to 127.0.0.1: local development only.

---

## CLI reference

Every command is available as `uv run versa <command>`. Commands that call an LLM accept `--stub` to run with no key and no cost.

| Command | What it does |
|---|---|
| `versa serve [--port 8000] [--stub]` | Run the API the app talks to, and serve the built web app (`app/build/web`) at `/`. |
| `versa chat --learner <label\|uuid>` | Start an interactive disambiguation-mode session. A label resumes a matching learner or creates one; a UUID must already exist. Accepts `--stub`. |
| `versa migrate` | Apply pending SQL migrations in order, once each (idempotent). |
| `versa migrate --status` | Show applied/pending migrations without changing anything. |
| `versa migrate --baseline` | Stamp every migration as already-applied without running it — for a DB that already has the full schema but no ledger. |
| `versa consolidate-session <session-id>` | Run the cross-session thinking-style detection step for one completed session on demand. Accepts `--stub`. |
| `versa aggregate-patterns` | Cluster every learner's latest interaction abstracts and write readable population patterns (≥20 distinct learners, ≤25% single-learner share). |
| `versa seed-demo-fixture` | (Re)apply two hand-authored, opposite-portrait demo learners plus a fixed question set. Idempotent; no LLM/embedding call. |
| `versa compare-portraits [--question]` | Three-column wrong-portrait control: the same question run against the concrete portrait, the abstract portrait, and a zero-claims control, side by side. Accepts `--stub` (under a stub all three columns are identical by construction). |
| `versa review-claims --learner <label\|uuid>` | Read-only listing of one learner's claims: statement, test, status, confidence, evidence count, topic spread, and the interactions behind each evidence row. |
| `versa score-predictions [--exclude-contaminated]` | Read-only reliability-diagram check: observed-vs-predicted hit rate per confidence bucket plus an overall Brier score, pooled across all learners. |

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
  population_patterns.py # cross-learner clustering
  domain_config.py       # education/general prompts-only switch
  model_config.py        # Gemini tier→model mapping
  llm.py, embeddings.py  # Gemini + stub clients
  topics.py              # Learn a topic: explore -> course -> lessons, progress
  exams.py               # Exam prep: syllabus units, unit quizzes, timed mocks
  resources.py           # PDF / web-link reading (SSRF-guarded) for Learn a topic
  migrations/*.sql        # 73 ordered schema migrations
tests/                    # pytest suite (stub-backed, no external calls)
app/                      # the Flutter app (web, Windows, Android, iOS)
scripts/                  # start.ps1 (one-command launcher), measure_turn.py, eval_assess_thinking.py, bench_retrieval.py
docs/verification-runs/   # saved output from past staged/grounding verification runs
archive/instrument_layer/ # parked capability + instrument code (not imported; see its README)
docker-compose.yml        # local Postgres 16 + pgvector (port from VERSA_DB_PORT, default 5434)
Dockerfile                # container image (uv sync; no entrypoint yet)
docs/IDEAS.md             # parked ideas, deferred work, known issues, decisions log
```

---

## License

[MIT](LICENSE). You may use, modify and distribute this code, including commercially, as long as the copyright and license notice stay with it.

---

## Deployment

Deployed on Google Cloud Run, backed by Cloud SQL (Postgres + pgvector) and Secret Manager for credentials. The container (`Dockerfile`) runs `uv sync --frozen` and currently has no entrypoint on purpose: `versa serve` has no authentication yet, so it must not be exposed publicly. Add auth first, then set `CMD` (`versa serve --host 0.0.0.0 --port $PORT`) and build the web app into the image.