"""
Pre-Execution Threat Intel MCP — Tenderly / GoPlus.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Provides shadow execution simulation (Tenderly) and static contract
analysis (GoPlus) as an absolute EVM security guardrail before any
on-chain transaction is signed.

Sentinel Invariants:
  - All models frozen=True (immutable DTOs)
  - Financial values as str/int for wei precision (never float)
  - httpx.AsyncClient for all HTTP (never requests)
  - msgspec for JSON serialization (never stdlib json)
  - Loguru structured kwargs (never f-strings)
  - Max 40 lines per function
"""
