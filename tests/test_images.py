"""Pictures attached to messages (images.py, migration 090, CLAUDE.md
invariant 21): read once on upload, the reading given to the turn and the
stage, append-only like every other store."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import websockets

import versa.images as images_module
from tests.test_rooms_append_only import _string_literals
from tests.test_server import _turn, live, new_chat  # noqa: F401  (fixtures)
from versa import stage as _stage
from versa.formatting import MATH_STYLE
from versa.images import ImageError, ImageStore, sniff_mime, with_image
from versa.llm import _DEFAULT_RESPONSES

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
READING = _DEFAULT_RESPONSES["IMAGE:READ"]

# ------------------------------------------------------------- invariant 21


def test_images_module_never_deletes_or_updates():
    path = Path(images_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_images_migration_has_no_delete_or_update():
    path = Path(images_module.__file__).resolve().parent / "migrations" / "090_images.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_image_store_has_no_removal_methods():
    for name in dir(ImageStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name


# ------------------------------------------------------------------ pure


def test_the_type_is_read_from_the_bytes_not_the_name():
    assert sniff_mime(PNG) == "image/png"
    assert sniff_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    with pytest.raises(ImageError):
        sniff_mime(b"%PDF-1.7 not a picture")


def test_a_picture_goes_with_the_words_and_alone_still_asks():
    assert with_image("is this right?", "x = 2") == "is this right?\n\n[Attached picture -- what it shows: x = 2]"
    assert with_image("", "a graph").startswith("(They sent only this picture")


def test_the_slime_is_told_it_can_hold_the_picture_up_only_when_there_is_one():
    assert "kind\":\"photo\"" in _stage.stage_prompt("q", live=True, photo=True)
    assert "THE STUDENT ATTACHED A PICTURE" not in _stage.stage_prompt("q", live=True)
    assert _stage.sanitize_action({"do": "spawn", "id": "pic", "kind": "photo", "x": 0.7})["kind"] == "photo"


# --------------------------------------------------------------- the API


async def _upload(client, learner_id, data=PNG, name="working.png"):
    return await client.post("/api/images", data={"learner_id": learner_id},
                             files={"file": (name, data, "application/octet-stream")})


@pytest.mark.asyncio(loop_scope="session")
async def test_an_upload_is_read_kept_and_served_back(live, new_chat, clean_pool):
    learner_id, _ = await new_chat()
    async with httpx.AsyncClient(base_url=live.http) as client:
        r = await _upload(client, learner_id)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mime_type"] == "image/png" and body["reading"] == READING
        back = await client.get(f"/api/images/{body['id']}")
        assert back.status_code == 200 and back.content == PNG
        assert back.headers["content-type"] == "image/png"
        assert (await _upload(client, learner_id, b"%PDF-1.7", "notes.pdf")).status_code == 422
    row = await clean_pool.fetchrow("SELECT prompt, reading, error, byte_count FROM images WHERE id = $1",
                                    __import__("uuid").UUID(body["id"]))
    assert row["prompt"].startswith("IMAGE:READ") and row["reading"] == READING
    assert row["error"] is None and row["byte_count"] == len(PNG)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_turn_with_a_picture_answers_about_it_and_the_slime_gets_it(live, new_chat, clean_pool):
    learner_id, session_id = await new_chat()
    async with httpx.AsyncClient(base_url=live.http) as client:
        image_id = (await _upload(client, learner_id)).json()["id"]
    async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat") as ws:
        events = await _turn(ws, {"type": "message", "text": "is my working right?", "image_id": image_id,
                                  "stage": True})
    assert events[-1]["type"] == "done", events
    await live.loop.wait_for_background_tasks()
    rows = await clean_pool.fetch(
        "SELECT node_name, input_json FROM node_calls WHERE session_id = $1", __import__("uuid").UUID(session_id))
    answer = next(json.loads(r["input_json"]) if isinstance(r["input_json"], str) else r["input_json"]
                  for r in rows if r["node_name"] == "FinalAnswer")
    assert "is my working right?" in json.dumps(answer) and "Attached picture" in json.dumps(answer)
    stage = [r for r in rows if r["node_name"] == "StageDirector"]
    assert stage, "the slime was directed"
    stage_input = stage[0]["input_json"]
    stage_input = json.loads(stage_input) if isinstance(stage_input, str) else stage_input
    assert stage_input.get("photo") is True and "Attached picture" in stage_input["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_picture_alone_is_a_question_and_someone_elses_is_refused(live, new_chat):
    owner, _ = await new_chat("owner")
    _, other_session = await new_chat("other")
    async with httpx.AsyncClient(base_url=live.http) as client:
        theirs = (await _upload(client, owner)).json()["id"]
    async with websockets.connect(f"{live.ws}/api/sessions/{other_session}/chat") as ws:
        refused = await _turn(ws, {"type": "message", "text": "", "image_id": theirs})
        assert refused[-1] == {"type": "error", "message": "unknown picture"}
        # "hi" with a picture is not small talk
        own, own_session = await new_chat("third")
        async with httpx.AsyncClient(base_url=live.http) as client:
            mine = (await _upload(client, own)).json()["id"]
    async with websockets.connect(f"{live.ws}/api/sessions/{own_session}/chat") as ws:
        events = await _turn(ws, {"type": "message", "text": "hi", "image_id": mine})
    assert not any(e["type"] == "chatter" for e in events)
    assert events[-1]["type"] == "done"


def test_the_answer_is_asked_to_write_maths_as_latex():
    import versa.disambiguate as d
    source = Path(d.__file__).read_text(encoding="utf-8")
    assert "MATH_STYLE" in source and "$" in MATH_STYLE
