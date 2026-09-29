"""Chatter: messages that make sense but need no analysis and no storing.

"ok", "thanks!", "haha nice", "got it 👍", "hi", "bye", "hmm" -- a person
reacting, not asking. Running one through the whole turn (the ambiguity
check, an answer, a new set of directions) costs three model calls for
nothing, and worse, it reads as evidence: a typed "ok" instead of a card
would count as a MISS (directions.py, migration 088), get read by the model,
and bend the thinking style. So a chatter message is answered with a short
reply from here -- no model call, nothing recorded, the open directions and
options left open so they can still be taken.

The rule is conservative: anything that could need an answer goes through
the normal turn. Every word must be chatter (from the lists below); a
question mark, a number, or any other word sends it through. And a message
that could be ANSWERING the tutor -- "ok", "sure", "yes", "no" -- goes
through when the last answer ended by asking something ("Want an
example?" -> "ok" means yes, do it).

`classify(text, after_question=...)` returns the kind, or None: not chatter.
"""

from __future__ import annotations

import random
import re
import unicodedata
from typing import Literal

Kind = Literal["thanks", "ack", "laugh", "greeting", "farewell", "filler", "emoji", "praise"]

# Longest message that can be chatter, in words ("ok got it thanks a lot").
MAX_WORDS = 7

# Phrases first (matched as whole-word sequences), then single words.
_PHRASES: dict[str, Kind] = {
    "thank you": "thanks", "thanks a lot": "thanks", "thank u": "thanks", "much appreciated": "thanks",
    "got it": "ack", "i see": "ack", "makes sense": "ack", "that makes sense": "ack", "i get it": "ack",
    "i understand": "ack", "fair enough": "ack", "all right": "ack", "sounds good": "ack", "i got it": "ack",
    "good morning": "greeting", "good afternoon": "greeting", "good evening": "greeting",
    "good night": "farewell", "see you": "farewell", "see ya": "farewell", "talk later": "farewell",
    "catch you later": "farewell", "that's all": "farewell", "thats all": "farewell",
    "well done": "praise", "good job": "praise", "nicely explained": "praise", "well explained": "praise",
}
_WORDS: dict[str, Kind] = {
    **dict.fromkeys(["thanks", "thx", "ty", "tysm", "thankyou", "thnx", "tnx", "cheers"], "thanks"),
    # not "yes" / "no" / "right" / "true": alone they can carry content ("no, that's wrong")
    **dict.fromkeys(["ok", "okay", "k", "kk", "okie", "okey", "alright", "understood", "noted", "gotcha",
                     "cool", "sure", "yeah", "yep", "yup", "clear", "indeed", "roger"], "ack"),
    **dict.fromkeys(["lol", "haha", "hahaha", "hehe", "lmao", "rofl", "xd"], "laugh"),
    **dict.fromkeys(["hi", "hello", "hey", "heya", "hiya", "yo", "namaste", "sup"], "greeting"),
    **dict.fromkeys(["bye", "goodbye", "cya", "gn", "ttyl", "later"], "farewell"),
    **dict.fromkeys(["hmm", "hm", "hmmm", "umm", "um", "uh", "oh", "ah", "ahh", "ohh", "ooh", "oo", "aha",
                     "wow", "whoa", "woah", "huh"], "filler"),
    **dict.fromkeys(["nice", "great", "awesome", "amazing", "perfect", "brilliant", "excellent", "interesting",
                     "neat", "superb", "wonderful", "good", "beautiful", "fantastic"], "praise"),
}
# Joining words allowed between chatter words ("ok and thanks", "oh wow so cool").
# Not "now" / "then" / "again": "ok now" can mean "carry on".
_GLUE = {"and", "so", "very", "really", "much", "a", "lot", "that", "is", "was", "its", "it's", "that's",
         "thats", "this", "super", "too", "man", "bro", "dude", "mate", "all", "for", "the", "help",
         "oh", "well", "just", "you", "u", "sir", "maam", "ma'am", "buddy"}
