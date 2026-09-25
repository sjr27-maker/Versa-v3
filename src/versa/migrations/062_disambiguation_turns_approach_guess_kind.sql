-- versa: widens disambiguation_turns.kind's CHECK constraint (migration
-- 058) to admit a third value, 'approach_guess' -- Guess Mode's second
-- narrowing round (sessions.guess_mode, migration 061; disambiguate.
-- GuessApproach; see IDEAS.md's "convert each query to options" entry).
-- Exactly the "third kind is ever needed" case migration 058's own
-- comment named as the reason this is a CHECK constraint, not free TEXT.
--
-- Postgres has no ALTER CHECK -- drop and recreate under the same name
-- convention (Postgres auto-names it disambiguation_turns_kind_check;
-- named explicitly here so a future fourth kind can find and drop it by
-- name too, not guess the auto-generated one).

ALTER TABLE disambiguation_turns DROP CONSTRAINT disambiguation_turns_kind_check;

ALTER TABLE disambiguation_turns
    ADD CONSTRAINT disambiguation_turns_kind_check
    CHECK (kind IN ('ambiguity', 'reason_confirmation', 'approach_guess'));
