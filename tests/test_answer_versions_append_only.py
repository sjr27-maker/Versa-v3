import ast
import re
from pathlib import Path

import versa.answer_versions as answer_versions_module


def test_answer_versions_module_has_no_delete_or_update():
    """AnswerVersionStore must be append-only (CLAUDE.md's established
    pattern for every store): a regeneration adds a version, it never
    changes or removes one. Same AST-based scan as the other stores."""
    source = Path(answer_versions_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstring_nodes: set[int] = set()
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(parent, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))

    forbidden = re.compile(r"\bDELETE\b|\bUPDATE\s+answer_versions\b", re.IGNORECASE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
            assert not forbidden.search(node.value), (
                f"string literal at line {node.lineno} deletes or updates — answer_versions is append-only"
            )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert not node.name.lower().startswith(("delete", "remove")), (
                f"function {node.name!r} at line {node.lineno} looks like a removal method"
            )


def test_answer_versions_migration_has_no_delete():
    path = (
        Path(answer_versions_module.__file__).resolve().parent
        / "migrations"
        / "071_answer_versions_and_knob_levels.sql"
    )
    code_only = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code_only, re.IGNORECASE)
