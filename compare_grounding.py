"""The grounded-vs-ungrounded comparison for time-sensitive grounding.

Same standard the previous architecture was cut by: run identical
questions through both arms, record both answers verbatim, and report
cost. This script decides nothing — it writes down what happened so a
human can read it and judge. A null result is a valid outcome.

    uv run python compare_grounding.py --calibration        # no key needed
    uv run python compare_grounding.py --set original
    uv run python compare_grounding.py --set fresh
    uv run python compare_grounding.py --set both

PROTOCOL (run 2, after the excerpt-selection fix)

* ORIGINAL_FIVE is the set run 1 used. Re-running it after a fix is a
  second attempt at questions the fix was debugged against, and must be
  read as such — it can only confirm the fix did something, never that
  the feature generalizes.
* FRESH_FIVE is the set that actually counts. It was written down
  before the run and never used to debug anything. If the fix wins on
  ORIGINAL_FIVE and loses on FRESH_FIVE, that is fitting, and both
  numbers get reported rather than averaged together.
* REPEATS: every question runs twice in each arm. Parallel's excerpts
  were observed to differ across identical calls, so a verdict that
  flips between two identical runs means a 5-question comparison cannot
  decide this at all and the honest answer is a bigger sample.
* A fresh learner per individual run. The memory layer writes a
  learner_fact after every resolved turn and semantically pre-checks
  against it on later turns; a shared learner would let one run's fact
  leak into the next and contaminate both the repeats and the arms.
* A turn whose FinalAnswer fails (Gemini 5xx) is retried, not scored.
  Run 1 lost a whole control arm to a 504.

If this feature does not win, delete it — this file included — rather
than leaving it behind a flag.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv

from probe.audit import NodeCallStore, TranscriptStore
from probe.db import create_pool
from probe.diagnostics import TurnDiagnosticsStore
from probe.disambiguate import DisambiguationStore
from probe.embeddings import build_embedding_client
from probe.grounding import GroundingConfig, detect_time_sensitivity
from probe.learner import LearnerStore
from probe.llm import build_tier_clients
from probe.loop import SessionLoop
from probe.memory import LearnerFactStore, ThinkingStyleStore
from probe.websearch import build_web_search_client

REPEATS = 2
MAX_RETRIES = 3

# ── Run 1's questions. The fix was diagnosed against Q1, so this set is
#    contaminated by hindsight and cannot be the deciding evidence. ────
ORIGINAL_FIVE = [
    "What is the latest stable version of Python, and what changed in it?",
    "Who is currently the CEO of Intel?",
    "What is the current price of a Claude API call for the best model?",
    "What was announced at the most recent Google I/O?",
    # Known heuristic miss (pinned as a passing test): no lexical
    # recency marker, so grounding never fires. Reported, never scored.
    "Has the EU AI Act's general-purpose model obligations taken effect yet?",
]

# ── The set that counts. Written before the run; no result seen. ──────
FRESH_FIVE = [
    "What is the latest version of the Rust compiler, and what did it add?",
    "Who is currently the Secretary-General of NATO?",
    "What was announced at the most recent Apple event?",
    "What is the current price of gold per ounce?",
    "Which country won the most recent FIFA World Cup?",
]

STABLE_CONTROL = [
    "What is a derivative, intuitively?",
    "Explain the chain rule with a worked example.",
    "Why does integration by parts work?",
    "What is current in an electrical circuit?",
    "How do I minimise a cost function with gradient descent?",
]

_TEACH_FAILURE = "the tutor failed to respond this turn"


# ────────────────────────── calibration ───────────────────────────────


def run_calibration() -> None:
    print("=" * 72)
    print("CALIBRATION — local heuristic only, zero API calls, zero cost")
    print("=" * 72)

    def report(label: str, questions: list[str]) -> int:
        fired = 0
        print(f"\n{label}")
        for q in questions:
            s = detect_time_sensitivity(q)
            fired += bool(s.is_time_sensitive)
            mark = "FIRE" if s.is_time_sensitive else "  . "
            marker = f"  <- {s.matched_marker!r}" if s.matched_marker else ""
            print(f"  [{mark}] {q}{marker}")
        print(f"  => {fired}/{len(questions)} fired")
        return fired

    report("ORIGINAL_FIVE:", ORIGINAL_FIVE)
    report("FRESH_FIVE:", FRESH_FIVE)
    misfires = report("STABLE CONTROL (must NOT fire):", STABLE_CONTROL)
    if misfires:
        print(
            "\nFINDING: the check fired on an ordinary tutoring question. "
            "Report it; do not tune it away quietly."
        )


# ─────────────────────────── the real run ─────────────────────────────


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        print(f"error: {name} not set (check .env)", file=sys.stderr)
        sys.exit(2)
    return value


def _build_loop(pool, tiers, embeddings, search_client, *, enabled: bool) -> SessionLoop:
    return SessionLoop(
        transcript=TranscriptStore(pool),
        node_calls=NodeCallStore(pool),
        llm=tiers.fast,
        model_tier_clients=tiers,
        diagnostics_store=TurnDiagnosticsStore(pool),
        disambiguation_store=DisambiguationStore(pool),
        learner_fact_store=LearnerFactStore(pool),
        thinking_style_store=ThinkingStyleStore(pool),
        embedding_client=embeddings,
        web_search_client=search_client if enabled else None,
        grounding_config=GroundingConfig(enabled=enabled),
    )


async def _one_run(ctx, loop, question: str) -> dict:
    """One question, one fresh learner, one fresh session, one turn.

    Retries a FinalAnswer transport failure rather than scoring it —
    a Gemini 504 is not evidence about grounding.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        learner = await ctx["learners"].create(label=f"cmp-{uuid4().hex[:8]}")
        session_id = await ctx["transcript"].create_session(learner.id)
        start = time.monotonic()
        try:
            answer = await loop.handle_turn(session_id, 0, question)
        except Exception as exc:  # noqa: BLE001 - a harness must not die here
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        elapsed_ms = (time.monotonic() - start) * 1000
        diags = await ctx["diagnostics"].list_for_session(session_id)
        diag = diags[0] if diags else None
        counts = diag.node_call_counts if diag else {}

        grounding_call = await ctx["node_calls"].get_call_for_turn(
            session_id, 0, "GroundTimeSensitive"
        )
        grounding = grounding_call.output_json if grounding_call else None

        if answer.startswith(_TEACH_FAILURE):
            last_error = "FinalAnswer failed (see diagnostics)"
            print(f"      retry {attempt}/{MAX_RETRIES}: {last_error}", flush=True)
            continue

        return {
            "ok": True,
            "answer": answer,
            "elapsed_ms": elapsed_ms,
            "call_counts": counts,
            "total_calls": sum(counts.values()),
            "attempts": attempt,
            "fired": bool(grounding and grounding.get("time_sensitive")),
            "marker": (grounding or {}).get("matched_marker"),
            "sources": [e["url"] for e in (grounding or {}).get("evidence", [])],
            "warnings": diag.warnings if diag else [],
        }
    return {
        "ok": False,
        "answer": f"[NO VALID RUN after {MAX_RETRIES} attempts: {last_error}]",
        "elapsed_ms": 0.0,
        "call_counts": {},
        "total_calls": 0,
        "attempts": MAX_RETRIES,
        "fired": False,
        "marker": None,
        "sources": [],
        "warnings": [],
    }


