"""Rooms (rooms/, migration rooms_001_rooms.sql) are append-only (CLAUDE.md
invariant 12), same AST-based scan as every other store: no DELETE and no
UPDATE anywhere -- done tasks and answered options are derived from event
rows, never stored."""

import ast
import re
from pathlib import Path

import versa.rooms.hub as hub_module
import versa.rooms.nodes as nodes_module
import versa.rooms.router as router_module
import versa.rooms.store as store_module

_MIGRATIONS = ("rooms_001_rooms.sql",)


def _string_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(parent, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not node.name.lower().startswith(("delete", "remove")), (
                f"{path.name}: function {node.name!r} looks like a removal method"
            )


def test_room_modules_never_delete_or_update():
    delete_kw = re.compile(r"\bDELETE\b", re.IGNORECASE)
    update_kw = re.compile(r"\bUPDATE\s+\w+\s+SET\b", re.IGNORECASE)
    for module in (store_module, hub_module, nodes_module, router_module):
        path = Path(module.__file__)
        for node in _string_literals(path):
            assert not delete_kw.search(node.value), f"{path.name}:{node.lineno} contains DELETE"
            assert not update_kw.search(node.value), f"{path.name}:{node.lineno} contains UPDATE"


def test_room_migrations_have_no_delete_or_update():
    base = Path(store_module.__file__).resolve().parent.parent / "migrations"
    for name in _MIGRATIONS:
        code = "\n".join(
            line.split("--", 1)[0] for line in (base / name).read_text(encoding="utf-8").splitlines()
        )
        assert not re.search(r"\bDELETE\b", code, re.IGNORECASE), name
        assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE), name


def test_room_store_has_no_removal_methods():
    for name in dir(store_module.RoomStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name
