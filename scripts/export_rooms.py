"""Export the rooms feature as the starting point of a new, standalone project.

    uv run python scripts/export_rooms.py ../study-rooms --package study_rooms

Writes a new directory (it must not exist yet) holding:

  src/<package>/      the rooms backend (store, nodes, hub, router) plus the
                      small slice of Versa core it runs on -- the Gemini
                      client and model tiers (llm.py, model_config.py), the
                      stage director (stage.py), PDF/link reading
                      (resources.py), the migration runner (migrate.py), and
                      two pieces lifted out of bigger modules: the topic
                      outline nodes (outline.py, from topics.py) and
                      `to_jsonable` (jsonable.py, from audit.py). Imports are
                      rewritten to the new package name. A small server.py
                      and cli.py (`<package> migrate` / `<package> serve`)
                      are generated.
  src/<package>/migrations/001_rooms.sql
  tests/              the rooms' pure-rule tests and the append-only check
  app/lib/            the Flutter rooms screens, the stage (slime) engine and
                      theme, with tiny generated stand-ins for Versa's app
                      shell (api.dart, app_state.dart, main.dart). Run
                      `flutter create .` inside app/ once to add the
                      platform folders.
  pyproject.toml, .env.example, README.md

Nothing in this repository is changed. The export is a snapshot of this
branch: re-run it to take a newer one.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "versa"
APP = ROOT / "app"

# Core modules copied whole (their only internal imports are to each other).
CORE_MODULES = ["llm.py", "model_config.py", "stage.py", "resources.py", "migrate.py"]
ROOM_MODULES = ["store.py", "nodes.py", "hub.py", "router.py"]

# Flutter files copied as they are (paths under app/lib).
APP_FILES = [
    "theme.dart",
    "config.dart",
    "composer_draft.dart",
    "widgets/composer.dart",
    "topic/topic_widgets.dart",
    "topic/topic_models.dart",
    "stage/engine.dart",
    "stage/painter.dart",
    "stage/script.dart",
    "stage/skits.dart",
    "stage/stage_view.dart",
]


def rewrite_imports(text: str, pkg: str) -> str:
    """versa.* -> <pkg>.* with the lifted pieces pointing at their new homes.
    Most specific first."""
    for old, new in [
        ("from versa.rooms.", f"from {pkg}."),
        ("import versa.rooms.", f"import {pkg}."),
        ("from versa.topics import", f"from {pkg}.outline import"),
        ("from versa.audit import", f"from {pkg}.jsonable import"),
        ("from versa import resources as", f"from {pkg} import resources as"),
        ("from versa.", f"from {pkg}."),
        ("import versa.", f"import {pkg}."),
    ]:
        text = text.replace(old, new)
    return text


def extract(path: Path, names: list[str]) -> str:
    """The source of the named top-level definitions/assignments in `path`,
    in file order."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = set(names)
    chunks, found = [], set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        else:
            continue
        if name in wanted:
            chunks.append(ast.get_source_segment(source, node, padded=True))
            found.add(name)
    missing = wanted - found
    if missing:
        sys.exit(f"export: {path.name} no longer defines {sorted(missing)} -- update scripts/export_rooms.py")
    return "\n\n\n".join(chunks) + "\n"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ backend


