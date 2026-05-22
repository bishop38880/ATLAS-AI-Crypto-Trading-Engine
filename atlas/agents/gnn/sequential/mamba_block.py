import torch
import torch.nn as nn


class MambaBlock(nn.Module):
    """Pure-PyTorch selective state-space model — CPU, no CUDA kernel.

    Based on the 'Selective Scan' recurrence (Mamba paper arXiv:2312.00752),
    implemented as a sequential Python loop. Slower than the CUDA kernel but
    functionally equivalent for small sequences.
    """

    def __init__(self, d_model: int = 32, d_state: int = 16, d_conv: int = 4) -> None:
        super().__init__()
        self.d_model = d_model
        self.d_state = d_state
        self.d_conv = d_conv

        self.in_proj = nn.Linear(d_model, d_model * 2, bias=False)
        self.conv1d = nn.Conv1d(
            in_channels=d_model,
            out_channels=d_model,
            kernel_size=d_conv,
            groups=d_model,
            padding=d_conv - 1,
        )
        self.x_proj = nn.Linear(d_model, 1 + d_state * 2, bias=False)
        self.dt_proj = nn.Linear(1, d_model, bias=True)
        self.out_proj = nn.Linear(d_model, d_model, bias=False)

        a_init = torch.arange(1, d_state + 1, dtype=torch.float32).repeat(d_model, 1)
        self.a_log = nn.Parameter(torch.log(a_init))
        self.d = nn.Parameter(torch.ones(d_model))

    def _selective_scan(
        self, x: torch.Tensor, delta: torch.Tensor, b: torch.Tensor, c: torch.Tensor
    ) -> torch.Tensor:
        """Sequential loop for SSM."""
        batch, seq_len, _ = x.shape
        a = -torch.exp(self.a_log.float())
        h = torch.zeros(batch, self.d_model, self.d_state, device=x.device, dtype=torch.float32)
        ys = []
        for i in range(seq_len):
            x_i = x[:, i]
            dt_i = delta[:, i, :].unsqueeze(-1)
            b_i = b[:, i, :].unsqueeze(1)
            c_i = c[:, i, :].unsqueeze(1)

            da = torch.exp(dt_i * a)
            db = dt_i * b_i

            h = da * h + db * x_i.unsqueeze(-1)
            y = torch.sum(h * c_i, dim=-1)
            ys.append(y)

        y_out = torch.stack(ys, dim=1)
        return y_out + x * self.d

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Selective scan forward pass."""
        _, seq_len, _ = x.shape
        if seq_len > 256:
            raise AssertionError(f"Sequence length {seq_len} exceeds maximum of 256")

        xz = self.in_proj(x)
        x_in, z = xz.chunk(2, dim=-1)

        x_conv = x_in.transpose(1, 2)
        x_conv = self.conv1d(x_conv)[:, :, :seq_len]
        x_conv = nn.functional.silu(x_conv.transpose(1, 2))

        x_dbl = self.x_proj(x_conv)
        delta, b, c = torch.split(x_dbl, [1, self.d_state, self.d_state], dim=-1)
        delta = nn.functional.softplus(self.dt_proj(delta))

        y = self._selective_scan(x_conv, delta, b, c)
        y = y * nn.functional.silu(z)

        return self.out_proj(y)
