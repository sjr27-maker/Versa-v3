-- versa: a study plan for an exam (exams.py): the days from the plan's start
-- to the exam, each with a few items -- revise a unit, quiz a unit, quiz
-- whichever unit is weakest right now, or sit a mock test.
--
-- Append-only (CLAUDE.md invariant 13). Re-planning writes a NEW plan (the
-- latest one is the plan); items are written once. Whether an item is done
-- is never a column: quizzes and mocks are matched to handed-in sittings,
-- and a person's own ticks are events, the latest one per item winning.

CREATE TABLE exam_plans (
    id          UUID PRIMARY KEY,
    exam_id     UUID NOT NULL REFERENCES exams (id),
    start_date  DATE NOT NULL,
    -- the exam day itself; the plan's last working day is the day before
    end_date    DATE NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date > start_date)
);

CREATE INDEX idx_exam_plans_exam ON exam_plans (exam_id, created_at DESC);

CREATE TABLE exam_plan_items (
    id        UUID PRIMARY KEY,
    plan_id   UUID NOT NULL REFERENCES exam_plans (id),
    day       DATE NOT NULL,
    position  INT NOT NULL,
    kind      TEXT NOT NULL CHECK (kind IN ('revise', 'quiz', 'weakest', 'mock')),
    unit_id   UUID NULL REFERENCES exam_units (id),
    CHECK ((kind IN ('revise', 'quiz')) = (unit_id IS NOT NULL))
);

CREATE INDEX idx_exam_plan_items_plan ON exam_plan_items (plan_id, day, position);

-- A person ticking an item done, or unticking it. Latest per item wins.
CREATE TABLE exam_plan_item_events (
    id          UUID PRIMARY KEY,
    item_id     UUID NOT NULL REFERENCES exam_plan_items (id),
    done        BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    seq         BIGSERIAL
);

CREATE INDEX idx_exam_plan_item_events_item ON exam_plan_item_events (item_id, seq);
