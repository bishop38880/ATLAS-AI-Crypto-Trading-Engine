import torch
import torch.nn as nn


class LiquidNetwork(nn.Module):
    """Liquid Neural Network — continuous-time dynamics, explicit Euler solver.

    No `torchdiffeq` dependency — simple fixed-step integration only.
    """

    def __init__(
        self, input_dim: int, hidden_dim: int = 16, n_steps: int = 10, dt: float = 0.1
    ) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.n_steps = n_steps
        self.dt = dt

        self.linear_in = nn.Linear(input_dim, hidden_dim)
        self.w = nn.Linear(hidden_dim, hidden_dim)
        self.a = nn.Parameter(torch.ones(hidden_dim))
        self.tau_inv = nn.Parameter(torch.ones(hidden_dim))

    def _f(self, h: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        """Computes the derivative dh/dt."""
        return -h * self.tau_inv + self.w(torch.tanh(h + x)) * self.a

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Explicit Euler integration over n_steps.

        h(t+dt) = h(t) + dt * f(h(t), x(t))
        """
        batch, seq_len, _ = x.shape
        h = torch.zeros(batch, self.hidden_dim, device=x.device, dtype=torch.float32)

        ys = []
        for i in range(seq_len):
            x_i = self.linear_in(x[:, i])
            for _ in range(self.n_steps):
                dh = self._f(h, x_i)
                h = h + self.dt * dh
                h = torch.clamp(h, min=-10.0, max=10.0)
            ys.append(h)

        return torch.stack(ys, dim=1)
