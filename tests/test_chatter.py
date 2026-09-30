"""Chatter (chatter.py): a reaction is answered without analysis or storing;
anything that could need an answer goes through the normal turn."""

from __future__ import annotations

import pytest

from versa.chatter import classify, reply


@pytest.mark.parametrize(("text", "kind"), [
    ("ok", "ack"), ("okay!", "ack"), ("okkkk", "ack"), ("got it", "ack"), ("makes sense", "ack"),
    ("I see", "ack"), ("cool", "ack"),
    ("thanks", "thanks"), ("thank you so much!", "thanks"), ("ok thanks", "thanks"), ("thx", "thanks"),
    ("got it, thanks for the help", "thanks"),
    ("haha", "laugh"), ("hahahaha", "laugh"), ("lol", "laugh"),
    ("hi", "greeting"), ("hello there", None), ("hey", "greeting"), ("good morning", "greeting"),
    ("bye", "farewell"), ("see you", "farewell"), ("good night", "farewell"),
    ("hmm", "filler"), ("oh", "filler"), ("wow", "filler"),
    ("nice", "praise"), ("great, well explained", "praise"), ("oh wow so cool", "ack"),
    ("👍", "emoji"), ("🙂🙂", "emoji"), ("!!!", "emoji"),
])
def test_reactions_are_chatter(text, kind):
    assert classify(text) == kind


@pytest.mark.parametrize("text", [
    "?", "ok but why?", "ok but why", "thanks, what about integrals", "what", "why", "no", "yes", "nope",
    "no that's wrong", "ok now", "again", "more", "go on", "continue", "explain simpler", "ok 2+2",
    "hello, can you explain derivatives", "derivative", "ok so what is a limit", "i don't get it",
    "that is not right", "", "   ", "wow " * 10,
])
def test_anything_that_could_need_an_answer_goes_through(text):
    assert classify(text) is None


def test_an_ok_after_the_tutor_asked_something_is_an_answer():
    assert classify("ok", after_question=True) is None  # "Want an example?" -> "ok"
    assert classify("sure", after_question=True) is None
    assert classify("yeah ok", after_question=True) is None
    assert classify("thanks", after_question=True) == "thanks"  # still just thanks
    assert classify("haha", after_question=True) == "laugh"


def test_the_reply_points_back_to_open_cards():
    assert "Pick a direction below" in reply("ack", directions_open=True)
    assert "Pick a direction" not in reply("ack")
    assert "Pick a direction" not in reply("farewell", directions_open=True)