def export_backend(out: Path, pkg: str, source_note: str) -> None:
    dest = out / "src" / pkg
    for name in CORE_MODULES:
        write(dest / name, rewrite_imports((SRC / name).read_text(encoding="utf-8"), pkg))
    for name in ROOM_MODULES:
        write(dest / name, rewrite_imports((SRC / "rooms" / name).read_text(encoding="utf-8"), pkg))

    write(dest / "outline.py", (
        '"""Topic outlining: a keyword -> its main parts, or a resource -> its\n'
        f"outline. Lifted from Versa's topics.py ({source_note}).\"\"\"\n\n"
        "from __future__ import annotations\n\nimport json\nimport re\n\n"
        f"from {pkg}.llm import LLMClient\n\n"
        + extract(SRC / "topics.py", [
            "_ROOT_BRANCHES", "_EXPAND_BRANCHES", "_json_object", "_clip",
            "_parse_branch_items", "GenerateBranches", "OutlineResource",
        ])
    ))
    write(dest / "jsonable.py", (
        f'"""Lifted from Versa\'s audit.py ({source_note})."""\n\n'
        "from __future__ import annotations\n\nfrom datetime import datetime\nfrom typing import Any\n"
        "from uuid import UUID\n\nfrom pydantic import BaseModel\n\n\n"
        + extract(SRC / "audit.py", ["to_jsonable"])
    ))
    write(dest / "db.py", DB_PY)
    write(dest / "server.py", SERVER_PY.format(pkg=pkg))
    write(dest / "cli.py", CLI_PY.format(pkg=pkg))
    rooms_doc = ast.get_docstring(ast.parse((SRC / "rooms" / "__init__.py").read_text(encoding="utf-8"))) or ""
    write(dest / "__init__.py", f'"""{rooms_doc}\n"""\n')
    shutil.copyfile(SRC / "migrations" / "rooms_001_rooms.sql", _mk(dest / "migrations" / "001_rooms.sql"))

    # Tests: the pure rules from tests/test_rooms.py (the end-to-end half
    # needs Versa's server fixtures) and the append-only scan.
    rules = (ROOT / "tests" / "test_rooms.py").read_text(encoding="utf-8")
    rules = rules.split("# ------------------------------------------------------------------ end to end")[0]
    rules = re.sub(r"^(import httpx|import pytest_asyncio|import websockets|import asyncio|"
                   r"from tests\..*|from versa\.llm import .*)\n", "", rules, flags=re.MULTILINE)
    write(out / "tests" / "test_rules.py", rewrite_imports(rules, pkg))
    append_only = (ROOT / "tests" / "test_rooms_append_only.py").read_text(encoding="utf-8")
    append_only = append_only.replace('("rooms_001_rooms.sql",)', '("001_rooms.sql",)')
    append_only = append_only.replace(".parent.parent / \"migrations\"", ".parent / \"migrations\"")
    write(out / "tests" / "test_append_only.py", rewrite_imports(append_only, pkg))
    write(out / "tests" / "__init__.py", "")


def _mk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


DB_PY = '''"""Pool helper: JSONB columns in and out as Python values."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, **kwargs: Any) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, init=_init_connection, **kwargs)
'''

SERVER_PY = '''"""The HTTP + WebSocket app: the rooms routes, plus the built Flutter web
app at "/" if there is one. No authentication: local / LAN use only."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from {pkg}.hub import RoomHub
from {pkg}.llm import LLMClient
from {pkg}.router import build_rooms_router

LOCAL_ORIGIN_REGEX = r"https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?"


def create_app(
    pool: asyncpg.Pool,
    llm: LLMClient,
    *,
    web_dir: Path | str | None = None,
    llm_mode: Literal["live", "stub"] = "live",
) -> FastAPI:
    app = FastAPI(title="Rooms", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware, allow_origin_regex=LOCAL_ORIGIN_REGEX, allow_methods=["*"], allow_headers=["*"],
    )
    hub = RoomHub(pool, llm)
    app.state.room_hub = hub

    @app.get("/api/health")
    async def health() -> dict:
        return {{"status": "ok", "llm": llm_mode}}

    app.include_router(build_rooms_router(hub))
    if web_dir is not None and Path(web_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return app
'''

CLI_PY = '''"""`{pkg} migrate` applies the schema; `{pkg} serve` runs the server."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

from dotenv import load_dotenv

from {pkg} import migrate as _migrate
from {pkg}.db import create_pool
from {pkg}.llm import StubLLMClient, build_tier_clients


def _database_url() -> str:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("error: DATABASE_URL not set (check .env)")
    return url


def _llm(stub: bool):
    if stub:
        return StubLLMClient()
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        sys.exit("error: GEMINI_API_KEY not set (check .env), or pass --stub")
    return build_tier_clients(key).fast


def _lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; picks the outbound interface
            return s.getsockname()[0]
    except OSError:
        return None


async def _migrate_cmd() -> None:
    pool = await create_pool(_database_url(), min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            done = await _migrate.apply_all(conn)
        print("applied:", ", ".join(done) if done else "nothing (up to date)")
    finally:
        await pool.close()


async def _serve(host: str, port: int, stub: bool, web_dir: str | None) -> None:
    import uvicorn

    from {pkg}.server import create_app

    pool = await create_pool(_database_url(), min_size=1, max_size=8)
    try:
        async with pool.acquire() as conn:
            _applied, pending = await _migrate.status(conn)
        if pending:
            sys.exit(f"error: {{len(pending)}} pending migration(s) -- run `{pkg} migrate` first")
        web = Path(web_dir) if web_dir else Path("app") / "build" / "web"
        app = create_app(pool, _llm(stub), web_dir=web, llm_mode="stub" if stub else "live")
        print(f"{pkg}: http://localhost:{{port}}/")
        lan = _lan_address() if host == "0.0.0.0" else None
        if lan:
            print(f"{pkg}: other devices on this network: http://{{lan}}:{{port}}/")
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).serve()
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="{pkg}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply pending migrations")
    serve = sub.add_parser("serve", help="run the API (and the built web app at /)")
    serve.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to let other devices on your network in")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--stub", action="store_true", help="no model calls (StubLLMClient)")
    serve.add_argument("--web-dir", default=None)
    args = parser.parse_args()
    if args.command == "migrate":
        asyncio.run(_migrate_cmd())
    else:
        asyncio.run(_serve(args.host, args.port, args.stub, args.web_dir))
'''


