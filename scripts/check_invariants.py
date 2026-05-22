#!/usr/bin/env python3
"""Sentinel Audit: Automated Invariant Checking — Full Canonical List.

Enforces ATLAS/PROMETHEUS hard wall and banned-library invariants
via AST analysis. Runs as a pre-commit hook and CI gate.

Banned-library list derived from:
    - 050-tech-stack2.mdc ABSOLUTE_BAN section
    - userMemories canonical-tools table
    - POLARIS Audit v1.0 Appendix B
"""

import ast
import re
import sys
from pathlib import Path

# ── Banned import modules (top-level) ──────────────────────────────
BANNED_IMPORTS: frozenset[str] = frozenset({
    # Replaced by approved equivalents
    "requests",       # → httpx.AsyncClient
    "urllib",         # → httpx.AsyncClient
    "pandas",         # → polars
    "psycopg2",       # → asyncpg
    "sqlalchemy",     # → asyncpg + raw SQL
    "pgvector",       # → qdrant-client
    "faiss",          # → qdrant-client
    "sentence_transformers",  # → MistralEmbeddingClient
    "orjson",         # → msgspec
    "aioredis",       # → redis.asyncio (abandoned upstream)
    "pickle",         # → msgspec.json
    "joblib",         # → msgspec.json
    "mypy",           # → pyright
    "gymnasium",      # banned RL library
    "stable_baselines3",  # banned RL library
    "llamaindex",     # → custom RAG
    "llama_index",    # → custom RAG
    # Exchange SDKs forbidden in ATLAS
    "ccxt",           # → PROMETHEUS only
    "bitget",         # → PROMETHEUS only
})

# ── Banned from-import sources ─────────────────────────────────────
BANNED_FROM_SOURCES: frozenset[str] = frozenset({
    "sqlalchemy",
    "psycopg2",
    "aioredis",
    "requests",
    "pandas",
    "ccxt",
    "bitget",
    "faiss",
    "sentence_transformers",
    "orjson",
    "gymnasium",
    "stable_baselines3",
})

# ── Banned attribute / call patterns in ATLAS ──────────────────────
BANNED_CALLS_IN_ATLAS: frozenset[str] = frozenset({
    "place_order", "create_order", "position", "balance",
})

# ── Stdlib json is banned except in jsonschema validation ──────────
# Regex: matches `import json` but not `import jsonschema`
_STDLIB_JSON_RE = re.compile(r"^import\s+json\s*$")


