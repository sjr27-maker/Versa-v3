-- versa rooms (EXPERIMENTAL, branch experiment/rooms): study together in a
-- group chat where Versa is one more member. See src/versa/rooms/__init__.py.
--
-- Named `rooms_NNN_*` rather than the next core number so it can never
-- collide with a migration added on main, and always sorts after every
-- numbered core migration (letters sort after digits). It references no core
-- table: a room member is a name inside one room, not a `learners` row, and
-- nothing here is read by the personal memory layer (walled off, the same
-- wall as CLAUDE.md invariants 6/8).
--
-- Append-only (CLAUDE.md invariant 12): every table here is only ever
-- INSERTed into. State that changes -- a task being done, an option set
-- being answered or replaced -- is derived from event rows, never stored.

CREATE TABLE rooms (
    id                UUID PRIMARY KEY,
    code              TEXT NOT NULL,
    code_key          TEXT NOT NULL UNIQUE,          -- lower(code): codes are case-insensitive
    title             TEXT NOT NULL,
    source_kind       TEXT NOT NULL CHECK (source_kind IN ('search', 'pdf', 'link')),
    query             TEXT NOT NULL,                 -- what was searched, or the resource's title
    resource_url      TEXT NULL,
    resource_filename TEXT NULL,
    resource_excerpt  TEXT NULL,                     -- the start of an uploaded/linked resource's text
    outline           JSONB NOT NULL,                -- [{title, summary}] the topic's parts
    created_by        TEXT NOT NULL,                 -- the creator's display name
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE room_members (
    id          UUID PRIMARY KEY,
    room_id     UUID NOT NULL REFERENCES rooms (id),
    name        TEXT NOT NULL,
    name_key    TEXT NOT NULL,                       -- lower(name): rejoining with the same name is the same member
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (room_id, name_key)
);

-- The group chat. `seq` orders a room's messages (1, 2, 3, ...).
--   sender:  'member' (a person), 'versa', or 'system' (joins, room created)
--   kind:    'text' plain chat, 'content' a teaching explanation, 'question'
--            Versa asking something (may carry an option set), 'task' a task
--            handed to someone, 'progress' a task marked done, 'pick' a
--            person's click on an option, 'event' a system line
--   to_member_id + private: Versa talks to everyone (NULL) or one person;
--            a private message is only ever shown to that person.
CREATE TABLE room_messages (
    id            UUID PRIMARY KEY,
    room_id       UUID NOT NULL REFERENCES rooms (id),
    seq           INT NOT NULL,
    sender        TEXT NOT NULL CHECK (sender IN ('member', 'versa', 'system')),
    member_id     UUID NULL REFERENCES room_members (id),
    kind          TEXT NOT NULL CHECK (kind IN ('text', 'content', 'question', 'task', 'progress', 'pick', 'event')),
    text          TEXT NOT NULL,
    to_member_id  UUID NULL REFERENCES room_members (id),
    private       BOOLEAN NOT NULL DEFAULT FALSE,
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (room_id, seq),
    CHECK (NOT private OR to_member_id IS NOT NULL)
);

CREATE INDEX idx_room_messages_room ON room_messages (room_id, seq);

-- A task for one person, written once.
CREATE TABLE room_tasks (
    id           UUID PRIMARY KEY,
    room_id      UUID NOT NULL REFERENCES rooms (id),
    member_id    UUID NOT NULL REFERENCES room_members (id),
    kind         TEXT NOT NULL CHECK (kind IN ('learn', 'practice', 'apply', 'check', 'discuss')),
    description  TEXT NOT NULL,
    message_id   UUID NULL REFERENCES room_messages (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_room_tasks_room ON room_tasks (room_id, created_at);

-- Whether a task is done is the latest event for it, never a stored flag.
CREATE TABLE room_task_events (
    id          UUID PRIMARY KEY,
    task_id     UUID NOT NULL REFERENCES room_tasks (id),
    event       TEXT NOT NULL CHECK (event IN ('completed', 'reopened')),
    evidence    TEXT NOT NULL DEFAULT '',
    message_id  UUID NULL REFERENCES room_messages (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_room_task_events_task ON room_task_events (task_id, created_at);

-- Clickable options Versa offers one person (member_id) or everyone (NULL).
-- A set is open for a person until they pick from it or a newer set for the
-- same audience replaces it -- both derived, nothing is updated.
CREATE TABLE room_option_sets (
    id          UUID PRIMARY KEY,
    room_id     UUID NOT NULL REFERENCES rooms (id),
    member_id   UUID NULL REFERENCES room_members (id),
    prompt      TEXT NOT NULL,
    message_id  UUID NULL REFERENCES room_messages (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_room_option_sets_room ON room_option_sets (room_id, created_at);

CREATE TABLE room_options (
    id        UUID PRIMARY KEY,
    set_id    UUID NOT NULL REFERENCES room_option_sets (id),
    position  INT NOT NULL,
    text      TEXT NOT NULL,
    UNIQUE (set_id, position)
);

CREATE TABLE room_option_picks (
    id          UUID PRIMARY KEY,
    set_id      UUID NOT NULL REFERENCES room_option_sets (id),
    option_id   UUID NOT NULL REFERENCES room_options (id),
    member_id   UUID NOT NULL REFERENCES room_members (id),
    message_id  UUID NULL REFERENCES room_messages (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (set_id, member_id)
);

-- Every model call a room makes, with its full input and output (the
-- CLAUDE.md invariant 2 payload). A room is not a `sessions` row, so --
-- as feed_generations and topic_generations already do -- it keeps its own
-- table, keyed by room and by the message seq that triggered the call.
CREATE TABLE room_node_calls (
    id           UUID PRIMARY KEY,
    room_id      UUID NULL REFERENCES rooms (id),   -- NULL: the outline call made before the room row exists
    seq          INT NOT NULL,
    node_name    TEXT NOT NULL,
    input_json   JSONB NOT NULL,
    output_json  JSONB NULL,
    error        TEXT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_room_node_calls_room ON room_node_calls (room_id, seq);
