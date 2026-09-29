-- versa: a bigger space of directions (directions.py lib-v2, docs/THINKING_STYLE.md).
--
-- Until now every answer offered the same six cards. Now there is a library
-- of 16 card types in four families, and each answer's cards are written for
-- a POOL drawn at random from it (two per family); the learner is shown a
-- HAND dealt from the pool (one card per family). A pick is read against the
-- cards actually shown (choice.py), so random hands stay comparable.
--
--   direction_pools     every card one answer's generation wrote -- what
--                       COULD have been shown, kept even if never dealt
--   direction_sets      + pool_id / deal_index: which pool a hand came from,
--                       and whether it was the first hand or a later deal
--   direction_cards     slot: any library type, or 'wild' (a card written
--                       with no type, tagged afterwards to the nearest type:
--                       tagged_as) or 'path' (two steps in one card:
--                       path_slots, e.g. 'example>use')
--   direction_events    kind 'more': the learner asked for other directions
--                       -- nothing in this hand matched; a new hand follows
--   direction_predictions  predicted_slot: any library type
--
-- Append-only (CLAUDE.md invariants 14 and 20): new rows and new columns
-- only; old six-card sets read exactly as before.

CREATE TABLE direction_pools (
    id               UUID PRIMARY KEY,
    session_id       UUID NOT NULL REFERENCES sessions (id),
    turn_index       INT NOT NULL,
    library_version  TEXT NOT NULL,
    cards            JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_direction_pools_session ON direction_pools (session_id, created_at);

ALTER TABLE direction_sets
    ADD COLUMN pool_id UUID NULL REFERENCES direction_pools (id),
    ADD COLUMN deal_index INT NOT NULL DEFAULT 0;

ALTER TABLE direction_cards DROP CONSTRAINT direction_cards_slot_check;
ALTER TABLE direction_cards
    ADD CONSTRAINT direction_cards_slot_check CHECK (slot IN (
        'intuition', 'example', 'why', 'use', 'deeper', 'next',
        'try_it', 'real_data', 'prove_it', 'mistake', 'visualise', 'story',
        'summary', 'compare', 'connect', 'debate', 'wild', 'path')),
    ADD COLUMN tagged_as TEXT NULL,
    ADD COLUMN path_slots TEXT NULL;

ALTER TABLE direction_events DROP CONSTRAINT direction_events_kind_check;
ALTER TABLE direction_events
    ADD CONSTRAINT direction_events_kind_check CHECK (kind IN ('picked', 'passed', 'more'));

ALTER TABLE direction_predictions DROP CONSTRAINT direction_predictions_predicted_slot_check;
ALTER TABLE direction_predictions
    ADD CONSTRAINT direction_predictions_predicted_slot_check CHECK (predicted_slot IN (
        'intuition', 'example', 'why', 'use', 'deeper', 'next',
        'try_it', 'real_data', 'prove_it', 'mistake', 'visualise', 'story',
        'summary', 'compare', 'connect', 'debate', 'wild', 'path'));
