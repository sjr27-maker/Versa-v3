"""Sign-in, invites, the ownership guard (accounts.py) and the sign-up
profile (profiles.py), through the real HTTP/WebSocket server.

Firebase is faked at the one seam it has (`Auth.verify_firebase`): a token
"google|<uid>|<email>|<name>" verifies as that account, anything else is
rejected -- exactly what the real verifier does for a forged token."""

from __future__ import annotations

import asyncio
import json
import re
import socket
from pathlib import Path
from uuid import UUID

import httpx
import pytest
import uvicorn
import websockets

import versa.accounts as accounts_module
import versa.profiles as profiles_module
from tests.test_rooms_append_only import _string_literals
from tests.test_server import _ANSWER, _FACT, _NOT_AMBIGUOUS, _Live, _stop, _turn
from versa.accounts import (
    OWNER_SQL,
    UNOWNED_PARAMS,
    AccountStore,
    Auth,
    FirebaseUser,
    InvalidIdToken,
    SessionTokens,
)
from versa.learner import LearnerStore
from versa.llm import ModelTierClients, StubLLMClient
from versa.server import create_app

SECRET = "s" * 48


async def _fake_firebase(token: str) -> FirebaseUser:
    parts = token.split("|")
    if len(parts) != 4 or parts[0] != "google":
        raise InvalidIdToken("forged")
    _, uid, email, name = parts
    return FirebaseUser(uid=uid, email=email, email_verified=True, name=name, sign_in_method="google.com")


def _auth(**overrides) -> Auth:
    settings = {
        "tokens": SessionTokens(SECRET),
        "verify_firebase": _fake_firebase,
        "dev_logins": frozenset({"sooraj", "adithya"}),
        "invites_required": True,
        "firebase_project_id": "versa-test",
    }
    settings.update(overrides)
    return Auth(**settings)


async def _start(pool, llm, embedding_client, auth: Auth) -> _Live:
    app = create_app(pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client,
                     llm_mode="stub", auth=auth, android_download_url="https://example.test/versa.apk")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    return _Live(port, app, server, task)


def _llm(**canned) -> StubLLMClient:
    return StubLLMClient(canned={
        "ASSESS:BRANCH": _NOT_AMBIGUOUS, "FINAL:ANSWER": _ANSWER, "WRITE:FACT": _FACT, **canned,
    })


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _google(client: httpx.AsyncClient, uid: str, invite: str | None = None) -> httpx.Response:
    body = {"id_token": f"google|{uid}|{uid}@example.test|{uid.title()} Person"}
    if invite is not None:
        body["invite_code"] = invite
    return await client.post("/api/auth/firebase", json=body)


_PROFILE = {
    "name": "Asha", "age": 16, "occupation": "school", "country": "India", "region": "Kerala",
    "institution": "St. Mary's HSS", "curriculum": "CBSE", "level": "Class 11",
    "subjects": "Physics, Chemistry, Maths", "goals": "JEE Main",
    "consent": {"data": True, "guardian": True},
}


# ------------------------------------------------------------------ tokens


def test_session_tokens_round_trip_and_reject_forgeries_and_expiry():
    now = [1_000_000.0]
    tokens = SessionTokens(SECRET, days=1, clock=lambda: now[0])
    learner = UUID("12345678-1234-5678-1234-567812345678")
    token = tokens.issue(learner, method="google.com")
    assert tokens.verify(token) == learner
    version, payload, sig = token.split(".")
    assert tokens.verify(f"{version}.{payload}.{sig[:-2]}xx") is None
    assert SessionTokens("t" * 48).verify(token) is None  # another server's secret
    assert tokens.verify("garbage") is None
    now[0] += 2 * 86400
    assert tokens.verify(token) is None
    with pytest.raises(ValueError):
        SessionTokens("short")


def test_every_path_parameter_is_ownership_checked_or_deliberately_unowned(pool, embedding_client):
    """A new route with a new kind of id must be added to OWNER_SQL (or,
    deliberately, UNOWNED_PARAMS) -- otherwise the guard wouldn't check it."""
    llm = _llm()
    app = create_app(pool, ModelTierClients(fast=llm, capable=llm, best=llm), embedding_client,
                     llm_mode="stub", auth=_auth())
    params = set()
    for route in app.routes:
        params.update(re.findall(r"{(\w+)}", getattr(route, "path", "")))
    params.discard("path")  # the static web mount
    unknown = params - set(OWNER_SQL) - UNOWNED_PARAMS
    assert not unknown, f"add these to accounts.OWNER_SQL or UNOWNED_PARAMS: {sorted(unknown)}"


