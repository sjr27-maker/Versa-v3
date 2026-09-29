"""Accounts: who is signed in, and what they may touch.

Versa used to take any name as a sign-in and trust whatever learner id a
request carried. This module replaces that:

- Sign-in. The app signs the person in with Firebase (Google, or email and
  password) and hands the Firebase ID token to `POST /api/auth/firebase`.
  The server verifies it against Google's keys for FIREBASE_PROJECT_ID and
  answers with a Versa session token. The two dev testers (VERSA_DEV_LOGINS,
  "sooraj,adithya" on a local server) can instead sign in by name through
  `POST /api/auth/dev` -- on a deployed server only with VERSA_DEV_LOGIN_CODE.
- Invites. Versa is invite-only: the FIRST sign-in of a new account needs an
  invite code (`versa invite create`), unless VERSA_INVITES=off. People who
  already have an account never need one again.
- The guard. Every /api route except health, sign-in and the RevenueCat
  webhook needs `Authorization: Bearer <token>` (a WebSocket offers the
  subprotocols ["versa", <token>] instead, because a browser can't set
  headers on one). The guard then checks that every learner-owned thing the
  request names -- a learner, session, claim, exam, quiz, topic, lesson...,
  in the path, the query or the body -- belongs to the signed-in learner.
  Anything that doesn't is answered 404, the same as something that doesn't
  exist, so ids can't be probed.

A learner is still just `learners.id`: RevenueCat's customer id, every
session and every other table keep working unchanged. Identities, sign-ins,
invites and redemptions are append-only (CLAUDE.md invariant 18).

Session tokens are HMAC-signed with VERSA_SESSION_SECRET (`v1.<payload>.<sig>`),
valid VERSA_SESSION_DAYS (default 30). There is no server-side revocation list
yet: rotating the secret signs everyone out.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketException
from fastapi.requests import HTTPConnection
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEV_LOGINS_LOCAL_DEFAULT = ("sooraj", "adithya")
TOKEN_VERSION = "v1"
WS_SUBPROTOCOL = "versa"

# Routes anyone may call. Everything else under /api needs a session token.
PUBLIC_API_PATHS = frozenset({"/api/health", "/api/billing/revenuecat/webhook"})
PUBLIC_API_PREFIXES = ("/api/auth/",)


# ------------------------------------------------------------------ tokens


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionTokens:
    """Stateless, signed session tokens: `v1.<payload>.<signature>`."""

    def __init__(self, secret: str, *, days: int = 30, clock: Callable[[], float] = time.time) -> None:
        if len(secret) < 32:
            raise ValueError("VERSA_SESSION_SECRET must be at least 32 characters")
        self._key = secret.encode("utf-8")
        self._ttl = days * 86400
        self._clock = clock

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._key, f"{TOKEN_VERSION}.{payload}".encode(), hashlib.sha256).digest())

    def issue(self, learner_id: UUID, *, method: str) -> str:
        now = int(self._clock())
        payload = _b64(json.dumps(
            {"sub": str(learner_id), "m": method, "iat": now, "exp": now + self._ttl},
            separators=(",", ":"),
        ).encode())
        return f"{TOKEN_VERSION}.{payload}.{self._sign(payload)}"

    def verify(self, token: str) -> UUID | None:
        """The learner a token was issued to, or None if it is forged,
        malformed or expired."""
        try:
            version, payload, signature = token.split(".")
        except ValueError:
            return None
        if version != TOKEN_VERSION or not hmac.compare_digest(signature, self._sign(payload)):
            return None
        try:
            claims = json.loads(_unb64(payload))
            if int(claims["exp"]) < self._clock():
                return None
            return UUID(claims["sub"])
        except (ValueError, KeyError, TypeError):
            return None


# ------------------------------------------------------------------ firebase


@dataclass
class FirebaseUser:
    uid: str
    email: str | None
    email_verified: bool
    name: str | None
    sign_in_method: str | None  # "google.com", "password", ...


class InvalidIdToken(Exception):
    pass


FirebaseVerifier = Callable[[str], Awaitable[FirebaseUser]]


def firebase_verifier(project_id: str) -> FirebaseVerifier:
    """Verifies a Firebase ID token's signature, audience, issuer and expiry
    against Google's published keys (google-auth), off the event loop."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    transport = google_requests.Request()
    issuer = f"https://securetoken.google.com/{project_id}"

    def _verify(token: str) -> FirebaseUser:
        try:
            claims = google_id_token.verify_firebase_token(token, transport, audience=project_id)
        except Exception as exc:  # noqa: BLE001 -- any failure means "not signed in"
            raise InvalidIdToken(str(exc)) from None
        if not claims or claims.get("iss") != issuer or not claims.get("sub"):
            raise InvalidIdToken("wrong issuer or subject")
        return FirebaseUser(
            uid=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=bool(claims.get("email_verified")),
            name=claims.get("name"),
            sign_in_method=(claims.get("firebase") or {}).get("sign_in_provider"),
        )

    async def verify(token: str) -> FirebaseUser:
        return await asyncio.to_thread(_verify, token)

    return verify


