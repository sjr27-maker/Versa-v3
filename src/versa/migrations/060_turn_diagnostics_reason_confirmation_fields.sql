-- versa: turn_diagnostics gains two visibility fields for the
-- reason-confirmation flow (disambiguation_turns.kind, migration 058;
-- learner_fact_type.reason_confirmed, migration 059) -- same
-- discipline as every other memory/history-block/reference-binding
-- field already here (migrations 031/035/039/057): a UI or a later
-- analysis reads what happened, never re-derives it.
--
-- `reason_confirmation_offered`: True on the turn a reason-based
-- match was shown to the student as a direct yes/no (never on a
-- turn where memory matched via situation/resolution instead, or
-- didn't match at all).
-- `reason_confirmed_by_student`: NULL until answered (matches
-- matched_fact_id's own "NULL covers both no-match and disabled"
-- precedent) -- true/false on the turn the student actually clicked
-- Yes or No.

ALTER TABLE turn_diagnostics
    ADD COLUMN reason_confirmation_offered BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN reason_confirmed_by_student BOOLEAN;
