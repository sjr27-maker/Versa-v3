"""Pure vector math for `interactions.py`'s per-turn similarity
computation (and a few other per-learner comparisons). The cross-learner
clustering that also used it (population_patterns.py) was removed on
2026-10-01, with its running_mean -- no learner's data feeds another's.
The per-turn computation (which
replaced topic clustering after two rounds of tuning plus a systematic
replay confirmed a fixed-threshold running centroid has no working
constant for this text length -- see topics_removal in interactions.py
and TopicConfig's git history for the full failure record).

No state, no I/O -- this used to live in topics.py alongside the class
that owned the failed mechanism; extracted here so no caller has to
import from, or depend on, a module named for a concept that no longer
exists.
"""

from __future__ import annotations


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
