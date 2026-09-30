"""Reading a pick against the cards that were actually offered.

With a fixed six-card skeleton, counting picks was enough: every card was
always there. With a random hand drawn from the card library
(directions.py lib-v2), a card can only be picked when it was dealt, so a
raw count mostly measures how often it happened to be shown. Everything that
learns from picks -- the guess (pick_prediction.py), the answer's way in, and
the thinking-style patterns (style_patterns.py) -- reads them through this
module instead:

  win_stats / win_rate   how often a card was taken WHEN IT WAS OFFERED,
                         against the chance of taking it at random from
                         that hand (1 / hand size)
  luce_fit               a preference weight for every card type from
                         choices among different subsets (the Plackett-Luce
                         / conditional-logit model, fitted by Hunter's MM
                         iteration), pulled toward a prior -- an even
                         start -- until there is evidence (only the
                         learner's own choices are ever fitted: no
                         learner's data feeds another's, 2026-10-01)
  among                  those weights turned into the chance of each card
                         in a given hand

Pure functions, no I/O. A random subset is as comparable as a fixed six once
picks are read this way -- and cleaner, since a preference is no longer tied
to always being shown next to the same five cards.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Shown:
    """One choice: the cards offered, the one taken, and how much it counts
    as evidence (layer 2's weighing)."""

    offered: tuple[str, ...]
    chosen: str
    weight: float = 1.0


def win_stats(choices: Iterable[Shown], slot: str) -> tuple[float, float, float]:
    """(weighted times taken, weighted times offered, average chance of
    taking it at random from the hands it was in)."""
    wins = offered = chance = 0.0
    for c in choices:
        if slot in c.offered:
            offered += c.weight
            chance += c.weight / len(c.offered)
            if c.chosen == slot:
                wins += c.weight
    return wins, offered, (chance / offered if offered else 0.0)


def win_rate(wins: float, offered: float, prior_rate: float, prior_strength: float = 1.0) -> float:
    """Times taken over times offered, pulled toward `prior_rate` (chance)
    by `prior_strength` pseudo-offers."""
    return (wins + prior_strength * prior_rate) / (offered + prior_strength)


def luce_fit(choices: Sequence[Shown], universe: Sequence[str], prior: dict[str, float] | None = None,
             prior_strength: float = 1.0, iterations: int = 200) -> dict[str, float]:
    """Preference weights (summing to 1 over `universe`) from choices among
    subsets. The prior acts as `prior_strength` pseudo-choices made from the
    whole universe in proportion to `prior` (uniform when not given), so a
    card never seen on offer keeps the prior's weight and nothing is ever
    certain on little evidence."""
    n = len(universe)
    base = {s: (prior or {}).get(s, 1.0 / n) for s in universe}
    total = sum(base.values()) or 1.0
    base = {s: v / total for s, v in base.items()}
    w = dict(base)
    wins = {s: prior_strength * base[s] for s in universe}
    for c in choices:
        if c.chosen in wins:
            wins[c.chosen] += c.weight
    for _ in range(iterations):
        denom = {s: prior_strength / sum(w.values()) for s in universe}
        for c in choices:
            shown = [s for s in c.offered if s in w]
            if not shown:
                continue
            z = sum(w[s] for s in shown)
            for s in shown:
                denom[s] += c.weight / z
        new = {s: (wins[s] / denom[s]) if denom[s] > 0 else w[s] for s in universe}
        z = sum(new.values()) or 1.0
        new = {s: v / z for s, v in new.items()}
        if max(abs(new[s] - w[s]) for s in universe) < 1e-9:
            w = new
            break
        w = new
    return w


def among(weights: dict[str, float], offered: Sequence[str]) -> dict[str, float]:
    """The chance of each offered card being taken, under `weights`."""
    z = sum(weights.get(s, 0.0) for s in offered)
    if z <= 0:
        return {s: 1.0 / len(offered) for s in offered}
    return {s: weights.get(s, 0.0) / z for s in offered}
