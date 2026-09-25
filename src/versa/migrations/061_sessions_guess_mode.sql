-- versa: "Guess mode" (IDEAS.md "convert each query to options" entry) --
-- an Akinator-style knob for a session: every message that ISN'T already
-- topically ambiguous still gets one more narrowing check -- is there a
-- genuinely open question about HOW to teach it? -- before FinalAnswer
-- ever runs. Off by default: existing sessions, and every session created
-- before this knob existed, behave exactly as they always have.
--
-- A session-level flag, not a learner-level one (unlike app_mode's sibling
-- pattern this migration otherwise follows) -- deliberately: this is a
-- mode you opt into for a given conversation, same as the client's own
-- "Animations" knob is a standing app setting but this is asked for per
-- chat, not globally.

ALTER TABLE sessions ADD COLUMN guess_mode BOOLEAN NOT NULL DEFAULT FALSE;
