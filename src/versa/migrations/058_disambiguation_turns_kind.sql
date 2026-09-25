-- versa: disambiguation_turns gains `kind` -- distinguishes an
-- ordinary ambiguity turn (AssessAndBranch ran, branches are LLM-
-- generated readings) from a reason-confirmation turn (a REASON-based
-- memory match -- migration 056 -- offered directly to the student as
-- a yes/no, never through AssessAndBranch or DisambiguationOptions).
-- See IDEAS.md's "ask for confirmation directly" entry.
--
-- A CHECK constraint, not left as free TEXT like migration 057's
-- memory_match_via: that column is purely observational (diagnostics
-- a UI reads), but `kind` DRIVES CONTROL FLOW in loop.py's click
-- resolution -- a bad value here would silently misroute a click, so
-- the closed, enforced set is worth the (rare) extra migration if a
-- third kind is ever needed.
--
-- DEFAULT 'ambiguity': every row written before this column existed,
-- and every row the existing AssessAndBranch/DisambiguationOptions
-- path writes, is this kind by construction.

ALTER TABLE disambiguation_turns
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'ambiguity'
    CHECK (kind IN ('ambiguity', 'reason_confirmation'));
