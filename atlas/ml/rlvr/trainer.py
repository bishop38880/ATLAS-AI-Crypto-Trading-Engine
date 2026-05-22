"""Custom PPO Trainer for RLVR."""

from __future__ import annotations

import asyncio
import torch
import torch.nn as nn
import torch.optim as optim

from atlas.ml.rlvr.policy_network import SignalPolicyNetwork


class RLVRTrainer:
    """Custom PPO trainer for the SignalPolicyNetwork.

    No external RL frameworks (gymnasium, stable-baselines3) are permitted.
    """

    def __init__(
        self,
        meta_learner_bullish: bool | None,
        thompson_entropy: float,
        lr: float = 3e-4,
        clip_ratio: float = 0.2,
        gamma: float = 0.99,
        epochs: int = 4,
    ) -> None:
        """Initialize trainer with explicit warm-start mapping.

        Session 19B Warm-Start Protocol:
        1. Decision head bias from meta-learner sign.
        2. Weight head bias to zero, weights to small Gaussian (std=0.01).
        3. Confidence head bias = 1.0 - normalized_thompson_entropy.
        4. Backbone uses standard Kaiming (done in network init).
        """
        self.policy = SignalPolicyNetwork()
        self.optimizer = optim.Adam(self.policy.parameters(), lr=lr)
        self.clip_ratio = clip_ratio
        self.gamma = gamma
        self.epochs = epochs

        self._apply_warm_start(meta_learner_bullish, thompson_entropy)

    def _apply_warm_start(self, meta_learner_bullish: bool | None, thompson_entropy: float) -> None:
        """Apply Session 19B Warm-Start Protocol."""
        with torch.no_grad():
            # 1. Decision head bias
            nn.init.zeros_(self.policy.decision_head.bias)
            if meta_learner_bullish is True:
                self.policy.decision_head.bias[0] += 0.5  # LONG
                self.policy.decision_head.bias[1] -= 0.5  # SHORT
            elif meta_learner_bullish is False:
                self.policy.decision_head.bias[0] -= 0.5  # LONG
                self.policy.decision_head.bias[1] += 0.5  # SHORT
            else:
                nn.init.zeros_(self.policy.decision_head.bias)

            # 2. Weight-adjustment head: zero bias, small Gaussian weights
            nn.init.normal_(self.policy.weight_head.weight, mean=0.0, std=0.01)
            nn.init.zeros_(self.policy.weight_head.bias)

            # 3. Confidence head
            conf_bias = 1.0 - max(0.0, min(1.0, thompson_entropy))
            nn.init.constant_(self.policy.confidence_head[0].bias, conf_bias)  # type: ignore[arg-type]

    def compute_loss(
        self,
        states: torch.Tensor,
        actions_logits: torch.Tensor,
        advantages: torch.Tensor,
        old_log_probs: torch.Tensor,
    ) -> torch.Tensor:
        """Compute PPO clipped surrogate loss."""
        # Simple policy gradient with clipping for the decision head
        logits, _, _ = self.policy(states)
        dist = torch.distributions.Categorical(logits=logits)
        
        # PPO requires action index. We assume actions_logits are indices here for simplicity
        # In a full rollout buffer, we'd store the selected action index.
        actions = actions_logits.argmax(dim=-1)
        new_log_probs = dist.log_prob(actions)

        ratio = torch.exp(new_log_probs - old_log_probs)
        clip_adv = torch.clamp(ratio, 1.0 - self.clip_ratio, 1.0 + self.clip_ratio) * advantages
        
        loss_pi = -(torch.min(ratio * advantages, clip_adv)).mean()
        return loss_pi

    def train_step(self, buffer: dict[str, torch.Tensor]) -> float:
        """Perform one epoch of PPO training on the buffer."""
        states = buffer["states"]
        actions = buffer["actions"]
        advantages = buffer["advantages"]
        old_log_probs = buffer["log_probs"]

        loss_val = 0.0
        for _ in range(self.epochs):
            self.optimizer.zero_grad()
            loss = self.compute_loss(states, actions, advantages, old_log_probs)
            loss.backward()
            self.optimizer.step()
            loss_val += loss.item()

        return loss_val / self.epochs

    async def train_step_async(self, buffer: dict[str, torch.Tensor]) -> float:
        """Async wrapper for training step."""
        return await asyncio.to_thread(self.train_step, buffer)

    def save(self, path: str) -> None:
        """Serialize using torch.save."""
        torch.save(self.policy.state_dict(), path)

    def load(self, path: str) -> None:
        """Deserialize using torch.load with weights_only=True."""
        self.policy.load_state_dict(torch.load(path, weights_only=True, map_location="cpu"))
