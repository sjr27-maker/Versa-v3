"""Exam preparation (exams.py, migration 075) is append-only (CLAUDE.md
invariant 13), same AST-based scan as every other store: no DELETE and no
UPDATE anywhere -- scores are derived from exam_answers, never stored."""

import re
from pathlib import Path

import versa.exams as exams_module
from tests.test_rooms_append_only import _string_literals

_MIGRATIONS = ("075_exams.sql", "076_exam_plans.sql")


def test_exams_module_never_deletes_or_updates():
    path = Path(exams_module.__file__)
    for node in _string_literals(path):
        assert not re.search(r"\bDELETE\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"
        assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", node.value, re.IGNORECASE), f"{path.name}:{node.lineno}"


def test_exam_migration_has_no_delete_or_update():
    base = Path(exams_module.__file__).resolve().parent / "migrations"
    for name in _MIGRATIONS:
        code = "\n".join(
            line.split("--", 1)[0] for line in (base / name).read_text(encoding="utf-8").splitlines()
        )
        assert not re.search(r"\bDELETE\b", code, re.IGNORECASE), name
        assert not re.search(r"\bUPDATE\b", code, re.IGNORECASE), name


def test_exam_store_has_no_removal_methods():
    for name in dir(exams_module.ExamStore):
        assert not name.lower().startswith(("delete", "remove", "update", "set_")), name
