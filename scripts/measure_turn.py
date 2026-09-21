"""Where does a real turn's time go? Runs a short scripted conversation through
the real pipeline (real Gemini models + embeddings, the dev database) and
prints every LLM / embedding call with its start offset and duration, plus
each turn's wall time.

    uv run python scripts/measure_turn.py                       # current settings
    GEMINI_THINKING_FAST=default GEMINI_THINKING_BEST=default \\
        uv run python scripts/measure_turn.py                   # thinking left at the model default

Costs a few cents of API calls and writes a throwaway learner + session to the
dev database (DATABASE_URL). It reads `SessionLoop` internals (`_transcript`,
`_disambiguation`) on purpose -- it is a
measuring tool, not a client.

Scenario (one session, one fresh learner):
    0  a clear question                    -> direct answer
    1  an ambiguous message                -> clickable options
    2  click the first option              -> answer
    3  a clear follow-up                   -> direct answer
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

from dotenv import load_dotenv

from versa.db import create_pool
from versa.embeddings import build_embedding_client
from versa.learner import LearnerStore
from versa.llm import ModelTierClients, build_tier_clients
from versa.model_config import ModelTierConfig
from versa.session_builder import build_session_loop

CALLS: list[tuple[str, float, float]] = []  # (label, start_offset, duration)
_T0 = 0.0


def _now() -> float:
    return time.monotonic() - _T0


class _TimedLLM:
    def __init__(self, inner, tier: str) -> None:
        self._inner = inner
        self._tier = tier

    @property
    def retry_count(self) -> int:
        return getattr(self._inner, "retry_count", 0)

    async def complete(self, prompt: str) -> str:
        start = _now()
        try:
            return await self._inner.complete(prompt)
        finally:
            label = prompt.split("\n", 1)[0][:34]
            CALLS.append((f"llm/{self._tier:<4} {label}", start, _now() - start))


    async def stream(self, prompt: str):
        # Must exist: FinalAnswer only streams if the client it holds has a
        # `stream` method, so a timing wrapper without one silently turns
        # streaming off and makes "first words" equal "answer complete".
        start = _now()
        first = None
        try:
            async for piece in self._inner.stream(prompt):
                if first is None:
                    first = _now() - start
                yield piece
        finally:
            label = prompt.split("\n", 1)[0][:34]
            note =f"  (first piece after {first:.2f}s)" if first is not None else ""
            CALLS.append((f"llm/{self._tier:<4} {label}{note}", start, _now() - start))


class _TimedEmbeddings:
    def __init__(self, inner) -> None:
        self._inner = inner

    async def embed(self, text: str, *, task_type: str | None = None):
        start = _now()
        try:
            return await self._inner.embed(text, task_type=task_type)
        finally:
            CALLS.append((f"embed     {task_type or 'untyped'}", start, _now() - start))


async def _turn(
    loop, session_id, idx: int, kind: str, text: str, option_id=None, *, stream: bool = True
) -> tuple[float, float]:
    """Returns (first_output, wall): when the user first sees anything (the
    first streamed word, or the options / whole reply when nothing streams)
    and when the turn returned."""
    global _T0
    CALLS.clear()
    _T0 = time.monotonic()
    first_delta: list[float] = []

    async def sink(_piece: str) -> None:
        if not first_delta:
            first_delta.append(time.monotonic() - _T0)

    message = await loop.handle_turn(
        session_id, idx, text, selected_option_id=option_id,
        on_delta=sink if stream else None, defer_tail=stream,
    )
    wall = time.monotonic() - _T0
    first = first_delta[0] if first_delta else wall
    calls = list(CALLS)
    while loop._background_tasks:  # tasks can spawn tasks: drain until quiet
        await loop.wait_for_background_tasks()
    bg_end = time.monotonic() - _T0
    shown = "first words" if first_delta else "shown (nothing streamed)"
    print(f"\nturn {idx} [{kind}]  {shown} at +{first:4.1f}s | returned +{wall:4.1f}s | "
          f"background done +{bg_end:.1f}s")
    print(f"   said: {message[:90]!r}")
    for label, start, dur in calls:
        print(f"   +{start:5.2f}s  {dur:5.2f}s  {label}")
    late = [c for c in CALLS if c not in calls]
    for label, start, dur in late:
        print(f"   +{start:5.2f}s  {dur:5.2f}s  {label}   (background)")
    return first, wall


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default=f"latency-{time.strftime('%H%M%S')}")
    parser.add_argument("--inline", action="store_true",
                        help="old behavior: no streaming, post-response writes before returning")
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "your-key-here":
        sys.exit("GEMINI_API_KEY is not set in .env")

    real = build_tier_clients(api_key)
    tiers = ModelTierClients(
        fast=_TimedLLM(real.fast, "fast"),
        capable=_TimedLLM(real.capable, "cap"),
        best=_TimedLLM(real.best, "best"),
    )
    embeddings = _TimedEmbeddings(build_embedding_client(api_key))
    pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        learner = await LearnerStore(pool).create(label=args.label)
        loop = build_session_loop(pool, tiers, embeddings)
        session_id = await loop._transcript.create_session(
            learner.id, ablation_config=loop._ablation
        )
        print(f"learner {args.label}  session {session_id}")
        cfg = ModelTierConfig.from_env()
        print(f"thinking: fast={cfg.fast_thinking} best={cfg.best_thinking}")

        stream = not args.inline
        print(f"mode: {'streaming + deferred writes' if stream else 'inline (no streaming)'}")
        firsts: list[float] = []
        walls: list[float] = []

        async def run(idx, kind, text, option_id=None):
            first, wall = await _turn(loop, session_id, idx, kind, text, option_id, stream=stream)
            firsts.append(first)
            walls.append(wall)

        await run(0, "clear question", "Explain how binary search works.")
        await run(1, "ambiguous", "can you help me with derivatives?")
        options = await loop.pending_options(session_id)
        if options:
            print(f"   options offered: {len(options)}")
            await run(2, "click option", options[0].text, options[0].id)
        else:
            print("   (no options were offered -- skipping the click turn)")
        await run(3, "clear follow-up", "Why is it faster than checking every element?")
        print(f"\nuser sees first output at: {', '.join(f'{f:.1f}s' for f in firsts)}  "
              f"| median {statistics.median(firsts):.1f}s")
        print(f"turn returned at:          {', '.join(f'{x:.1f}s' for x in walls)}  "
              f"| median {statistics.median(walls):.1f}s")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
