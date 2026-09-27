-- versa: how a directions set was PRESENTED (directions.py). The same six
-- directions can be shown as cards under the answer ('strip') or as inline
-- links the answer ends with, where a pick continues the same explanation
-- ('fork', IDEAS.md 2026-09-27). Different presentations can change what
-- people pick, so every set records which one the learner actually saw.
-- Sets from before this migration were all cards.

ALTER TABLE direction_sets
    ADD COLUMN presentation TEXT NOT NULL DEFAULT 'strip'
        CHECK (presentation IN ('strip', 'fork'));