# Words that, as a whole reply, can answer a tutor's question -- through when it asked one.
_ANSWERS = {"ok", "okay", "k", "kk", "okie", "okey", "alright", "sure", "yeah", "yep", "yup", "cool"}
# Laughter spelled out any length: "hahahaha", "hehehe".
_LAUGH = re.compile(r"^(?:ha|he|hi|ah){2,}h?$")
_STRETCH = re.compile(r"(.)\1{2,}")  # "okkkk" -> "okk", "thanksss" -> "thankss"


def _is_symbolic(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat.startswith(("S", "P", "Z")) or ch in "‍️"


def _norm(word: str) -> str:
    w = _STRETCH.sub(r"\1\1", word)
    for candidate in (word, w, _STRETCH.sub(r"\1", word)):
        if candidate in _WORDS or candidate in _GLUE:
            return candidate
    return w


def classify(text: str, *, after_question: bool = False) -> Kind | None:
    """The kind of chatter `text` is, or None -- it goes through the normal
    turn. `after_question`: the last answer ended by asking the learner
    something, so an "ok" / "yes" may be their answer."""
    raw = (text or "").strip()
    if not raw or len(raw) > 80:
        return None
    if "?" in raw or any(ch.isdigit() for ch in raw):
        return None  # a question, or something to work with
    letters = [ch for ch in raw if ch.isalpha()]
    if not letters:
        # emoji / symbols only (a bare "?" was sent through above)
        return "emoji" if any(not ch.isspace() for ch in raw) and all(_is_symbolic(ch) or ch.isspace()
                                                                       for ch in raw) else None
    words = [_norm(w) for w in re.findall(r"[a-z']+", raw.lower())]
    if not words or len(words) > MAX_WORDS:
        return None
    kinds: list[Kind] = []
    i = 0
    while i < len(words):
        for size in (3, 2):
            phrase = " ".join(words[i:i + size])
            if len(words) - i >= size and phrase in _PHRASES:
                kinds.append(_PHRASES[phrase])
                i += size
                break
        else:
            w = words[i]
            if w in _WORDS:
                kinds.append(_WORDS[w])
            elif _LAUGH.match(w):
                kinds.append("laugh")
            elif w not in _GLUE:
                return None  # a word with content: not chatter
            i += 1
    if not kinds:
        return None  # only joining words ("so", "then") -- let the turn decide
    if after_question and all(w in _ANSWERS or w in _GLUE for w in words):
        return None  # "ok" / "yes" after "Want an example?" is an answer
    if after_question and any(k == "ack" for k in kinds) and not any(k == "thanks" for k in kinds):
        return None
    # the most telling kind: thanks and farewells over acknowledgements
    for kind in ("farewell", "thanks", "greeting", "laugh", "praise", "ack", "filler"):
        if kind in kinds:
            return kind  # type: ignore[return-value]
    return kinds[0]


_REPLIES: dict[Kind, tuple[str, ...]] = {
    "thanks": ("You're welcome!", "Happy to help!", "Anytime!"),
    "ack": ("Great.", "Good.", "\U0001F44D"),
    "laugh": ("\U0001F604", "Ha!"),
    "greeting": ("Hi! What would you like to explore?", "Hello! Ask me anything."),
    "farewell": ("See you next time!", "Bye for now — come back anytime."),
    "filler": ("Take your time.", "No rush — ask whenever you're ready."),
    "emoji": ("\U0001F642",),
    "praise": ("Glad that landed!", "Thank you! \U0001F60A"),
}


def reply(kind: Kind, *, directions_open: bool = False, rng: random.Random | None = None) -> str:
    """A short reply, no model call. With cards still open, a pointer to them."""
    text = (rng or random).choice(_REPLIES[kind])
    if directions_open and kind in ("thanks", "ack", "praise", "filler", "emoji", "laugh"):
        text += " Pick a direction below, or ask me anything."
    return text