async def run_set(ctx, on, off, label: str, questions: list[str], report) -> list[dict]:
    rows = []
    for i, question in enumerate(questions, 1):
        print(f"\n[{label}] Q{i}: {question}", flush=True)
        entry = {"question": question, "on": [], "off": []}
        for arm_name, loop in (("on", on), ("off", off)):
            for rep in range(1, REPEATS + 1):
                print(f"   {arm_name} run {rep}...", end="", flush=True)
                result = await _one_run(ctx, loop, question)
                print(
                    f" {result['elapsed_ms']:.0f}ms "
                    f"{'fired' if result['fired'] else 'no-fire'} "
                    f"{'OK' if result['ok'] else 'FAILED'}",
                    flush=True,
                )
                entry[arm_name].append(result)
        rows.append(entry)
        _write_question(report, label, i, entry)
    return rows


def _write_question(report, label: str, i: int, entry: dict) -> None:
    report.write(f"\n\n## [{label}] Q{i}: {entry['question']}\n")
    for arm in ("on", "off"):
        for rep, r in enumerate(entry[arm], 1):
            report.write(
                f"\n### grounding {arm.upper()} — run {rep}\n\n"
                f"- ok: `{r['ok']}` | attempts: {r['attempts']} | "
                f"{r['elapsed_ms']:.0f} ms | {r['total_calls']} calls "
                f"`{r['call_counts']}`\n"
                f"- fired: `{r['fired']}` | marker: `{r['marker']}`\n"
                f"- sources: {r['sources'] or '(none)'}\n"
            )
            if r["warnings"]:
                report.write(f"- warnings: {r['warnings']}\n")
            report.write(f"\n> {r['answer']}\n")
    report.flush()


