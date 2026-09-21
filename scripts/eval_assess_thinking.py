"""Does limiting model "thinking" change WHEN the ambiguity check fires?

`AssessAndBranch` decides whether a message needs clarifying options. Cutting
thinking makes it faster (see model_config.py) -- this checks it does not also
make it trigger-happy (options on clear messages) or blind (no options on
genuinely ambiguous ones). Same messages, same real fast-tier model, each judged
REPEATS times per thinking setting; prints how often each was judged ambiguous.

    uv run python scripts/eval_assess_thinking.py
    uv run python scripts/eval_assess_thinking.py --settings default minimal 128 256 low --repeats 5

Real API calls (fast tier only; ~14 messages x repeats x settings), no database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from google import genai

from versa.disambiguate import AssessAndBranch
from versa.llm import GeminiLLMClient
from versa.model_config import ModelTierConfig

_BINARY = (
    "turn 0 student: Explain how binary search works.\n"
    "turn 0 tutor: Binary search repeatedly halves a sorted list: compare the middle "
    "element to the target, then keep only the half that could still contain it."
)
_CHAIN = (
    "turn 0 student: How does the chain rule work?\n"
    "turn 0 tutor: The chain rule differentiates a composite function: multiply the "
    "derivative of the outer function, evaluated at the inner one, by the derivative "
    "of the inner function."
)

# A topic detour: "it" can only mean binary search, but the recent history also
# holds derivatives. Reasoning resolves it; "minimal" thinking asks instead
# (measured 5/6 wrong; a 128-256 token budget 0/6 wrong).
_MIXED = (
    _BINARY + "\n"
    "turn 1 student: can you help me with derivatives?\n"
    "turn 1 tutor: Which of these did you mean?\n"
    "turn 2 student: Let's look at core differentiation rules.\n"
    "turn 2 tutor: The power, product and chain rules are the core tools for differentiating."
)

# (message, recent_history, expected)  expected: "clear" | "ambiguous"
CASES: list[tuple[str, str, str]] = [
    ("Explain how binary search works.", "", "clear"),
    ("What is the derivative of x^3 with respect to x?", "", "clear"),
    ("Write a Python function that reverses a string.", "", "clear"),
    ("Why is the sky blue?", "", "clear"),
    ("What is the capital of France?", "", "clear"),
    ("Explain how quicksort works and what its average time complexity is.", "", "clear"),
    ("Why is it faster than checking every element?", _BINARY, "clear"),
    ("Can you show me one more example of that?", _CHAIN, "clear"),
    ("Why is it faster than checking every element?", _MIXED, "clear"),
    ("can you help me with derivatives?", "", "ambiguous"),
    ("tell me about python", "", "ambiguous"),
    ("how do I make it faster?", "", "ambiguous"),
    ("explain the bank", "", "ambiguous"),
    ("what's a good approach for this?", "", "ambiguous"),
    ("help me with my project", "", "ambiguous"),
]


async def _judge(node: AssessAndBranch, message: str, history: str, sem) -> bool:
    async with sem:
        result = await node.run(message=message, recent_history=history)
    return result.needs_branches


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", nargs="+", default=["default", "minimal", "256"])
    parser.add_argument("--repeats", type=int, default=4)
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or api_key == "your-key-here":
        sys.exit("GEMINI_API_KEY is not set in .env")
    model = ModelTierConfig.from_env().fast
    client = genai.Client(api_key=api_key)
    sem = asyncio.Semaphore(6)

    results: dict[str, list[int]] = {}
    for setting in args.settings:
        node = AssessAndBranch(GeminiLLMClient(client, model, thinking=setting))
        counts = await asyncio.gather(*(
            asyncio.gather(*(_judge(node, m, h, sem) for _ in range(args.repeats)))
            for m, h, _ in CASES
        ))
        results[setting] = [sum(c) for c in counts]

    print(f"model={model}  repeats={args.repeats}  (cell = times judged AMBIGUOUS out of {args.repeats})\n")
    head = f"{'message':60s} {'expected':9s} " + " ".join(f"{s:>8s}" for s in args.settings)
    print(head)
    print("-" * len(head))
    for i, (message, history, expected) in enumerate(CASES):
        tag = message if not history else f"{message} [+context]"
        print(f"{tag[:60]:60s} {expected:9s} " + " ".join(f"{results[s][i]:>8d}" for s in args.settings))
    print()
    for s in args.settings:
        fp = sum(results[s][i] for i, c in enumerate(CASES) if c[2] == "clear")
        fn = sum(args.repeats - results[s][i] for i, c in enumerate(CASES) if c[2] == "ambiguous")
        n_clear = sum(1 for c in CASES if c[2] == "clear") * args.repeats
        n_amb = sum(1 for c in CASES if c[2] == "ambiguous") * args.repeats
        print(f"{s:>8s}: options on CLEAR messages {fp}/{n_clear}   |   missed AMBIGUOUS {fn}/{n_amb}")


if __name__ == "__main__":
    asyncio.run(main())
