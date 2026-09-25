-- versa: per-session style knobs (IDEAS.md "Session knobs"): answer length,
-- depth and tone, changeable mid-chat from the Sandbox knobs rail. Each
-- value only shapes how FinalAnswer writes; the defaults reproduce the
-- pre-knob behaviour exactly (no directive is rendered when all three are
-- at their default), so every existing session is unaffected.

ALTER TABLE sessions
    ADD COLUMN answer_length TEXT NOT NULL DEFAULT 'balanced'
        CHECK (answer_length IN ('terse', 'balanced', 'full')),
    ADD COLUMN depth TEXT NOT NULL DEFAULT 'standard'
        CHECK (depth IN ('gist', 'standard', 'rigorous')),
    ADD COLUMN tone TEXT NOT NULL DEFAULT 'direct'
        CHECK (tone IN ('direct', 'socratic'));