def _summarize(report, label: str, rows: list[dict]) -> None:
    report.write(f"\n\n# COST SUMMARY — {label}\n\n")
    report.write("| Q | fired | on ms (r1/r2) | off ms (r1/r2) | on calls | off calls |\n")
    report.write("|---|---|---|---|---|---|\n")
    deltas = []
    for i, e in enumerate(rows, 1):
        on1, on2 = e["on"]
        off1, off2 = e["off"]
        fired = on1["fired"] and on2["fired"]
        if fired and on1["ok"] and off1["ok"]:
            deltas.append(
                statistics.mean([on1["elapsed_ms"], on2["elapsed_ms"]])
                - statistics.mean([off1["elapsed_ms"], off2["elapsed_ms"]])
            )
        report.write(
            f"| {i} | {fired} | {on1['elapsed_ms']:.0f}/{on2['elapsed_ms']:.0f} "
            f"| {off1['elapsed_ms']:.0f}/{off2['elapsed_ms']:.0f} "
            f"| {on1['total_calls']}/{on2['total_calls']} "
            f"| {off1['total_calls']}/{off2['total_calls']} |\n"
        )
    if deltas:
        report.write(
            f"\nmean latency delta on firing questions: "
            f"{statistics.mean(deltas):+.0f} ms "
            f"(NOTE: end-to-end, includes Gemini variance/retries — "
            f"see the isolated search-latency measurement for the clean number)\n"
        )

    report.write(f"\n\n# EXCERPT STABILITY — {label}\n\n")
    report.write(
        "Did the two identical grounded runs retrieve the same sources? "
        "If not, a single-run verdict is noise.\n\n"
    )
    report.write("| Q | run1 sources | run2 sources | identical |\n|---|---|---|---|\n")
    for i, e in enumerate(rows, 1):
        s1, s2 = e["on"][0]["sources"], e["on"][1]["sources"]
        report.write(
            f"| {i} | {s1 or '(none)'} | {s2 or '(none)'} | {s1 == s2} |\n"
        )
    report.flush()


async def run_full(which: str) -> None:
    load_dotenv()
    gemini_key = _require("GEMINI_API_KEY")
    parallel_key = _require("PARALLEL_API_KEY")
    database_url = _require("DATABASE_URL")

    tiers = build_tier_clients(gemini_key)
    embeddings = build_embedding_client(gemini_key)
    search_client = build_web_search_client(parallel_key)

    sets = []
    if which in ("original", "both"):
        sets.append(("ORIGINAL (post-fix 2nd attempt)", ORIGINAL_FIVE))
    if which in ("fresh", "both"):
        sets.append(("FRESH (never debugged against)", FRESH_FIVE))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = f"grounding_run2_{which}_{stamp}.md"

    pool = await create_pool(database_url, min_size=2, max_size=6)
    try:
        ctx = {
            "transcript": TranscriptStore(pool),
            "diagnostics": TurnDiagnosticsStore(pool),
            "node_calls": NodeCallStore(pool),
            "learners": LearnerStore(pool),
        }
        on = _build_loop(pool, tiers, embeddings, search_client, enabled=True)
        off = _build_loop(pool, tiers, embeddings, search_client, enabled=False)

        with open(path, "w", encoding="utf-8") as report:
            report.write(f"# Grounding comparison, run 2 (post excerpt-selection fix)\n")
            report.write(f"\nUTC {stamp} | repeats per arm: {REPEATS}\n")
            for label, questions in sets:
                report.write(f"\n\n# ===== {label} =====\n")
                rows = await run_set(ctx, on, off, label, questions, report)
                _summarize(report, label, rows)
        print(f"\n\nfull verbatim report written to: {path}")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", action="store_true")
    parser.add_argument(
        "--set", dest="which", choices=("original", "fresh", "both"), default="both"
    )
    args = parser.parse_args()
    if args.calibration:
        run_calibration()
        return
    asyncio.run(run_full(args.which))


if __name__ == "__main__":
    main()
