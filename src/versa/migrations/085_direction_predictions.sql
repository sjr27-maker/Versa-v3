-- versa: Versa's guess at which "where this could go" card the learner will
-- take (pick_prediction.py) -- the proof clock for the core claim
-- (docs/THINKING_STYLE.md): if it is learning how someone thinks, these
-- guesses should get better over time.
--
-- Written once per set, BEFORE the cards are sent, so a guess can never see
-- the pick. `scores` is the blended chance per slot; `contributions` is the
-- exact breakdown (everyone else's picks, this learner's picks, their order
-- of approach or how they open a chat, and what was set aside) -- what
-- contributed to the guess is kept, not just the guess. Whether it was right
-- is never stored: it is derived from the set's direction_events row.
--
-- Append-only (CLAUDE.md invariant 20).

CREATE TABLE direction_predictions (
    id                 UUID PRIMARY KEY,
    set_id             UUID NOT NULL UNIQUE REFERENCES direction_sets (id),
    learner_id         UUID NOT NULL REFERENCES learners (id),
    predicted_slot     TEXT NOT NULL
        CHECK (predicted_slot IN ('intuition', 'example', 'why', 'use', 'deeper', 'next')),
    scores             JSONB NOT NULL,
    contributions      JSONB NOT NULL,
    -- how many of this learner's picks the guess had to go on
    evidence_count     INT NOT NULL,
    predictor_version  TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_direction_predictions_learner ON direction_predictions (learner_id, created_at DESC);
