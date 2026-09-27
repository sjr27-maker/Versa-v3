-- versa: "where this could go" -- after every answer, a standard set of
-- directions the learner could take next (directions.py, IDEAS.md "A space
-- of possibilities", 2026-09-27). What they pick, in what order, and what
-- they pass over is thinking-style evidence, so all of it is kept.
--
-- Append-only (CLAUDE.md invariant 14). A set is written once with its
-- cards; what happened to it is events. Nothing is flagged in place.

-- One set, offered after one answered turn. The slider levels it was pitched
-- at are kept, so a pick can be read against the window it came from.
CREATE TABLE direction_sets (
    id             UUID PRIMARY KEY,
    session_id     UUID NOT NULL REFERENCES sessions (id),
    -- the answered turn this set follows
    turn_index     INT NOT NULL,
    depth_level    SMALLINT NOT NULL CHECK (depth_level BETWEEN 0 AND 100),
    breadth_level  SMALLINT NOT NULL CHECK (breadth_level BETWEEN 0 AND 100),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_direction_sets_session ON direction_sets (session_id, created_at DESC);

-- The cards, one per slot of the standard skeleton. `position` is where it
-- was SHOWN (shuffled per set), so a pick can be told apart from simply
-- tapping whatever came first.
CREATE TABLE direction_cards (
    id        UUID PRIMARY KEY,
    set_id    UUID NOT NULL REFERENCES direction_sets (id),
    slot      TEXT NOT NULL CHECK (slot IN ('intuition', 'example', 'why', 'use', 'deeper', 'next')),
    position  INT NOT NULL,
    text      TEXT NOT NULL,
    UNIQUE (set_id, slot),
    UNIQUE (set_id, position)
);

-- What the learner did with a set: picked a card, or passed it by typing
-- their own next message. At most one event per set (the first thing they
-- did settles it). `elapsed_ms` is from the set being offered to the event;
-- `next_turn_index` is the turn it led to.
CREATE TABLE direction_events (
    id               UUID PRIMARY KEY,
    set_id           UUID NOT NULL UNIQUE REFERENCES direction_sets (id),
    kind             TEXT NOT NULL CHECK (kind IN ('picked', 'passed')),
    card_id          UUID NULL REFERENCES direction_cards (id),
    next_turn_index  INT NOT NULL,
    elapsed_ms       INT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((kind = 'picked') = (card_id IS NOT NULL))
);