# ------------------------------------------------------------------ config


@dataclass
class Auth:
    """Everything the sign-in routes and the guard need. `create_app(auth=None)`
    (the test suite, `versa serve` with VERSA_AUTH=off on a laptop) runs with
    no sign-in at all, exactly as before this module existed."""

    tokens: SessionTokens
    verify_firebase: FirebaseVerifier | None = None
    dev_logins: frozenset[str] = frozenset()
    dev_login_code: str | None = None
    invites_required: bool = True
    firebase_project_id: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def google(self) -> bool:
        return self.verify_firebase is not None

    def public_config(self) -> dict:
        """What the app needs to know to draw the sign-in screen."""
        return {
            "required": True,
            "firebase": self.google,
            "dev": bool(self.dev_logins),
            "dev_code": self.dev_login_code is not None,
            "invites": self.invites_required,
        }

    @classmethod
    def from_env(cls, *, local: bool) -> Auth | None:
        """None when VERSA_AUTH=off. `local` is whether the server only
        listens on this machine: a deployed server must be given its secret,
        and gets no name-only sign-in without VERSA_DEV_LOGIN_CODE."""
        if os.environ.get("VERSA_AUTH", "on").strip().lower() == "off":
            return None
        notes: list[str] = []
        secret = os.environ.get("VERSA_SESSION_SECRET", "").strip()
        if not secret:
            if not local:
                raise SystemExit("VERSA_SESSION_SECRET must be set on a server that isn't local-only")
            secret = secrets.token_urlsafe(48)
            notes.append("no VERSA_SESSION_SECRET: sign-ins last until this server restarts")
        days = int(os.environ.get("VERSA_SESSION_DAYS", "30"))
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "").strip() or None
        dev_code = os.environ.get("VERSA_DEV_LOGIN_CODE", "").strip() or None
        raw_dev = os.environ.get("VERSA_DEV_LOGINS")
        if raw_dev is None:
            dev = DEV_LOGINS_LOCAL_DEFAULT if local else ()
        else:
            dev = tuple(n.strip().lower() for n in raw_dev.split(",") if n.strip())
        if dev and not local and dev_code is None:
            notes.append("dev sign-in is off: a deployed server needs VERSA_DEV_LOGIN_CODE for it")
            dev = ()
        if project_id is None:
            notes.append("no FIREBASE_PROJECT_ID: Google / email sign-in is off")
        return cls(
            tokens=SessionTokens(secret, days=days),
            verify_firebase=firebase_verifier(project_id) if project_id else None,
            dev_logins=frozenset(dev),
            dev_login_code=dev_code,
            invites_required=os.environ.get("VERSA_INVITES", "on").strip().lower() != "off",
            firebase_project_id=project_id,
            notes=notes,
        )


# ------------------------------------------------------------------ store


def new_invite_code() -> str:
    """Eight characters, no look-alikes (0/O, 1/I/L), easy to read out."""
    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def normalise_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


class InviteProblem(Exception):
    """Why an invite code can't let a new account in (shown to the person)."""


@dataclass
class InviteRow:
    id: UUID
    code: str
    note: str | None
    max_uses: int | None
    expires_at: datetime | None
    created_at: datetime
    uses: int
    revoked: bool