def export_project_files(out: Path, pkg: str, source_note: str) -> None:
    write(out / "pyproject.toml", f'''[project]
name = "{pkg.replace("_", "-")}"
version = "0.1.0"
description = "Study rooms: a group chat where an AI tutor is one more member."
requires-python = ">=3.12"
dependencies = [
    "asyncpg>=0.31.0",
    "fastapi>=0.141.1",
    "google-genai>=2.20.0",
    "httpx>=0.28.1",
    "pydantic>=2.13.4",
    "pypdf>=6.19.0",
    "python-dotenv>=1.2.3",
    "python-multipart>=0.0.32",
    "uvicorn>=0.53.0",
]

[project.scripts]
{pkg.replace("_", "-")} = "{pkg}.cli:main"

[build-system]
requires = ["uv_build>=0.12.5,<0.13.0"]
build-backend = "uv_build"

[dependency-groups]
dev = ["pytest>=9.1.1", "pytest-asyncio>=1.4.0"]
''')
    write(out / ".env.example", (
        "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/rooms\n"
        "GEMINI_API_KEY=\n"
        f"# GEMINI_MODEL_FAST=...   (see src/{pkg}/model_config.py)\n"
    ))
    write(out / ".gitignore", ".env\n.venv/\n__pycache__/\napp/build/\napp/.dart_tool/\n")
    cmd = pkg.replace("_", "-")
    write(out / "README.md", f"""# {pkg}

Study rooms: a WhatsApp-style group chat where people learn a topic together
and an AI tutor (Versa) is one more member -- it gives each person a task,
steps in when it helps, and otherwise stays quiet.

Exported from Versa ({source_note}) by `scripts/export_rooms.py`. See
`src/{pkg}/__init__.py` for how it works and `src/{pkg}/hub.py` for the live
protocol.

## Run it

    cp .env.example .env            # set DATABASE_URL (+ GEMINI_API_KEY)
    uv sync
    uv run {cmd} migrate
    uv run {cmd} serve --stub       # or without --stub for real Gemini calls

The app:

    cd app
    flutter create . --project-name {pkg}_app   # once: adds android/, web/, windows/, ...
    flutter pub get
    flutter build web               # then `{cmd} serve` serves it at http://localhost:8000/

Other devices on the same network: `uv run {cmd} serve --host 0.0.0.0` and
open the address it prints. No logins: a name and a room code get you in.

## What came from where

- `store.py`, `nodes.py`, `hub.py`, `router.py`, `migrations/001_rooms.sql`: the rooms feature.
- `llm.py`, `model_config.py`: Versa's Gemini client with model tiers, retries and a
  stub. It still carries the response schemas/stub answers of Versa's other nodes;
  only `ROOM:DIRECT`, `TOPIC:BRANCHES`, `TOPIC:OUTLINE` and `STAGE:DIRECT` are used here.
- `stage.py`: the stage director (the slime acting out explanations).
- `resources.py`: reading PDFs and web links (with SSRF guards).
- `outline.py`, `jsonable.py`: lifted out of Versa's topics.py / audit.py.
- `app/lib/room/`, `app/lib/stage/`: the Flutter screens and stage engine.
  `api.dart`, `app_state.dart` and `main.dart` are small stand-ins for Versa's app shell.

## Rules kept from Versa

Everything is append-only (no UPDATE / DELETE: `tests/test_append_only.py`),
and every model call is recorded with its input and output in `room_node_calls`.
""")


# ------------------------------------------------------------------ Flutter app


def export_app(out: Path, pkg: str) -> None:
    lib = out / "app" / "lib"
    for rel in APP_FILES:
        _mk(lib / rel)
        shutil.copyfile(APP / "lib" / rel, lib / rel)
    shutil.copytree(APP / "lib" / "room", lib / "room")
    shutil.copyfile(APP / "analysis_options.yaml", out / "app" / "analysis_options.yaml")
    write(lib / "api.dart", APP_API)
    write(lib / "app_state.dart", APP_STATE)
    write(lib / "main.dart", APP_MAIN)
    pubspec = (APP / "pubspec.yaml").read_text(encoding="utf-8")
    pubspec = re.sub(r"^name: .*$", f"name: {pkg}_app", pubspec, count=1, flags=re.MULTILINE)
    pubspec = re.sub(r"^description: .*$", 'description: "Study rooms."', pubspec, count=1, flags=re.MULTILINE)
    write(out / "app" / "pubspec.yaml", pubspec)


