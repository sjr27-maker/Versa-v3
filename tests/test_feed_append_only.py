import ast
import re
from pathlib import Path

import versa.feed as feed_module


def test_feed_module_has_no_delete():
    """FeedGenerationStore must be append-only (CLAUDE.md's established
    pattern for every store). Same AST-based scan as the other stores."""
    source = Path(feed_module.__file__).read_text(encoding="utf-8")
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

    delete_kw = re.compile(r"\bDELETE\b", re.IGNORECASE)
    update_kw = re.compile(r"\bUPDATE\s+feed_generations\b", re.IGNORECASE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
            assert not delete_kw.search(node.value), (
                f"string literal at line {node.lineno} contains DELETE — feed.py must be append-only"
            )
            assert not update_kw.search(node.value), (
                f"string literal at line {node.lineno} updates feed_generations — rows are never changed"
            )

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lname = node.name.lower()
            assert not lname.startswith(("delete", "remove")), (
                f"function {node.name!r} at line {node.lineno} looks like a removal method"
            )


def test_feed_migration_has_no_delete():
    path = Path(feed_module.__file__).resolve().parent / "migrations" / "070_feed_generations.sql"
    code_only = "\n".join(line.split("--", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())
    assert not re.search(r"\bDELETE\b", code_only, re.IGNORECASE)