class AccountStore:
    """Identities, sign-ins, invites. Append-only (invariant 18): no method
    here deletes or updates anything."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_identity(self, provider: str, subject: str) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "SELECT * FROM learner_identities WHERE provider = $1 AND subject = $2", provider, subject
        )

    async def add_identity(
        self, conn: asyncpg.Connection, *, learner_id: UUID, provider: str, subject: str,
        sign_in_method: str | None = None, email: str | None = None,
        email_verified: bool | None = None, display_name: str | None = None,
    ) -> UUID:
        identity_id = uuid4()
        await conn.execute(
            "INSERT INTO learner_identities (id, learner_id, provider, subject, sign_in_method, "
            "email, email_verified, display_name) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            identity_id, learner_id, provider, subject, sign_in_method, email, email_verified, display_name,
        )
        return identity_id

    async def record_sign_in(self, learner_id: UUID, identity_id: UUID) -> None:
        await self._pool.execute(
            "INSERT INTO learner_sign_ins (id, learner_id, identity_id) VALUES ($1, $2, $3)",
            uuid4(), learner_id, identity_id,
        )

    async def identity_for_learner(self, learner_id: UUID) -> asyncpg.Record | None:
        return await self._pool.fetchrow(
            "SELECT * FROM learner_identities WHERE learner_id = $1 ORDER BY created_at LIMIT 1", learner_id
        )

    # -------------------------------------------------------- invites

    async def create_invite(
        self, *, max_uses: int | None = None, note: str | None = None,
        expires_at: datetime | None = None, created_by: str | None = None, code: str | None = None,
    ) -> InviteRow:
        code = normalise_code(code) if code else new_invite_code()
        invite_id = uuid4()
        await self._pool.execute(
            "INSERT INTO invites (id, code, note, max_uses, expires_at, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            invite_id, code, note, max_uses, expires_at, created_by,
        )
        return await self.get_invite(code)

    async def revoke_invite(self, code: str, reason: str | None = None) -> bool:
        invite = await self.get_invite(code)
        if invite is None:
            return False
        await self._pool.execute(
            "INSERT INTO invite_revocations (id, invite_id, reason) VALUES ($1, $2, $3) "
            "ON CONFLICT (invite_id) DO NOTHING",
            uuid4(), invite.id, reason,
        )
        return True

    _INVITE_SELECT = (
        "SELECT i.*, (SELECT count(*) FROM invite_redemptions r WHERE r.invite_id = i.id) AS uses, "
        "EXISTS (SELECT 1 FROM invite_revocations v WHERE v.invite_id = i.id) AS revoked FROM invites i"
    )

    @staticmethod
    def _invite(row: asyncpg.Record) -> InviteRow:
        return InviteRow(
            id=row["id"], code=row["code"], note=row["note"], max_uses=row["max_uses"],
            expires_at=row["expires_at"], created_at=row["created_at"], uses=row["uses"],
            revoked=row["revoked"],
        )

    async def get_invite(self, code: str) -> InviteRow | None:
        row = await self._pool.fetchrow(f"{self._INVITE_SELECT} WHERE i.code = $1", normalise_code(code))
        return self._invite(row) if row is not None else None

    async def list_invites(self) -> list[InviteRow]:
        rows = await self._pool.fetch(f"{self._INVITE_SELECT} ORDER BY i.created_at DESC")
        return [self._invite(r) for r in rows]

    @staticmethod
    def invite_problem(invite: InviteRow | None, now: datetime) -> str | None:
        if invite is None:
            return "That invite code isn't valid."
        if invite.revoked:
            return "That invite code has been withdrawn."
        if invite.expires_at is not None and invite.expires_at <= now:
            return "That invite code has expired."
        if invite.max_uses is not None and invite.uses >= invite.max_uses:
            return "That invite code has already been used up."
        return None

    async def create_learner_with_identity(
        self, *, invite_code: str | None, invites_required: bool, display_name: str | None,
        provider: str, subject: str, sign_in_method: str | None, email: str | None,
        email_verified: bool | None,
    ) -> tuple[UUID, UUID]:
        """A brand-new learner, its identity and (when invites are on) the
        redemption of the code that let it in -- all or nothing. Uses are
        counted under a lock on the invite, so a code's last use can't be
        taken twice."""
        async with self._pool.acquire() as conn, conn.transaction():
            invite_id: UUID | None = None
            if invites_required or invite_code:
                code = normalise_code(invite_code or "")
                if not code:
                    raise InviteProblem("Versa is invite-only for now: enter your invite code.")
                row = await conn.fetchrow("SELECT id FROM invites WHERE code = $1", code)
                if row is not None:
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"invite:{row['id']}")
                full = await conn.fetchrow(f"{self._INVITE_SELECT} WHERE i.code = $1", code)
                invite = self._invite(full) if full is not None else None
                problem = self.invite_problem(invite, datetime.now(UTC))
                if problem is not None:
                    raise InviteProblem(problem)
                invite_id = invite.id
            learner_id = uuid4()
            await conn.execute(
                "INSERT INTO learners (id, display_name, created_at) VALUES ($1, $2, $3)",
                learner_id, display_name, datetime.now(UTC),
            )
            identity_id = await self.add_identity(
                conn, learner_id=learner_id, provider=provider, subject=subject,
                sign_in_method=sign_in_method, email=email, email_verified=email_verified,
                display_name=display_name,
            )
            if invite_id is not None:
                await conn.execute(
                    "INSERT INTO invite_redemptions (id, invite_id, learner_id) VALUES ($1, $2, $3)",
                    uuid4(), invite_id, learner_id,
                )
        return learner_id, identity_id

    async def dev_learner(self, name: str) -> tuple[UUID, UUID, str]:
        """The dev tester's learner: the one that already carries their name
        (so everything they tested before sign-in existed stays theirs), or a
        new one. Returns (learner_id, identity_id, label)."""
        subject = name.lower()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"dev:{subject}")
            identity = await conn.fetchrow(
                "SELECT i.id, i.learner_id, l.label FROM learner_identities i "
                "JOIN learners l ON l.id = i.learner_id WHERE i.provider = 'dev' AND i.subject = $1",
                subject,
            )
            if identity is not None:
                return identity["learner_id"], identity["id"], identity["label"] or name
            row = await conn.fetchrow(
                "SELECT id, label FROM learners WHERE lower(label) = $1 ORDER BY created_at LIMIT 1", subject
            )
            if row is None:
                learner_id, label = uuid4(), name.capitalize()
                await conn.execute(
                    "INSERT INTO learners (id, label, created_at) VALUES ($1, $2, $3)",
                    learner_id, label, datetime.now(UTC),
                )
            else:
                learner_id, label = row["id"], row["label"]
            identity_id = await self.add_identity(
                conn, learner_id=learner_id, provider="dev", subject=subject,
                sign_in_method="dev", display_name=label,
            )
        return learner_id, identity_id, label


# ------------------------------------------------------------------ ownership

# How to find the learner that owns a thing named by a path, query or body
# field. Every learner-owned id the API accepts is listed here; a new route
# with a new kind of id must add it (tests/test_accounts.py checks that every
# path parameter in the app is either here or deliberately unowned).
OWNER_SQL: dict[str, str] = {
    "learner_id": "SELECT id FROM learners WHERE id = $1",
    "session_id": "SELECT learner_id FROM sessions WHERE id = $1",
    "claim_id": "SELECT learner_id FROM claims WHERE id = $1",
    "candidate_id": "SELECT learner_id FROM thinking_style_candidates WHERE id = $1",
    "exam_id": "SELECT learner_id FROM exams WHERE id = $1",
    "unit_id": "SELECT e.learner_id FROM exam_units u JOIN exams e ON e.id = u.exam_id WHERE u.id = $1",
    "quiz_id": "SELECT e.learner_id FROM exam_quizzes q JOIN exams e ON e.id = q.exam_id WHERE q.id = $1",
    "item_id": (
        "SELECT e.learner_id FROM exam_plan_items i JOIN exam_plans p ON p.id = i.plan_id "
        "JOIN exams e ON e.id = p.exam_id WHERE i.id = $1"
    ),
    "exploration_id": "SELECT learner_id FROM topic_explorations WHERE id = $1",
    "node_id": (
        "SELECT x.learner_id FROM topic_nodes n JOIN topic_explorations x ON x.id = n.exploration_id "
        "WHERE n.id = $1"
    ),
    "topic_id": "SELECT learner_id FROM topics WHERE id = $1",
    "lesson_id": (
        "SELECT t.learner_id FROM topic_lessons l JOIN topic_chapters c ON c.id = l.chapter_id "
        "JOIN topics t ON t.id = c.topic_id WHERE l.id = $1"
    ),
}

# Ids that are checked some other way: a review is checked against the claim
# or candidate in the same path by its handler; rooms are shared spaces with
# their own membership (a room code and a member id), walled off from
# learners (invariant 12).
UNOWNED_PARAMS = frozenset({"review_id", "code", "member_id"})

# `for_learner` fields carry the SIGNED-IN learner id for the route, so a
# learner id there must be theirs. The rest are learner-owned things.
_LEARNER_FIELDS = ("learner_id",)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="not found")


class OwnershipCheck:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def owner_of(self, field_name: str, value) -> UUID | None:
        """The owning learner, or None when the thing doesn't exist (the
        handler answers that itself) or the value isn't an id at all."""
        sql = OWNER_SQL.get(field_name)
        if sql is None:
            return None
        try:
            key = value if isinstance(value, UUID) else UUID(str(value))
        except ValueError:
            return None
        return await self._pool.fetchval(sql, key)

    async def check(self, fields: dict, learner_id: UUID) -> bool:
        for name, value in fields.items():
            if name not in OWNER_SQL or value is None:
                continue
            if name in _LEARNER_FIELDS:
                try:
                    if UUID(str(value)) != learner_id:
                        return False
                except ValueError:
                    continue
                continue
            owner = await self.owner_of(name, value)
            if owner is not None and owner != learner_id:
                return False
        return True


