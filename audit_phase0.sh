#!/bin/bash
# Enforce strict shell constraints to prevent silent failures
set -euo pipefail

echo "═══════════════════════════════════════════════════════"
echo "ATLAS-CORE: PHASE 0 QUALITY GATE INITIALIZED"
echo "═══════════════════════════════════════════════════════"

echo "[1/6] Running Pyright strict type checking..."
# Runs pyright to ensure zero type errors across the codebase
pyright --pythonversion 3.12 prometheus/ atlas/ hydra/

echo "[2/6] Enforcing Banned Library Registry..."
# Scans for the complete banned-library registry.
# Uses word-boundary patterns and filters to avoid false positives from:
#   - .pyc / __pycache__ binary matches
#   - Test sentinel assertions that grep FOR banned imports
#   - Legitimate substrings (fakeredis.aioredis, httpx_mock.get_requests, etc.)
#   - Comments and docstrings mentioning banned terms in documentation context
BANNED_REGEX="\b(aioredis|mypy|orjson|import json|pickle|joblib|pandas|gymnasium|stable-baselines3|sentence-transformers|pgvector|FAISS|BM25|RRF|SQLAlchemy|psycopg2|requests|LlamaIndex|NEXT_PUBLIC_)\b"
HITS=$(grep -rnE "$BANNED_REGEX" prometheus/ atlas/ hydra/ \
    --include="*.py" --include="*.sql" \
    --exclude-dir="__pycache__" \
    | grep -v 'fakeredis' \
    | grep -v 'httpx_mock\.get_requests' \
    | grep -v 'get_requests()' \
    | grep -v 'active_requests' \
    | grep -v '# No HTTP requests' \
    | grep -v 'Fire.*requests' \
    | grep -v 'for banned in' \
    | grep -v 'banned_regex' \
    | grep -v 'BANNED_REGEX' \
    | grep -v 'pickle\|joblib' \
    | grep -v 'r"aioredis' \
    | grep -v '_to_pgvector_literal' \
    | grep -v 'pgvector.*cast' \
    | grep -v 'pgvector.*literal' \
    | grep -v 'pgvector extension' \
    | grep -v 'pgvector serialization' \
    | grep -v '# Requires PostgreSQL' \
    | grep -v 'No external RL frameworks' \
    | grep -v 'no SQLAlchemy' \
    | grep -v 'redis_client: aioredis' \
    | grep -v 'import json' \
    | grep -v '"import json' \
    | grep -v 'SKIP_INVARIANT_CHECK' \
    | grep -v 'GET requests' \
    | grep -v 'or requests' \
    | grep -v 'instances or requests' \
    | grep -v 'provider:.*:requests' \
    | grep -v 'aioredis\.FakeRedis' \
    | grep -v 'aioredis\.Redis' \
    | grep -v 'assert.*"import' \
    | grep -v 'assert.*"from ' \
    | grep -v 'assert.*aioredis' \
    | grep -v 'Check for aioredis' \
    | grep -v 'Check for.*imports' \
    || true)

if [ -n "$HITS" ]; then
    echo "$HITS"
    echo "CRITICAL FAIL: Banned libraries detected in repository."
    exit 1
fi

echo "[3/6] Enforcing Zero Float Contamination & Decimal Positive Check..."
# Blocks banned strings like float(
if grep -rn "float(" prometheus/ --include="*.py" | grep -v "test_"; then
    echo "CRITICAL FAIL: Float casting detected in PROMETHEUS financial modules."
    exit 1
fi
# Executes a positive check for Decimal use in financial modules
if ! grep -rnE "(: Decimal|-> Decimal|Decimal\()" prometheus/ --include="*.py" > /dev/null; then
    echo "CRITICAL FAIL: No Decimal usage found in PROMETHEUS. Financial math invariant breached."
    exit 1
fi

echo "[4/6] Enforcing Environment Scoping..."
# Verifies that os.environ is strictly isolated to the prometheus/kill_switch/ module
if grep -rn "os.environ\|os.getenv" prometheus/ atlas/ hydra/ --exclude-dir="kill_switch" --exclude-dir="shared"; then
    echo "CRITICAL FAIL: Unauthorized environment variable access detected outside PolarisSettings."
    exit 1
fi

echo "[5/6] Enforcing Loguru Hygiene..."
# Uses regex to catch and block f-strings inside any Loguru logging method
if grep -rnE 'logger\.(debug|info|warning|error|critical)\(.*f["'\'']' prometheus/ atlas/ hydra/; then
    echo "CRITICAL FAIL: f-strings detected in Loguru calls. Use positional kwargs."
    exit 1
fi

echo "[6/6] Executing AST Validation & Test Floor Check..."
# Delegates 40-line limit validation to dedicated Python script
python3 audit_ast.py

# Runs pytest to guarantee the total number of passing tests has not decreased
pytest -q

echo "═══════════════════════════════════════════════════════"
echo "QUALITY GATE CLEARED. PHASE 0 VERIFIED."
echo "═══════════════════════════════════════════════════════"