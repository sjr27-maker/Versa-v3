-- versa: every settled move of a session's length/depth/breadth sliders
-- (knob_events.py KnobEventStore). `sessions` only holds the CURRENT levels
-- (overwritten in place, migrations 063/071/077), so until now every move
-- but the last one in a session was lost -- yet a move is the learner
-- setting their own range (docs/THINKING_STYLE.md, the Range lens), and a
-- learner-initiated one, so it is clean evidence.
--
-- One row per PATCH that changed anything (the app debounces a drag into
-- one PATCH, and a pad move that changes depth and breadth together is one
-- decision, so one row). `turn_count` is how many turns the session had
-- when the slider moved: 0 = set before the first message.
--
-- Append-only (CLAUDE.md invariant 19): written once, never edited.

CREATE TABLE knob_events (
    id                 UUID PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES sessions (id),
    turn_count         INT NOT NULL CHECK (turn_count >= 0),
    from_answer_length SMALLINT NOT NULL CHECK (from_answer_length BETWEEN 0 AND 100),
    from_depth         SMALLINT NOT NULL CHECK (from_depth BETWEEN 0 AND 100),
    from_breadth       SMALLINT NOT NULL CHECK (from_breadth BETWEEN 0 AND 100),
    to_answer_length   SMALLINT NOT NULL CHECK (to_answer_length BETWEEN 0 AND 100),
    to_depth           SMALLINT NOT NULL CHECK (to_depth BETWEEN 0 AND 100),
    to_breadth         SMALLINT NOT NULL CHECK (to_breadth BETWEEN 0 AND 100),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_knob_events_session ON knob_events (session_id, created_at);
