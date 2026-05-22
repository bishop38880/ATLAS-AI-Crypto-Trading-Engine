"""
FastMCP server for Pre-Execution Threat Intel.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Exposes three tools for the PROMETHEUS agent:
  - simulate_evm_transaction (Tenderly shadow execution)
  - scan_token_security (GoPlus static analysis)
  - evaluate_execution_safety (Master Guardrail — concurrent)

Sentinel Invariants:
  - msgspec for all JSON serialization
  - httpx.AsyncClient (singleton per client)
  - asyncio.gather for concurrent Layer 1 + Layer 2
  - Loguru positional format only
  - No os.getenv — uses dotenv for MCP-scoped config
  - Max 40 lines per function
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import msgspec
from dotenv import dotenv_values
from loguru import logger
from mcp.server.fastmcp import FastMCP

from .clients.goplus import GoPlusClient
from .clients.tenderly import TenderlyClient
from .config import resolve_chain_id, resolve_tenderly_network
from .models import (
    ExecutionSafetyReport,
    ShadowDeltaResult,
    TenderlySimulationResult,
    TokenSecurityReport,
)
from .security_engine import (
    calculate_shadow_delta,
    evaluate_execution_safety as engine_evaluate,
)

# ──────────────────────────────────────────────────────────────
# Config loading (dotenv — not os.getenv in app code)
# ──────────────────────────────────────────────────────────────

_ENV_PATH: Path = Path(__file__).parent / ".env"
_config: dict[str, str | None] = dotenv_values(_ENV_PATH)


def _cfg(key: str, default: str = "") -> str:
    """Read a config value from the .env file."""
    return str(_config.get(key, default) or default)


# ──────────────────────────────────────────────────────────────
# Client singletons
# ──────────────────────────────────────────────────────────────

_tenderly_client: TenderlyClient = TenderlyClient(
    access_key=_cfg("TENDERLY_ACCESS_KEY"),
    account_slug=_cfg("TENDERLY_ACCOUNT_SLUG"),
    project_slug=_cfg("TENDERLY_PROJECT_SLUG"),
    base_url=_cfg("TENDERLY_BASE_URL", "https://api.tenderly.co"),
    timeout=float(_cfg("HTTP_TIMEOUT_SECONDS", "2.0")),
)

_goplus_client: GoPlusClient = GoPlusClient(
    base_url=_cfg("GOPLUS_BASE_URL", "https://api.gopluslabs.io"),
    timeout=float(_cfg("HTTP_TIMEOUT_SECONDS", "2.0")),
)

_delta_threshold: float = float(
    _cfg("SHADOW_DELTA_THRESHOLD_PCT", "90.0"),
)

# ──────────────────────────────────────────────────────────────
# FastMCP server
# ──────────────────────────────────────────────────────────────

mcp = FastMCP(
    "threat-intel",
    instructions=(
        "PROMETHEUS Execution Router — Pre-Execution Security Guardrail. "
        "Tenderly shadow simulation + GoPlus static contract analysis. "
        "A HARD_ABORT is a non-negotiable command to drop the transaction."
    ),
)


def _serialize(obj: Any) -> str:
    """Serialize a Pydantic model to JSON via msgspec."""
    if hasattr(obj, "model_dump"):
        return msgspec.json.encode(
            obj.model_dump(mode="json"),
        ).decode("utf-8")
    return msgspec.json.encode(obj).decode("utf-8")


# ──────────────────────────────────────────────────────────────
# Tool 1: simulate_evm_transaction
# ──────────────────────────────────────────────────────────────


@mcp.tool()
async def simulate_evm_transaction(
    network_id: str,
    from_addr: str,
    to_addr: str,
    calldata: str,
    value_wei: str,
) -> str:
    """
    Shadow-execute a raw EVM transaction via Tenderly.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Sends the exact trade payload to a private fork for simulation.
    Returns gas used, success status, and all asset state changes.
    If this returns success=False, the transaction WILL revert
    on-chain — do NOT sign it.

    Args:
        network_id: Tenderly network ID (e.g. "1" for mainnet).
        from_addr: Sender wallet address.
        to_addr: Destination contract address.
        calldata: Hex-encoded transaction calldata.
        value_wei: Native ETH value in wei (string).

    Returns:
        JSON TenderlySimulationResult.
    """
    result: TenderlySimulationResult = (
        await _tenderly_client.simulate_transaction(
            network_id, from_addr, to_addr, calldata, value_wei,
        )
    )
    return _serialize(result)


# ──────────────────────────────────────────────────────────────
# Tool 2: scan_token_security
# ──────────────────────────────────────────────────────────────


@mcp.tool()
async def scan_token_security(
    chain_id: str,
    token_address: str,
) -> str:
    """
    Statically analyse an ERC-20 contract for security threats.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Uses GoPlus to scan the token's bytecode for honeypots,
    pausable transfers, blacklists, unlimited minting, and
    hidden transfer taxes. Any critical flag means HARD_ABORT.

    Args:
        chain_id: Chain name (e.g. "ethereum") or numeric ID ("1").
        token_address: Token contract address.

    Returns:
        JSON TokenSecurityReport with all flags.
    """
    resolved_chain: str = resolve_chain_id(chain_id)
    report: TokenSecurityReport = await _goplus_client.scan_token(
        resolved_chain, token_address,
    )
    return _serialize(report)


# ──────────────────────────────────────────────────────────────
# Tool 3: evaluate_execution_safety (Master Guardrail)
# ──────────────────────────────────────────────────────────────


@mcp.tool()
async def evaluate_execution_safety(
    chain_id: str,
    network_id: str,
    token_address: str,
    from_addr: str,
    to_addr: str,
    calldata: str,
    value_wei: str,
    expected_output_wei: str,
) -> str:
    """
    Master pre-execution security guardrail (concurrent).

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Runs Tenderly + GoPlus via asyncio.gather. Calculates Shadow Delta.
    If hard_abort=True, PROMETHEUS MUST drop the transaction. Non-negotiable.
    """
    resolved_chain: str = resolve_chain_id(chain_id)
    resolved_network: str = resolve_tenderly_network(network_id)

    sim_result, sec_report = await _run_concurrent_checks(
        resolved_network, resolved_chain,
        from_addr, to_addr, calldata, value_wei, token_address,
    )

    delta = _compute_delta(sim_result, expected_output_wei, from_addr)

    report: ExecutionSafetyReport = engine_evaluate(
        sim_result, sec_report, delta,
    )

    _log_verdict(report)
    return _serialize(report)


async def _run_concurrent_checks(
    network_id: str,
    chain_id: str,
    from_addr: str,
    to_addr: str,
    calldata: str,
    value_wei: str,
    token_address: str,
) -> tuple[TenderlySimulationResult | None, TokenSecurityReport | None]:
    """
    Run Tenderly and GoPlus checks concurrently.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.

    Args:
        network_id: Tenderly network ID.
        chain_id: GoPlus chain ID.
        from_addr: Sender address.
        to_addr: Contract address.
        calldata: Transaction calldata.
        value_wei: ETH value in wei.
        token_address: Token to scan.

    Returns:
        Tuple of (simulation_result, security_report).
    """
    sim_task = _tenderly_client.simulate_transaction(
        network_id, from_addr, to_addr, calldata, value_wei,
    )
    sec_task = _goplus_client.scan_token(chain_id, token_address)

    results = await asyncio.gather(
        sim_task, sec_task, return_exceptions=True,
    )

    sim = _extract_sim(results[0])
    sec = _extract_sec(results[1])
    return sim, sec


def _extract_sim(
    result: TenderlySimulationResult | BaseException,
) -> TenderlySimulationResult | None:
    """Extract a Tenderly result or log the exception."""
    if isinstance(result, BaseException):
        logger.error(
            "Concurrent check failed | type=Tenderly | error={}",
            result,
        )
        return None
    return result


def _extract_sec(
    result: TokenSecurityReport | BaseException,
) -> TokenSecurityReport | None:
    """Extract a GoPlus result or log the exception."""
    if isinstance(result, BaseException):
        logger.error(
            "Concurrent check failed | type=GoPlus | error={}",
            result,
        )
        return None
    return result


def _compute_delta(
    sim: TenderlySimulationResult | None,
    expected_wei: str,
    wallet: str,
) -> ShadowDeltaResult | None:
    """Compute Shadow Delta if simulation succeeded."""
    if sim is None or not sim.success:
        return None
    if not expected_wei or expected_wei == "0":
        return None
    return calculate_shadow_delta(
        expected_wei, sim.asset_changes, wallet, _delta_threshold,
    )


def _log_verdict(report: ExecutionSafetyReport) -> None:
    """Log the final safety verdict."""
    if report.hard_abort:
        logger.warning(
            "HARD_ABORT | threats={} | reasons={}",
            report.threat_level.value,
            len(report.threat_reasons),
        )
    else:
        logger.info(
            "SAFE | threat_level={}",
            report.threat_level.value,
        )


# ──────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
