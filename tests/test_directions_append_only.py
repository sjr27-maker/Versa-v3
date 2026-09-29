"""Directions (directions.py, migration 078) are append-only (CLAUDE.md
invariant 14), same AST-based scan as every other store."""

import re
from pathlib import Path

import versa.directions as directions_module
from tests.test_rooms_append_only import _string_literals


def test_directions_module_never_deletes_or_updates():
    path = Path(directions_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_directions_migrations_have_no_delete_or_update():
    base = Path(directions_module.__file__).resolve().parent / "migrations"
    for name in ("078_directions.sql", "079_direction_presentation.sql", "086_card_library.sql", "087_compass.sql",
                 "088_direction_misses.sql", "089_direction_miss_readings.sql"):
        code = "\n".join(line.split("--", 1)[0] for line in (base / name).read_text(encoding="utf-8").splitlines())
        assert not re.search(r"\bDELETE\b", code, re.IGNORECASE), name
        assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE), name


def test_direction_store_has_no_removal_methods():
    for name in dir(directions_module.DirectionStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name