# ------------------------------------------------------------------ sign-in + invites


@pytest.mark.asyncio(loop_scope="session")
async def test_everything_but_health_and_sign_in_needs_a_token(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client, _auth())
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            health = (await client.get("/api/health")).json()
            assert health["auth"] == {"required": True, "firebase": True, "dev": True,
                                      "dev_code": False, "judge": False, "invites": True}
            learner = await LearnerStore(clean_pool).create(label="someone")
            assert (await client.get(f"/api/learners/{learner.id}/sessions")).status_code == 401
            assert (await client.post("/api/sessions", json={"learner_id": str(learner.id)})).status_code == 401
            bad = {"Authorization": "Bearer v1.bogus.token"}
            assert (await client.get(f"/api/learners/{learner.id}/sparks", headers=bad)).status_code == 401
            # the name-only route of the open server doesn't exist with sign-in on
            assert (await client.post("/api/learners", json={"label": "sooraj"})).status_code in (401, 404, 405)
            # the RevenueCat webhook keeps its own check (401 from it, not the guard's)
            hook = await client.post("/api/billing/revenuecat/webhook", json={})
            assert hook.status_code != 200
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_new_account_needs_a_working_invite_and_an_old_one_never_does(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client, _auth())
    store = AccountStore(clean_pool)
    single = await store.create_invite(max_uses=1, note="for asha")
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            r = await _google(client, "asha")
            assert r.status_code == 403 and r.json()["detail"]["reason"] == "invite"
            r = await _google(client, "asha", invite="NOPE1234")
            assert r.status_code == 403 and "isn't valid" in r.json()["detail"]["message"]
            assert (await client.get(f"/api/auth/invites/{single.code}")).json()["valid"] is True

            first = await _google(client, "asha", invite=single.code.lower())  # case-insensitive
            assert first.status_code == 200, first.text
            body = first.json()
            assert body["new_account"] is True and body["profile_complete"] is False
            assert body["learner"]["label"] == "Asha Person"

            # the code is used up for anyone else...
            other = await _google(client, "ravi", invite=single.code)
            assert other.status_code == 403 and "used up" in other.json()["detail"]["message"]
            assert (await client.get(f"/api/auth/invites/{single.code}")).json()["valid"] is False
            # ...but the same person signs back in with no code, as the same learner
            again = await _google(client, "asha")
            assert again.status_code == 200 and again.json()["new_account"] is False
            assert again.json()["learner"]["id"] == body["learner"]["id"]

            me = await client.get("/api/me", headers=_bearer(again.json()["token"]))
            assert me.json()["email"] == "asha@example.test"
            assert me.json()["profile_complete"] is False

            # a forged Firebase token is refused
            forged = await client.post("/api/auth/firebase", json={"id_token": "not-a-real-token"})
            assert forged.status_code == 401

            # a revoked code stops working; the invite page says so
            multi = await store.create_invite(max_uses=None)
            await store.revoke_invite(multi.code, "leaked")
            assert (await _google(client, "meera", invite=multi.code)).status_code == 403
            page = await client.get(f"/invite/{multi.code}")
            assert page.status_code == 200 and "withdrawn" in page.text
            fresh = await store.create_invite(max_uses=5)
            page = await client.get(f"/invite/{fresh.code}")
            assert fresh.code in page.text and "https://example.test/versa.apk" in page.text

        async with clean_pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM invite_redemptions") == 1
            assert await conn.fetchval("SELECT count(*) FROM learner_sign_ins") == 2
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_invites_off_lets_anyone_with_a_google_account_in(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client, _auth(invites_required=False))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            assert (await _google(client, "open")).status_code == 200
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_dev_sign_in_is_only_for_the_two_testers_and_keeps_their_old_learner(clean_pool, embedding_client):
    existing = await LearnerStore(clean_pool).create(label="Sooraj")
    live = await _start(clean_pool, _llm(), embedding_client, _auth())
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            r = await client.post("/api/auth/dev", json={"name": " sooraj "})
            assert r.status_code == 200, r.text
            assert r.json()["learner"]["id"] == str(existing.id)  # their pre-sign-in history stays theirs
            again = await client.post("/api/auth/dev", json={"name": "SOORAJ"})
            assert again.json()["learner"]["id"] == str(existing.id)
            adithya = await client.post("/api/auth/dev", json={"name": "Adithya"})
            assert adithya.status_code == 200 and adithya.json()["learner"]["id"] != str(existing.id)
            assert (await client.post("/api/auth/dev", json={"name": "mallory"})).status_code == 403
    finally:
        await _stop(live)

    live = await _start(clean_pool, _llm(), embedding_client, _auth(dev_login_code="tester-code"))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            assert (await client.post("/api/auth/dev", json={"name": "sooraj"})).status_code == 403
            ok = await client.post("/api/auth/dev", json={"name": "sooraj", "code": "tester-code"})
            assert ok.status_code == 200
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_judges_sign_in_with_any_name_and_the_shared_code(clean_pool, embedding_client):
    """Any name + the judge code: each name its own account (the same name
    comes back to it), never an existing learner -- a judge typing a team
    tester's name gets a separate account. Wrong or missing code: refused;
    no code configured: judge sign-in is closed."""
    team = await LearnerStore(clean_pool).create(label="Sooraj")
    live = await _start(clean_pool, _llm(), embedding_client, _auth(judge_code="judge-2026"))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            health = (await client.get("/api/health")).json()
            assert health["auth"]["judge"] is True
            anna = await client.post("/api/auth/judge", json={"name": "Anna Judge", "code": "judge-2026"})
            assert anna.status_code == 200, anna.text
            body = anna.json()
            assert body["learner"]["label"] == "Anna Judge" and body["sign_in_method"] == "judge"
            assert body["profile_complete"] is False  # a judge may skip it in the app
            again = await client.post("/api/auth/judge", json={"name": " anna   JUDGE ", "code": "judge-2026"})
            assert again.json()["learner"]["id"] == body["learner"]["id"]
            bo = await client.post("/api/auth/judge", json={"name": "Bo", "code": "judge-2026"})
            assert bo.json()["learner"]["id"] != body["learner"]["id"]
            sneaky = await client.post("/api/auth/judge", json={"name": "Sooraj", "code": "judge-2026"})
            assert sneaky.status_code == 200 and sneaky.json()["learner"]["id"] != str(team.id)
            assert sneaky.json()["learner"]["label"] == "Sooraj (judge)"  # names are unique
            back = await client.post("/api/auth/judge", json={"name": "sooraj", "code": "judge-2026"})
            assert back.json()["learner"]["id"] == sneaky.json()["learner"]["id"]
            assert (await client.post("/api/auth/judge", json={"name": "Cy", "code": "nope"})).status_code == 403
            assert (await client.post("/api/auth/judge", json={"name": "Cy"})).status_code == 403
            # the token works like any other: the judge reaches their own things only
            token = body["token"]
            me = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
            assert me.json()["sign_in_method"] == "judge"
            theirs = await client.get(f"/api/learners/{team.id}/sessions/all",
                                      headers={"Authorization": f"Bearer {token}"})
            assert theirs.status_code == 404
    finally:
        await _stop(live)

    live = await _start(clean_pool, _llm(), embedding_client, _auth())
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            assert (await client.get("/api/health")).json()["auth"]["judge"] is False
            closed = await client.post("/api/auth/judge", json={"name": "Anna Judge", "code": "judge-2026"})
            assert closed.status_code == 403
    finally:
        await _stop(live)


