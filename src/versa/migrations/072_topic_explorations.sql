-- versa: "Learn a topic" discovery (topics.py, resources.py). Before a
-- course exists, the student explores a topic as a tree: a keyword search,
-- a PDF or a web link produces first-level branches, and any branch can be
-- expanded into more. Everything here is append-only (no UPDATE, no
-- DELETE): expanding a node appends children, a second "more" expansion
-- appends further children, and nothing is ever rewritten.
--
-- topic_generations is the audit record for every LLM call this mode
-- makes outside a chat (branch generation, resource outlining, lesson
-- planning). node_calls.session_id is NOT NULL REFERENCES sessions and
-- none of these calls belongs to a session, so -- same reasoning and
-- shape as feed_generations (070) -- this table carries the node name,
-- the full input and the parsed output instead (CLAUDE.md invariant 2).

CREATE TABLE topic_resources (
    id          UUID PRIMARY KEY,
    learner_id  UUID NOT NULL REFERENCES learners (id),
    kind        TEXT NOT NULL CHECK (kind IN ('pdf', 'link')),
    title       TEXT NOT NULL,
    url         TEXT NULL,
    filename    TEXT NULL,
    text        TEXT NOT NULL,
    headings    JSONB NOT NULL DEFAULT '[]'::jsonb,
    char_count  INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE topic_explorations (
    id           UUID PRIMARY KEY,
    learner_id   UUID NOT NULL REFERENCES learners (id),
    query        TEXT NOT NULL,
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('search', 'pdf', 'link')),
    resource_id  UUID NULL REFERENCES topic_resources (id),
    -- which of the learner's known traits shaped the first branches
    -- (topics.build_learner_profile's `used`), shown as "Shaped by: ..."
    personalization_used  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topic_explorations_learner ON topic_explorations (learner_id, created_at DESC);

CREATE TABLE topic_generations (
    id              UUID PRIMARY KEY,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    exploration_id  UUID NULL REFERENCES topic_explorations (id),
    node_name       TEXT NOT NULL,
    input_json      JSONB NOT NULL,
    output_json     JSONB NULL,
    error           TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topic_generations_learner ON topic_generations (learner_id, created_at DESC);

CREATE TABLE topic_nodes (
    id              UUID PRIMARY KEY,
    exploration_id  UUID NOT NULL REFERENCES topic_explorations (id),
    parent_id       UUID NULL REFERENCES topic_nodes (id),
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    depth           INT NOT NULL,
    position        INT NOT NULL,
    generation_id   UUID NULL REFERENCES topic_generations (id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topic_nodes_exploration ON topic_nodes (exploration_id, depth, position);
CREATE INDEX idx_topic_nodes_parent ON topic_nodes (parent_id, position);
