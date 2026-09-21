"""Per-turn answer streaming — how `FinalAnswer`'s text reaches a client
while it is still being generated.

A client (the CLI, a server pushing to an app) passes an async callback
as `SessionLoop.handle_turn(..., on_delta=...)`. The loop installs it in
`delta_sink` for the duration of that one turn; `FinalAnswer.run` reads it
and, if set, streams the model's output through it piece by piece instead
of waiting for the whole completion.

A ContextVar (not a node kwarg, not an attribute on the shared node
object) on purpose:
  * node kwargs are persisted verbatim to `node_calls.input_json`
    (CLAUDE.md invariant 2) -- a callback has no business there;
  * one `SessionLoop` (and so one `FinalAnswer`) serves many concurrent
    sessions, so per-turn state must not live on it;
  * asyncio tasks inherit the ContextVar of whoever created them, so it
    follows the turn without being threaded through every signature.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

DeltaSink = Callable[[str], Awaitable[None]]

delta_sink: ContextVar[DeltaSink | None] = ContextVar("versa_delta_sink", default=None)