APP_API = """import 'package:http/http.dart' as http;

/// Stand-in for Versa's REST client: the rooms screens only need the base
/// URL, the shared HTTP client and ApiException.
class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

class VersaApi {
  VersaApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  final String baseUrl;
  final http.Client _http;
  http.Client get httpClient => _http;
}
"""

APP_STATE = """import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';

/// Who is using this device. There are no accounts: the name is the default
/// display name in rooms, and the id only keys this device's rooms list.
class Learner {
  const Learner({required this.id, required this.label});
  final String id;
  final String label;
}

/// Stand-in for Versa's AppState: the API client, saved settings, and who
/// is signed in on this device.
class AppState extends ChangeNotifier {
  // ignore: prefer_initializing_formals -- a private field can't be a named formal
  AppState({required this.api, SharedPreferences? prefs}) : _prefs = prefs;

  final VersaApi api;
  SharedPreferences? _prefs;
  SharedPreferences? get prefs => _prefs;

  static const _kName = 'name';
  Learner? learner;
  bool loaded = false;

  Future<void> load() async {
    _prefs ??= await SharedPreferences.getInstance();
    final name = _prefs!.getString(_kName);
    if (name != null && name.isNotEmpty) learner = Learner(id: name.toLowerCase(), label: name);
    loaded = true;
    notifyListeners();
  }

  Future<void> signIn(String name) async {
    final clean = name.trim();
    learner = Learner(id: clean.toLowerCase(), label: clean);
    await _prefs!.setString(_kName, clean);
    notifyListeners();
  }

  Future<void> signOut() async {
    learner = null;
    await _prefs!.remove(_kName);
    notifyListeners();
  }
}

/// Stand-in for Versa's ShellState: the rooms list's back button signs out.
class ShellState extends ChangeNotifier {
  ShellState(this.app);
  final AppState app;
  void closeRooms() => app.signOut();
}
"""

APP_MAIN = """import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api.dart';
import 'app_state.dart';
import 'config.dart';
import 'room/rooms_root.dart';
import 'theme.dart';

void main() => runApp(const RoomsApp());

class RoomsApp extends StatefulWidget {
  const RoomsApp({super.key});

  @override
  State<RoomsApp> createState() => _RoomsAppState();
}

class _RoomsAppState extends State<RoomsApp> {
  late final AppState _app = AppState(api: VersaApi(defaultApiBase()))..load();

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AppState>.value(value: _app),
        ChangeNotifierProvider<ShellState>(create: (_) => ShellState(_app)),
      ],
      child: MaterialApp(
        title: 'Study rooms',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(),
        home: Consumer<AppState>(builder: (context, app, _) {
          if (!app.loaded) return const Scaffold(body: Center(child: CircularProgressIndicator()));
          if (app.learner == null) return const _NameScreen();
          return Scaffold(body: SafeArea(child: RoomsRoot(key: ValueKey(app.learner!.id))));
        }),
      ),
    );
  }
}

class _NameScreen extends StatefulWidget {
  const _NameScreen();

  @override
  State<_NameScreen> createState() => _NameScreenState();
}

class _NameScreenState extends State<_NameScreen> {
  final _name = TextEditingController();

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  void _go() {
    if (_name.text.trim().isNotEmpty) context.read<AppState>().signIn(_name.text);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Paper.page,
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 380),
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Study rooms', style: serif(30)),
              const SizedBox(height: 16),
              TextField(
                controller: _name,
                autofocus: true,
                onSubmitted: (_) => _go(),
                decoration: const InputDecoration(labelText: 'Your name'),
              ),
              const SizedBox(height: 16),
              FilledButton(onPressed: _go, child: const Text('Continue')),
            ]),
          ),
        ),
      ),
    );
  }
}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", help="directory to create (must not exist yet)")
    parser.add_argument("--package", default="study_rooms", help="Python package name (default study_rooms)")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.package):
        sys.exit("export: --package must be a lowercase Python identifier")
    out = Path(args.out).resolve()
    if out.exists():
        sys.exit(f"export: {out} already exists -- pick a new directory")
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        source_note = f"branch {branch} at {commit}"
    except (OSError, subprocess.CalledProcessError):
        source_note = "branch experiment/rooms"
    export_backend(out, args.package, source_note)
    export_project_files(out, args.package, source_note)
    export_app(out, args.package)
    print(f"exported to {out}")
    print("next: see README.md there (uv sync; migrate; serve; flutter create . in app/)")


if __name__ == "__main__":
    main()
