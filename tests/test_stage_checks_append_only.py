"""The stage's quick-check picks (stage.py StageCheckStore, migration 080)
are append-only (CLAUDE.md invariant 15), same AST-based scan as every other
store."""

import re
from pathlib import Path

import versa.stage as stage_module
from tests.test_rooms_append_only import _string_literals


def test_stage_module_never_deletes_or_updates():
    path = Path(stage_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_stage_checks_migration_has_no_delete_or_update():
    path = Path(stage_module.__file__).resolve().parent / "migrations" / "080_stage_checks.sql"
    code = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE)


def test_stage_check_store_has_no_removal_methods():
    for name in dir(stage_module.StageCheckStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name