def bearer_token(conn: HTTPConnection) -> str | None:
    header = conn.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    if conn.scope.get("type") == "websocket":
        # ["versa", "<token>"]: the only way a browser can send one.
        offered = [p.strip() for p in conn.headers.get("sec-websocket-protocol", "").split(",")]
        if WS_SUBPROTOCOL in offered:
            rest = [p for p in offered if p and p != WS_SUBPROTOCOL]
            return rest[0] if rest else None
    return None


def ws_subprotocol(ws: WebSocket) -> str | None:
    """The subprotocol to accept a WebSocket with: a client that offered
    "versa" (and its token) must get it echoed back, or the browser drops
    the connection."""
    return WS_SUBPROTOCOL if WS_SUBPROTOCOL in ws.scope.get("subprotocols", []) else None


async def _body_fields(request: Request) -> dict:
    """Top-level fields of a JSON or form body. FastAPI has already read
    (and cached) the body by the time a dependency runs."""
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("application/json"):
            body = await request.json()
            return body if isinstance(body, dict) else {}
        if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            form = await request.form()
            return {k: v for k, v in form.items() if isinstance(v, str)}
    except (ValueError, UnicodeDecodeError):
        return {}
    return {}


def build_guard(auth: Auth | None, pool: asyncpg.Pool) -> Callable[[HTTPConnection], Awaitable[None]]:
    """The dependency every /api route runs first (see module docstring)."""
    ownership = OwnershipCheck(pool)

    async def guard(conn: HTTPConnection) -> None:
        if auth is None:
            return
        path = conn.url.path
        if not path.startswith("/api/") or path in PUBLIC_API_PATHS or path.startswith(PUBLIC_API_PREFIXES):
            return
        is_ws = conn.scope.get("type") == "websocket"
        token = bearer_token(conn)
        learner_id = auth.tokens.verify(token) if token else None
        if learner_id is None:
            if is_ws:
                raise WebSocketException(code=4401, reason="sign in first")
            raise HTTPException(status_code=401, detail="sign in first")
        conn.state.learner_id = learner_id
        fields = {**dict(conn.query_params), **conn.path_params}
        if not is_ws and isinstance(conn, Request) and conn.method in ("POST", "PUT", "PATCH"):
            fields = {**(await _body_fields(conn)), **fields}
        if not await ownership.check(fields, learner_id):
            if is_ws:
                raise WebSocketException(code=4404, reason="not found")
            raise _not_found()

    return guard