class InvariantVisitor(ast.NodeVisitor):
    """AST visitor that collects invariant violations."""

    def __init__(self, filepath: str, is_atlas: bool) -> None:
        self.filepath = filepath
        self.is_atlas = is_atlas
        self.errors: list[str] = []
        # Calculate current top-level folder under atlas/
        path_parts = Path(filepath).parts
        self.current_folder: str | None = None
        if "atlas" in path_parts:
            atlas_idx = path_parts.index("atlas")
            if len(path_parts) > atlas_idx + 1:
                self.current_folder = path_parts[atlas_idx + 1]

    def _add(self, node: ast.AST, msg: str) -> None:
        lineno = getattr(node, "lineno", 0)
        self.errors.append(f"{self.filepath}:{lineno}: {msg}")

    # ── import X ───────────────────────────────────────────────────
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base = alias.name.split(".")[0]
            if base in BANNED_IMPORTS:
                self._add(node, f"Banned import '{base}' — see 050-tech-stack2.mdc")
            # stdlib json check
            if alias.name == "json":
                self._add(node, "Banned 'import json' — use msgspec")
            # Cross-folder imports in atlas
            self._check_cross_folder(node, alias.name)
        self.generic_visit(node)

    # ── from X import Y ────────────────────────────────────────────
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base = node.module.split(".")[0]
            if base in BANNED_FROM_SOURCES:
                self._add(
                    node,
                    f"Banned from-import '{base}' — see 050-tech-stack2.mdc",
                )
            # stdlib json via from-import
            if node.module == "json":
                self._add(node, "Banned 'from json import ...' — use msgspec")
            # Cross-folder imports in atlas
            self._check_cross_folder(node, node.module)
        self.generic_visit(node)

    # ── Banned calls/attrs (ATLAS only) ────────────────────────────
    def visit_Call(self, node: ast.Call) -> None:
        # Check for Loguru keyword arguments violation (all folders)
        self._check_loguru_kwargs(node)
        
        if self.is_atlas:
            if isinstance(node.func, ast.Name):
                if node.func.id in BANNED_CALLS_IN_ATLAS:
                    self._add(node, f"Banned call '{node.func.id}' in ATLAS")
            elif isinstance(node.func, ast.Attribute):
                if node.func.attr in BANNED_CALLS_IN_ATLAS:
                    self._add(node, f"Banned call '{node.func.attr}' in ATLAS")
        self.generic_visit(node)

    # ── Loguru guard ───────────────────────────────────────────────
    def _check_loguru_kwargs(self, node: ast.Call) -> None:
        """Flag logger.info("msg", key=val) as it loses data in Loguru."""
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == "logger" and node.keywords:
                # logger.bind(k=v).info("msg") is OK, but logger.info("msg", k=v) is NOT.
                if node.func.attr in {"debug", "info", "warning", "error", "critical", "success"}:
                    self._add(
                        node,
                        f"Banned loguru kwarg '{node.keywords[0].arg}' — "
                        "use logger.info('msg | key={}', val) or logger.bind(key=val).info('msg')",
                    )

    # ── Function length guard ──────────────────────────────────────
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # 40-line limit (excluding decorators and docstrings)
        start = node.lineno
        end_attr = getattr(node, "end_lineno", None)
        end = int(end_attr) if end_attr is not None else start
        length = end - start + 1
        if length > 40:
            # We subtract docstring length if present
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                doc_lines = (body[0].value.value.count("\n") + 1) if isinstance(body[0].value.value, str) else 0
                length -= doc_lines
            
            if length > 40:
                self._add(
                    node,
                    f"Function '{node.name}' is too long ({length} lines) — "
                    "limit is 40 lines (Invariant 7)",
                )
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)  # type: ignore

    # ── Cross-folder import guard ──────────────────────────────────
    def _check_cross_folder(self, node: ast.AST, module_path: str) -> None:
        if "registry.py" in self.filepath:
            return
        
        # Skip if inside TYPE_CHECKING
        parent = getattr(node, "parent", None)
        while parent:
            if isinstance(parent, ast.If) and isinstance(parent.test, ast.Name) and parent.test.id == "TYPE_CHECKING":
                return
            parent = getattr(parent, "parent", None)

        parts = module_path.split(".")
        if len(parts) >= 2 and parts[0] == "atlas":
            imported_folder = parts[1]
            allowed_cross = {"core", "models", "shared", "providers", "telemetry"}
            
            # Orchestration/Glue layers are allowed to import from feature folders
            glue_layers = {"core", "orchestrator", "pipeline", "testing", "tests", "marl", "routes"}
            if self.current_folder in glue_layers:
                allowed_cross.update({"agents", "signals", "ml", "rag", "orchestrator", "pipeline", "dependencies", "settings"})

            if self.current_folder == "agents":
                allowed_cross.add("ml")

            if (
                self.current_folder
                and imported_folder != self.current_folder
                and imported_folder not in allowed_cross
            ):
                self._add(
                    node,
                    f"Direct cross-folder import 'atlas.{imported_folder}' "
                    f"from 'atlas.{self.current_folder}' — use core/registry.py",
                )


def check_file(filepath: str) -> bool:
    """Parse and check a single Python file for invariant violations."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return False

    if "SKIP_INVARIANT_CHECK" in content:
        return True

    try:
        tree = ast.parse(content, filename=filepath)
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child.parent = node  # type: ignore
    except SyntaxError as e:
        print(f"Syntax error in {filepath}: {e}")
        return False

    is_atlas = "atlas/" in filepath or "atlas\\" in filepath
    visitor = InvariantVisitor(filepath, is_atlas)
    visitor.visit(tree)

    for err in visitor.errors:
        print(err)

    return len(visitor.errors) == 0


def main() -> None:
    """Entry point: check all .py files passed as arguments."""
    files_to_check = sys.argv[1:]
    all_passed = True
    for f in files_to_check:
        if f.endswith(".py"):
            passed = check_file(f)
            if not passed:
                all_passed = False

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
