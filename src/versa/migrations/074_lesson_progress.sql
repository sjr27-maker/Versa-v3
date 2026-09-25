-- versa: progress through a course, and the personalization signals this
-- mode produces (topics.py).
--
-- lesson_task_events: every judged task completion (JudgeLessonProgress,
-- recorded to node_calls as usual). Progress is DERIVED from the latest
-- event per task, never stored as a mutable percentage, so how a lesson was
-- completed stays on record. Append-only: a task that needs redoing gets a
-- new 'reopened' row, nothing is edited or removed.
--
-- topic_signals: episodic personalization signals (what was searched,
-- expanded, selected or skipped, lesson open order, turns per task,
-- end-of-lesson check results, drifting off-chapter). Same wall as CLAUDE.md
-- invariants 6/8: these are raw episodes, never written into claims or
-- thinking styles directly. Append-only.
--
-- sessions.lesson_id: a lesson chat is an ordinary session (app_mode
-- 'topic') tied to its lesson, set once at creation.

CREATE TABLE lesson_task_events (
    id           UUID PRIMARY KEY,
    lesson_id    UUID NOT NULL REFERENCES topic_lessons (id),
    task_id      UUID NOT NULL REFERENCES lesson_tasks (id),
    session_id   UUID NULL REFERENCES sessions (id),
    turn_index   INT NULL,
    event        TEXT NOT NULL CHECK (event IN ('completed', 'reopened')),
    evidence     TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_lesson_task_events_lesson ON lesson_task_events (lesson_id, created_at);

CREATE TABLE topic_signals (
    id              UUID PRIMARY KEY,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    topic_id        UUID NULL REFERENCES topics (id),
    exploration_id  UUID NULL REFERENCES topic_explorations (id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'search', 'resource', 'expand', 'selection', 'lesson_open',
                        'task_completed', 'check_result', 'chapter_drift')),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topic_signals_learner ON topic_signals (learner_id, created_at);

ALTER TABLE sessions ADD COLUMN lesson_id UUID NULL REFERENCES topic_lessons (id);

CREATE INDEX idx_sessions_lesson ON sessions (lesson_id, created_at DESC) WHERE lesson_id IS NOT NULL;
