-- versa: a built course (topics.py). The student's selected branches become
-- chapters, their selected sub-branches become lessons, and every lesson gets
-- 3-5 tasks ending in a 'check' (the end-of-lesson questions).
--
-- A plain course OUTLINE, not a learner model (CLAUDE.md invariant 4): no
-- prerequisite edges, no mastery estimate, nothing inferred about the
-- student is stored here. Written once when the course is built and never
-- changed afterwards -- append-only like every store in this codebase.
-- Progress is not a column: it is derived from lesson_task_events (074).

CREATE TABLE topics (
    id              UUID PRIMARY KEY,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    exploration_id  UUID NULL REFERENCES topic_explorations (id),
    title           TEXT NOT NULL,
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('search', 'pdf', 'link')),
    -- which of the learner's known traits shaped the lesson plan
    personalization_used  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topics_learner ON topics (learner_id, created_at DESC);

CREATE TABLE topic_chapters (
    id        UUID PRIMARY KEY,
    topic_id  UUID NOT NULL REFERENCES topics (id),
    node_id   UUID NULL REFERENCES topic_nodes (id),
    position  INT NOT NULL,
    title     TEXT NOT NULL,
    summary   TEXT NOT NULL
);

CREATE INDEX idx_topic_chapters_topic ON topic_chapters (topic_id, position);

CREATE TABLE topic_lessons (
    id          UUID PRIMARY KEY,
    chapter_id  UUID NOT NULL REFERENCES topic_chapters (id),
    node_id     UUID NULL REFERENCES topic_nodes (id),
    position    INT NOT NULL,
    title       TEXT NOT NULL,
    objective   TEXT NOT NULL
);

CREATE INDEX idx_topic_lessons_chapter ON topic_lessons (chapter_id, position);

CREATE TABLE lesson_tasks (
    id           UUID PRIMARY KEY,
    lesson_id    UUID NOT NULL REFERENCES topic_lessons (id),
    position     INT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('learn', 'practice', 'apply', 'check')),
    description  TEXT NOT NULL
);

CREATE INDEX idx_lesson_tasks_lesson ON lesson_tasks (lesson_id, position);