def current_learner(conn: HTTPConnection) -> UUID:
    learner_id = getattr(conn.state, "learner_id", None)
    if learner_id is None:
        raise HTTPException(status_code=401, detail="sign in first")
    return learner_id


# ------------------------------------------------------------------ routes


class FirebaseSignInIn(BaseModel):
    id_token: str = Field(min_length=10, max_length=8192)
    invite_code: str | None = Field(None, max_length=40)


class DevSignInIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    code: str | None = Field(None, max_length=200)


class SignedInLearner(BaseModel):
    id: UUID
    label: str


class SignInOut(BaseModel):
    token: str
    learner: SignedInLearner
    new_account: bool
    profile_complete: bool


def _label_for(user: FirebaseUser) -> str:
    if user.name and user.name.strip():
        return " ".join(user.name.split())[:60]
    if user.email:
        return user.email.split("@", 1)[0][:60]
    return "Learner"


def build_auth_router(
    auth: Auth,
    pool: asyncpg.Pool,
    *,
    has_profile: Callable[[UUID], Awaitable[bool]],
    display_name: Callable[[UUID], Awaitable[str | None]],
) -> APIRouter:
    router = APIRouter(prefix="/api")
    store = AccountStore(pool)

    async def signed_in(learner_id: UUID, identity_id: UUID, label: str, *, method: str,
                        new: bool) -> SignInOut:
        await store.record_sign_in(learner_id, identity_id)
        return SignInOut(
            token=auth.tokens.issue(learner_id, method=method),
            learner=SignedInLearner(id=learner_id, label=(await display_name(learner_id)) or label),
            new_account=new,
            profile_complete=await has_profile(learner_id),
        )

    @router.post("/auth/firebase", response_model=SignInOut)
    async def sign_in_firebase(body: FirebaseSignInIn) -> SignInOut:
        if auth.verify_firebase is None:
            raise HTTPException(status_code=503, detail="Google and email sign-in aren't set up on this server")
        try:
            user = await auth.verify_firebase(body.id_token)
        except InvalidIdToken:
            raise HTTPException(status_code=401, detail="That sign-in didn't check out -- try again.") from None
        label = _label_for(user)
        identity = await store.find_identity("firebase", user.uid)
        if identity is not None:
            return await signed_in(identity["learner_id"], identity["id"], label,
                                   method=user.sign_in_method or "firebase", new=False)
        try:
            learner_id, identity_id = await store.create_learner_with_identity(
                invite_code=body.invite_code, invites_required=auth.invites_required,
                display_name=label, provider="firebase", subject=user.uid,
                sign_in_method=user.sign_in_method, email=user.email, email_verified=user.email_verified,
            )
        except InviteProblem as exc:
            raise HTTPException(status_code=403, detail={"reason": "invite", "message": str(exc)}) from None
        except asyncpg.UniqueViolationError:
            # the same account's first sign-in raced itself: use the winner
            identity = await store.find_identity("firebase", user.uid)
            if identity is None:
                raise
            learner_id, identity_id = identity["learner_id"], identity["id"]
            return await signed_in(learner_id, identity_id, label,
                                   method=user.sign_in_method or "firebase", new=False)
        return await signed_in(learner_id, identity_id, label,
                               method=user.sign_in_method or "firebase", new=True)

    @router.post("/auth/dev", response_model=SignInOut)
    async def sign_in_dev(body: DevSignInIn) -> SignInOut:
        name = " ".join(body.name.split()).lower()
        if name not in auth.dev_logins:
            raise HTTPException(status_code=403, detail="Name-only sign-in is only for the Versa team's testers.")
        if auth.dev_login_code is not None and not hmac.compare_digest(
            (body.code or "").encode(), auth.dev_login_code.encode()
        ):
            raise HTTPException(status_code=403, detail="Wrong tester code.")
        learner_id, identity_id, label = await store.dev_learner(name)
        return await signed_in(learner_id, identity_id, label, method="dev", new=False)

    @router.get("/auth/invites/{code}")
    async def check_invite(code: str) -> dict:
        """Whether a code would let a new account in (the invite page and the
        app's sign-up screen ask before sending someone through Google)."""
        problem = store.invite_problem(await store.get_invite(code), datetime.now(UTC))
        return {"valid": problem is None, "message": problem}

    return router


async def create_invites(
    pool: asyncpg.Pool, *, count: int = 1, max_uses: int | None = None, note: str | None = None,
    days: int | None = None, created_by: str | None = None,
) -> list[InviteRow]:
    store = AccountStore(pool)
    expires = datetime.now(UTC) + timedelta(days=days) if days else None
    return [
        await store.create_invite(max_uses=max_uses, note=note, expires_at=expires, created_by=created_by)
        for _ in range(count)
    ]
