-- versa: turn_diagnostics gains a visibility field for the reason-
-- retrieval experiment (memory.py / migration 056) -- same discipline
-- as migration 031/035/039's memory/history-block/reference-binding
-- fields: which retrieval path actually produced the winning
-- learner_facts candidate this turn, so a UI or a later analysis can
-- read it directly instead of re-deriving it.
--
-- Nullable, not an enum: NULL covers both "no memory match at all"
-- and "memory is disabled for this session" -- collapsing those into
-- one NULL state (rather than inventing a third label) matches how
-- `matched_fact_id` already handles the same two cases.
-- 'situation_resolution' | 'reason' are written as plain TEXT, not a
-- new Postgres ENUM type: this is an experiment expected to gain
-- values (e.g. "both agreed") as the comparison IDEAS.md calls for
-- gets built out, and TEXT lets that happen without a migration each
-- time, same reasoning turn_diagnostics.warnings already uses TEXT[]
-- over an enum array.

ALTER TABLE turn_diagnostics ADD COLUMN memory_match_via TEXT;
