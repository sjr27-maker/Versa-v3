-- versa: a course or an exam can be built from a PICTURE the learner took
-- (a page, a syllabus, their notes), next to a search, a PDF and a web link.
-- The picture is read once on upload (images.py, migration 090, invariant
-- 21); that reading is the resource's text, kept in topic_resources like a
-- PDF's -- nothing downstream sees the pixels.
--
-- Only the allowed values of the four kind columns widen: every row already
-- there satisfies the new check, and none is touched.

ALTER TABLE topic_resources
    DROP CONSTRAINT topic_resources_kind_check,
    ADD CONSTRAINT topic_resources_kind_check CHECK (kind IN ('pdf', 'link', 'image'));

ALTER TABLE topic_explorations
    DROP CONSTRAINT topic_explorations_source_kind_check,
    ADD CONSTRAINT topic_explorations_source_kind_check
        CHECK (source_kind IN ('search', 'pdf', 'link', 'image'));

ALTER TABLE topics
    DROP CONSTRAINT topics_source_kind_check,
    ADD CONSTRAINT topics_source_kind_check CHECK (source_kind IN ('search', 'pdf', 'link', 'image'));

ALTER TABLE exams
    DROP CONSTRAINT exams_source_kind_check,
    ADD CONSTRAINT exams_source_kind_check
        CHECK (source_kind IN ('search', 'pdf', 'link', 'course', 'image'));
