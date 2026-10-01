"""A course, an exam or a room built from a PICTURE the learner took (a page,
a syllabus, their notes): the picture is read once on upload (images.py,
invariant 21) -- in full when it is uploaded as a resource -- and that
reading is the resource's text. Nothing downstream handles the pixels."""

from __future__ import annotations

from uuid import UUID

import httpx
import pytest

from tests.test_exams import _llm as _exam_llm
from tests.test_server import _start, _stop
from versa import resources
from versa.images import READING_LIMIT, RESOURCE_READING_LIMIT, read_prompt, read_resource_prompt
from versa.llm import StubLLMClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SYLLABUS = (
    "Syllabus: Class 10 Physics, Electricity\n"
    "Unit 1: Electric current and circuits\n"
    "- charge, current $I = \\frac{Q}{t}$, the ampere\n"
    "Unit 2: Ohm's law and resistance\n"
    "- $V = IR$, resistors in series and parallel\n"
    "3. Heating effect of current\n"
    "The fuse wire melts when the current is too large."
)


# ------------------------------------------------------------------ pure


def test_a_reading_becomes_a_resource_that_keeps_the_pages_own_structure():
    res = resources.resource_from_reading(SYLLABUS, "IMG_2041.jpg")
    assert res.kind == "image" and res.filename == "IMG_2041.jpg"
    assert res.title == "Syllabus: Class 10 Physics, Electricity", "named after what it is, not after the camera"
    assert res.headings == [
        "Unit 1: Electric current and circuits", "Unit 2: Ohm's law and resistance", "3. Heating effect of current",
    ]
    assert "$V = IR$" in res.text and res.url is None
    with pytest.raises(resources.ResourceError):
        resources.resource_from_reading("   \n ")


def test_a_long_first_line_falls_back_to_a_real_file_name_never_a_camera_one():
    long_line = "The second law of thermodynamics says that " + "the entropy of an isolated system never falls " * 3
    assert resources.resource_from_reading(long_line, "thermo notes.png").title == "thermo notes"
    assert resources.resource_from_reading(long_line, "IMG_0007.png").title.startswith("The second law")


def test_a_resource_picture_is_asked_for_in_full_a_message_picture_is_not():
    resource, message = read_resource_prompt("page.jpg"), read_prompt("page.jpg")
    assert resource.startswith("IMAGE:READ") and message.startswith("IMAGE:READ")
    assert "A course will be built" in resource and "A course will be built" not in message
    assert "word for word" in resource and "heading" in resource
    assert RESOURCE_READING_LIMIT > READING_LIMIT


# ---------------------------------------------------------------- the API


async def _upload(client, learner_id, purpose=None, name="syllabus.png"):
    data = {"learner_id": learner_id} | ({"purpose": purpose} if purpose else {})
    return await client.post("/api/images", data=data, files={"file": (name, PNG, "application/octet-stream")})


@pytest.mark.asyncio(loop_scope="session")
async def test_a_course_is_mapped_from_the_learners_own_picture(clean_pool, embedding_client):
    llm = StubLLMClient({"IMAGE:READ": SYLLABUS})
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "photographer"})).json()["id"]
            other = (await client.post("/api/learners", json={"label": "someone-else"})).json()["id"]

            assert (await _upload(client, lid, "poster")).status_code == 422
            up = await _upload(client, lid, "resource")
            assert up.status_code == 200, up.text
            image_id = up.json()["id"]
            row = await clean_pool.fetchrow("SELECT prompt, reading FROM images WHERE id = $1", UUID(image_id))
            assert "A course will be built" in row["prompt"], "the prompt it was read with is on the row"
            assert row["reading"] == SYLLABUS

            r = await client.post("/api/topic-explorations/from-image",
                                  json={"learner_id": lid, "image_id": image_id})
            assert r.status_code == 200, r.text
            exploration = r.json()
            assert exploration["source_kind"] == "image"
            assert exploration["resource"]["kind"] == "image"
            assert exploration["resource"]["title"] == "Syllabus: Class 10 Physics, Electricity"
            assert exploration["root_nodes"], "branches came back"
            outline = next(p for p in reversed(llm.prompts) if "Unit 2: Ohm's law and resistance" in p)
            assert "The fuse wire melts" in outline, "the outline was made from the reading"

            # the reading is kept as the resource's text, like a PDF's
            kept = await clean_pool.fetchrow(
                "SELECT kind, text, filename FROM topic_resources WHERE id = $1", UUID(exploration["resource"]["id"]))
            assert kept["kind"] == "image" and kept["text"] == resources.resource_from_reading(SYLLABUS).text

            # a course can be built from it
            topic = await client.post("/api/topics", json={
                "learner_id": lid, "exploration_id": exploration["id"],
                "selected_node_ids": [exploration["root_nodes"][0]["id"]],
            })
            assert topic.status_code == 200, topic.text
            assert topic.json()["source_kind"] == "image"

            # someone else's picture, or one that doesn't exist, is refused
            theirs = await client.post("/api/topic-explorations/from-image",
                                       json={"learner_id": other, "image_id": image_id})
            assert theirs.status_code == 404
            missing = await client.post("/api/topic-explorations/from-image", json={
                "learner_id": lid, "image_id": "00000000-0000-0000-0000-000000000000"})
            assert missing.status_code == 404
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_exam_is_set_up_from_a_picture_of_the_syllabus(clean_pool, embedding_client):
    llm = _exam_llm()
    llm.canned["IMAGE:READ"] = SYLLABUS
    live = await _start(clean_pool, llm, embedding_client)
    try:
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            lid = (await client.post("/api/learners", json={"label": "exam-photo"})).json()["id"]
            other = (await client.post("/api/learners", json={"label": "exam-other"})).json()["id"]
            image_id = (await _upload(client, lid, "resource")).json()["id"]

            r = await client.post("/api/exams/from-image", json={
                "learner_id": lid, "image_id": image_id, "exam_date": "2030-01-02"})
            assert r.status_code == 200, r.text
            exam = r.json()
            assert exam["source_kind"] == "image" and exam["units"]
            assert exam["title"] == "Syllabus: Class 10 Physics, Electricity"
            assert "The fuse wire melts" in llm.prompts[-1], "the syllabus read the picture's reading"

            theirs = await client.post("/api/exams/from-image", json={"learner_id": other, "image_id": image_id})
            assert theirs.status_code == 404
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_room_is_built_from_the_words_read_out_of_a_picture(clean_pool, embedding_client):
    live = await _start(clean_pool, StubLLMClient(), embedding_client)
    try:
        hub = live.app.state.room_hub
        async with httpx.AsyncClient(base_url=live.http, timeout=30) as client:
            r = await client.post("/api/rooms", json={
                "code": "phys-10", "name": "Kim", "picture": {"reading": SYLLABUS, "filename": "board.jpg"}})
            assert r.status_code == 200, r.text
            room = r.json()["room"]
            assert room["source_kind"] == "image" and room["resource_filename"] == "board.jpg"
            empty = await client.post("/api/rooms", json={
                "code": "phys-11", "name": "Kim", "picture": {"reading": ""}})
            assert empty.status_code == 422
        await hub.wait_idle()
        kept = await clean_pool.fetchval("SELECT resource_excerpt FROM rooms WHERE code = 'phys-10'")
        assert "The fuse wire melts" in kept, "Versa teaches the room from the reading"
    finally:
        await _stop(live)
