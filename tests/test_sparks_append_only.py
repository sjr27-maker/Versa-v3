"""Sparks (sparks.py, migration 081) are an append-only ledger (CLAUDE.md
invariant 16), same AST-based scan as every other store: no DELETE and no
UPDATE anywhere -- a balance is derived from events, never stored, and a
refund is a new event, never an edit of the spend."""

import re
from pathlib import Path

import versa.sparks as sparks_module
from tests.test_rooms_append_only import _string_literals


def test_sparks_module_never_deletes_or_updates():
    path = Path(sparks_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_sparks_migration_has_no_delete_or_update():
    path = Path(sparks_module.__file__).resolve().parent / "migrations" / "081_sparks.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_spark_store_has_no_removal_methods():
    for name in dir(sparks_module.SparkStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name