def test_auth_from_env_is_strict_off_this_machine(monkeypatch):
    for key in ("VERSA_AUTH", "VERSA_SESSION_SECRET", "FIREBASE_PROJECT_ID", "VERSA_DEV_LOGINS",
                "VERSA_DEV_LOGIN_CODE", "VERSA_INVITES", "VERSA_JUDGE_CODE"):
        monkeypatch.delenv(key, raising=False)
    local = Auth.from_env(local=True)
    assert local is not None and local.dev_logins == {"sooraj", "adithya"} and not local.google
    with pytest.raises(SystemExit):
        Auth.from_env(local=False)  # no secret
    monkeypatch.setenv("VERSA_SESSION_SECRET", SECRET)
    deployed = Auth.from_env(local=False)
    assert deployed.dev_logins == frozenset()  # no name-only sign-in without a code
    monkeypatch.setenv("VERSA_DEV_LOGINS", "sooraj,adithya")
    monkeypatch.setenv("VERSA_DEV_LOGIN_CODE", "c0de")
    assert Auth.from_env(local=False).dev_logins == {"sooraj", "adithya"}
    assert Auth.from_env(local=False).judge_code is None  # judges: off unless given a code
    monkeypatch.setenv("VERSA_JUDGE_CODE", " judges ")
    assert Auth.from_env(local=False).judge_code == "judges"
    monkeypatch.setenv("VERSA_AUTH", "off")
    assert Auth.from_env(local=True) is None


