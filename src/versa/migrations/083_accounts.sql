-- versa: accounts -- sign-in identities, invites and the learner's own
-- sign-up profile (accounts.py, profiles.py).
--
-- Append-only, like every store here (CLAUDE.md invariant 18). A learner is
-- still just `learners.id`; these tables say how a person proves they are
-- that learner, who let them in, and what they told us about themselves.

-- How a person signs in as a learner: a Firebase account (Google or
-- email/password -- `subject` is the Firebase uid) or, for the two dev
-- testers only, a dev name (`subject` is the lower-cased name). One row per
-- (provider, subject), written the first time it signs in, never edited.
CREATE TABLE IF NOT EXISTS learner_identities (
    id              UUID PRIMARY KEY,
    learner_id      UUID NOT NULL REFERENCES learners (id),
    provider        TEXT NOT NULL CHECK (provider IN ('firebase', 'dev')),
    subject         TEXT NOT NULL,
    -- what the identity provider said at first sign-in (google.com / password)
    sign_in_method  TEXT NULL,
    email           TEXT NULL,
    email_verified  BOOLEAN NULL,
    display_name    TEXT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, subject)
);

CREATE INDEX IF NOT EXISTS idx_learner_identities_learner ON learner_identities (learner_id);

-- Every successful sign-in (a session token was issued), for the record.
CREATE TABLE IF NOT EXISTS learner_sign_ins (
    id           UUID PRIMARY KEY,
    learner_id   UUID NOT NULL REFERENCES learners (id),
    identity_id  UUID NOT NULL REFERENCES learner_identities (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learner_sign_ins_learner ON learner_sign_ins (learner_id, created_at DESC);

-- Versa is invite-only: a NEW account needs a code. How many times a code
-- has been used is derived from invite_redemptions, never stored; a code is
-- withdrawn by adding an invite_revocations row, never by editing it.
CREATE TABLE IF NOT EXISTS invites (
    id          UUID PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    note        TEXT NULL,
    max_uses    INT NULL CHECK (max_uses IS NULL OR max_uses > 0),
    expires_at  TIMESTAMPTZ NULL,
    created_by  TEXT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per account an invite let in (a learner is let in once).
CREATE TABLE IF NOT EXISTS invite_redemptions (
    id          UUID PRIMARY KEY,
    invite_id   UUID NOT NULL REFERENCES invites (id),
    learner_id  UUID NOT NULL UNIQUE REFERENCES learners (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invite_redemptions_invite ON invite_redemptions (invite_id);

CREATE TABLE IF NOT EXISTS invite_revocations (
    id          UUID PRIMARY KEY,
    invite_id   UUID NOT NULL UNIQUE REFERENCES invites (id),
    reason      TEXT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The model call that reads a sign-up profile. There is no session yet, so
-- this is invariant 2's record in accounts' own table (same precedent as
-- topic_generations / exam_generations): full input incl. the prompt, and
-- the output or the error.
CREATE TABLE IF NOT EXISTS profile_extractions (
    id           UUID PRIMARY KEY,
    learner_id   UUID NOT NULL REFERENCES learners (id),
    node_name    TEXT NOT NULL,
    input_json   JSONB NOT NULL,
    output_json  JSONB NULL,
    error        TEXT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- What the learner told us at sign-up (and each time they edit it): the
-- answers verbatim, the consent they gave, and what the extraction read
-- from them. The latest row is the profile; earlier rows stay.
CREATE TABLE IF NOT EXISTS learner_profiles (
    id             UUID PRIMARY KEY,
    learner_id     UUID NOT NULL REFERENCES learners (id),
    answers        JSONB NOT NULL,
    consent        JSONB NOT NULL,
    extracted      JSONB NULL,
    extraction_id  UUID NULL REFERENCES profile_extractions (id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learner_profiles_learner ON learner_profiles (learner_id, created_at DESC);
