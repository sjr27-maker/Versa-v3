-- versa: a new chat's length slider starts at 40 instead of 50
-- (session_knobs.py DEFAULT_ANSWER_LENGTH, 2026-10-01: untouched answers ran
-- long on a phone). Only the default for sessions created from here on --
-- every existing session keeps the level it has, and depth and breadth still
-- start at 50.

ALTER TABLE sessions ALTER COLUMN answer_length_level SET DEFAULT 40;