# ------------------------------------------------------------------ the guard


@pytest.mark.asyncio(loop_scope="session")
async def test_a_learner_can_only_reach_their_own_things(clean_pool, embedding_client):
    live = await _start(clean_pool, _llm(), embedding_client, _auth(invites_required=False))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            a = (await _google(client, "alice")).json()
            b = (await _google(client, "bob")).json()
            ha, hb = _bearer(a["token"]), _bearer(b["token"])
            a_id, b_id = a["learner"]["id"], b["learner"]["id"]

            made = await client.post("/api/sessions", json={"learner_id": a_id}, headers=ha)
            assert made.status_code == 200
            session_id = made.json()["session_id"]

            # Bob can't create a session for Alice, list hers, or read her chat
            assert (await client.post("/api/sessions", json={"learner_id": a_id}, headers=hb)).status_code == 404
            assert (await client.get(f"/api/learners/{a_id}/sessions", headers=hb)).status_code == 404
            assert (await client.get(f"/api/sessions/{session_id}/history", headers=hb)).status_code == 404
            assert (await client.get(f"/api/sessions/{session_id}/knobs", headers=hb)).status_code == 404
            assert (await client.get(f"/api/learners/{a_id}/sparks", headers=hb)).status_code == 404
            assert (await client.get(f"/api/learners/{a_id}/profile", headers=hb)).status_code == 404
            # ...not even through a form field
            pdf = await client.post("/api/topic-explorations/from-pdf", headers=hb,
                                    data={"learner_id": a_id}, files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
            assert pdf.status_code == 404
            # Alice can
            assert (await client.get(f"/api/sessions/{session_id}/history", headers=ha)).status_code == 200
            assert (await client.get(f"/api/learners/{a_id}/sessions", headers=ha)).status_code == 200
            assert (await client.get(f"/api/learners/{b_id}/sessions", headers=hb)).status_code == 200

            # the chat socket: no token, or Bob's, is refused; Alice's works
            url = f"{live.ws}/api/sessions/{session_id}/chat"
            with pytest.raises(websockets.exceptions.InvalidStatus):
                async with websockets.connect(url):
                    pass
            with pytest.raises((websockets.exceptions.InvalidStatus, websockets.exceptions.ConnectionClosed)):
                async with websockets.connect(url, subprotocols=["versa", b["token"]]) as ws:
                    await ws.send(json.dumps({"type": "message", "text": "what is a limit?"}))
                    await asyncio.wait_for(ws.recv(), timeout=5)
            async with websockets.connect(url, subprotocols=["versa", a["token"]]) as ws:
                assert ws.subprotocol == "versa"
                events = await _turn(ws, {"type": "message", "text": "what is a derivative?"})
            assert events[-1]["type"] == "done" and events[-1]["text"] == _ANSWER
    finally:
        await _stop(live)


# ------------------------------------------------------------------ the profile


@pytest.mark.asyncio(loop_scope="session")
async def test_the_profile_is_checked_saved_append_only_and_shapes_the_answer(clean_pool, embedding_client):
    llm = _llm()
    live = await _start(clean_pool, llm, embedding_client, _auth(invites_required=False))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            signed = (await _google(client, "asha")).json()
            h, learner_id = _bearer(signed["token"]), signed["learner"]["id"]
            url = f"/api/learners/{learner_id}/profile"
            assert (await client.get(url, headers=h)).json()["complete"] is False

            too_young = {**_PROFILE, "age": 4}
            assert (await client.post(url, json=too_young, headers=h)).status_code == 422
            assert (await client.post(url, json={**_PROFILE, "age": 101}, headers=h)).status_code == 422
            no_guardian = {**_PROFILE, "consent": {"data": True, "guardian": False}}
            assert (await client.post(url, json=no_guardian, headers=h)).status_code == 422
            no_consent = {**_PROFILE, "age": 30, "consent": {"data": False, "guardian": False}}
            assert (await client.post(url, json=no_consent, headers=h)).status_code == 422
            # an adult needs no guardian
            adult = {**_PROFILE, "age": 19, "occupation": "university", "consent": {"data": True}}
            assert (await client.post(f"{url}/check", json=adult, headers=h)).json()["warning"] is None
            odd = {**_PROFILE, "age": 45}
            assert "45" in (await client.post(f"{url}/check", json=odd, headers=h)).json()["warning"]

            saved = await client.post(url, json=_PROFILE, headers=h)
            assert saved.status_code == 200, saved.text
            profile = saved.json()["profile"]
            assert profile["extracted"]["level"] == "Class 11"  # the stub's reading
            assert profile["consent"]["guardian"] is True and profile["consent"]["version"]

            me = (await client.get("/api/me", headers=h)).json()
            assert me["profile_complete"] is True and me["learner"]["label"] == "Asha"
            again = (await _google(client, "asha")).json()
            assert again["profile_complete"] is True

            # an edit is a new row; the latest wins
            await client.post(url, json={**_PROFILE, "level": "Class 12"}, headers=h)

            session_id = (await client.post("/api/sessions", json={"learner_id": learner_id},
                                            headers=h)).json()["session_id"]
        async with websockets.connect(f"{live.ws}/api/sessions/{session_id}/chat",
                                      subprotocols=["versa", signed["token"]]) as ws:
            await _turn(ws, {"type": "message", "text": "explain projectile motion"})
        answer_prompt = [p for p in llm.prompts if p.startswith("FINAL:ANSWER")][-1]
        assert "Who this student is" in answer_prompt and "CBSE" in answer_prompt
        assert "Age: 16" in answer_prompt
        assess_prompt = [p for p in llm.prompts if p.startswith("ASSESS:BRANCH")][-1]
        assert "Who this student is" in assess_prompt

        async with clean_pool.acquire() as conn:
            assert await conn.fetchval("SELECT count(*) FROM learner_profiles") == 2
            extraction = await conn.fetchrow("SELECT * FROM profile_extractions ORDER BY created_at LIMIT 1")
            assert extraction["input_json"]["prompt"].startswith("PROFILE:EXTRACT")
            assert extraction["error"] is None
    finally:
        await _stop(live)


@pytest.mark.asyncio(loop_scope="session")
async def test_an_age_that_doesnt_fit_is_left_out_and_a_failed_reading_keeps_the_answers(
    clean_pool, embedding_client
):
    misfit = json.dumps({"stage": "undergraduate", "education_system": None, "level": "Year 1 B.Sc",
                         "location": "Texas, USA", "institution": None, "field": "Biology",
                         "subjects": ["Biology"], "working_towards": [], "starting_point": None,
                         "age_fits_stage": False, "note": None})
    llm = _llm(**{"PROFILE:EXTRACT": misfit})
    live = await _start(clean_pool, llm, embedding_client, _auth(invites_required=False))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            signed = (await _google(client, "tex")).json()
            h, learner_id = _bearer(signed["token"]), signed["learner"]["id"]
            body = {**_PROFILE, "age": 99, "occupation": "university", "country": "USA", "region": "Texas",
                    "curriculum": None, "level": "Year 1", "course": "B.Sc Biology",
                    "consent": {"data": True}}
            assert (await client.post(f"/api/learners/{learner_id}/profile", json=body, headers=h)).status_code == 200
        background = await profiles_module.load_background(clean_pool, UUID(learner_id), noun="student")
        assert "Year 1 B.Sc" in background and "Age: 99" not in background
        assert "doesn't fit" in background
    finally:
        await _stop(live)

    broken = _llm(**{"PROFILE:EXTRACT": "not json"})
    live = await _start(clean_pool, broken, embedding_client, _auth(invites_required=False))
    try:
        async with httpx.AsyncClient(base_url=live.http) as client:
            signed = (await _google(client, "kept")).json()
            h, learner_id = _bearer(signed["token"]), signed["learner"]["id"]
            r = await client.post(f"/api/learners/{learner_id}/profile", json=_PROFILE, headers=h)
            assert r.status_code == 200 and r.json()["profile"]["extracted"] is None
        background = await profiles_module.load_background(clean_pool, UUID(learner_id))
        assert "Class 11" in background and "CBSE" in background
    finally:
        await _stop(live)


# ------------------------------------------------------------------ invariant 18


def test_accounts_and_profiles_never_delete_or_update():
    for module in (accounts_module, profiles_module):
        path = Path(module.__file__)
        for node in _string_literals(path):
            assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
            assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_accounts_migration_has_no_delete_or_update():
    path = Path(accounts_module.__file__).resolve().parent / "migrations" / "083_accounts.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_account_and_profile_stores_have_no_removal_methods():
    for cls in (accounts_module.AccountStore, profiles_module.ProfileStore):
        for name in dir(cls):
            assert not name.lower().startswith(("delete", "remove", "update", "set_")), f"{cls.__name__}.{name}"
