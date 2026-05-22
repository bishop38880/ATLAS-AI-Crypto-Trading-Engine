"""Shadow Runner for RLVR."""

from __future__ import annotations

import asyncio
from typing import Literal

import asyncpg
import torch
from loguru import logger

from atlas.ml.rlvr.action_decoder import RLVRAction, project_weight_adjustments
from atlas.ml.rlvr.policy_network import SignalPolicyNetwork
from atlas.ml.rlvr.state_encoder import RLVRState


class ShadowRunner:
    """Runs RLVR policy inference alongside the base scorer."""

    def __init__(self, policy: SignalPolicyNetwork, asyncpg_pool: asyncpg.Pool) -> None:
        """Initialize with policy and db connection."""
        self.policy = policy
        self._pool = asyncpg_pool

    async def execute(
        self,
        state: RLVRState,
        base_decision: str,
        risk_veto: bool,
        rlvr_live: bool,
        signal_id: str,
    ) -> tuple[str, list[float] | None]:
        """Execute shadow policy.
        
        Args:
            state: The fully constructed RLVRState.
            base_decision: The decision from the base ConfluenceScorer.
            risk_veto: Whether the risk agent vetoed.
            rlvr_live: Whether RLVR is in live mode (from config).
            signal_id: The UUID of the current signal.
            
        Returns:
            A tuple of (final_decision, final_weight_adjustments).
        """
        if risk_veto:
            logger.info("rlvr_veto_enforced | signal_id={}", signal_id)
            return ("NO_POSITION", None)

        try:
            action = await self._run_inference(state)
            await self._log_to_postgres(signal_id, state, action, base_decision)
            
            if not rlvr_live:
                return (base_decision, None)

            if action.trade_decision == "ABSTAIN":
                return ("NO_POSITION", action.weight_adjustments)
            return (action.trade_decision, action.weight_adjustments)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("rlvr_inference_failed | signal_id={} | error={}", signal_id, e)
            return (base_decision, None)

    async def _run_inference(self, state: RLVRState) -> RLVRAction:
        """Run policy network safely in a thread."""
        x = torch.tensor(state.to_vector(), dtype=torch.float32).unsqueeze(0)
        
        logits, conf, weights = await self.policy.forward_async(x)
        
        # Parse outputs
        decision_idx = int(logits.argmax(dim=-1).item())
        decision_map: dict[int, Literal["LONG", "SHORT", "ABSTAIN"]] = {
            0: "LONG", 1: "SHORT", 2: "ABSTAIN"
        }
        decision = decision_map[decision_idx]
        
        confidence = float(conf.item())
        raw_weights = weights.detach().numpy()[0]
        
        # Project weights (deterministic, O(1), zero-sum)
        adjusted_weights = await asyncio.to_thread(project_weight_adjustments, raw_weights, 0.1)

        return RLVRAction(
            trade_decision=decision,
            trade_confidence=confidence,
            weight_adjustments=adjusted_weights.tolist(),
        )

    async def _log_to_postgres(
        self, signal_id: str, state: RLVRState, action: RLVRAction, base_decision: str
    ) -> None:
        """Log state, action, and base comparison to Postgres."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO rlvr_shadow_log 
                    (signal_id, base_decision, rlvr_decision, rlvr_confidence)
                    VALUES ($1, $2, $3, $4)
                    """,
                    signal_id, base_decision, action.trade_decision, action.trade_confidence,
                    timeout=5.0
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("rlvr_shadow_log_failed | error={}", e)
