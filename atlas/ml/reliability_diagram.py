from atlas.ml.calibrator import CalibrationResult


def generate_reliability_diagram_svg(
    calibration_result: CalibrationResult,
    width: int = 400,
    height: int = 400,
) -> str:
    """Returns an SVG string for the reliability diagram."""
    padding = 50
    chart_w = width - 2 * padding
    chart_h = height - 2 * padding

    parts = _build_svg_header(width, height, padding, calibration_result.ece)
    parts.append(f'<g transform="translate({padding}, {padding})">')
    parts.append(
        f'<line x1="0" y1="{chart_h}" x2="{chart_w}" y2="0" '
        f'stroke="#666666" stroke-width="2" stroke-dasharray="5,5" />'
    )
    parts.extend(_build_data_points(calibration_result, chart_w, chart_h))
    parts.extend(_build_axes_and_ticks(chart_w, chart_h))
    parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts)


def _build_svg_header(w: int, h: int, pad: int, ece: float) -> list[str]:
    """Build SVG root, background, and title elements."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'<rect width="{w}" height="{h}" fill="#1e1e1e" />',
        f'<text x="{w/2}" y="{pad/2}" fill="#ffffff" font-family="sans-serif" font-size="16" text-anchor="middle">Reliability Diagram</text>',
        f'<text x="{w - pad}" y="{pad/2}" fill="#ffcc00" font-family="sans-serif" font-size="14" text-anchor="end">ECE: {ece:.4f}</text>',
    ]


def _build_data_points(
    result: CalibrationResult, chart_w: int, chart_h: int,
) -> list[str]:
    """Render calibration bin circles scaled by sample count."""
    parts: list[str] = []
    max_samples = max((b.n_samples for b in result.bins), default=1)
    for b in result.bins:
        if b.n_samples == 0:
            continue
        cx = b.predicted_mean * chart_w
        cy = chart_h - (b.observed_rate * chart_h)
        r = 3 + 12 * (b.n_samples / max_samples) ** 0.5
        dist = abs(b.predicted_mean - b.observed_rate)
        color = "#00ff00" if dist < 0.05 else ("#ffff00" if dist < 0.15 else "#ff0000")
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}" '
            f'fill-opacity="0.6" stroke="{color}" stroke-width="1" />'
        )
    return parts


def _build_axes_and_ticks(chart_w: int, chart_h: int) -> list[str]:
    """Render axes, axis labels, and tick marks."""
    parts = [
        f'<line x1="0" y1="{chart_h}" x2="{chart_w}" y2="{chart_h}" stroke="#ffffff" stroke-width="2" />',
        f'<line x1="0" y1="0" x2="0" y2="{chart_h}" stroke="#ffffff" stroke-width="2" />',
        f'<text x="{chart_w/2}" y="{chart_h + 30}" fill="#ffffff" font-family="sans-serif" font-size="12" text-anchor="middle">Predicted Probability</text>',
        f'<text transform="rotate(-90)" x="-{chart_h/2}" y="-35" fill="#ffffff" font-family="sans-serif" font-size="12" text-anchor="middle">Observed Frequency</text>',
    ]
    for i in range(0, 11, 2):
        val = i / 10.0
        x = val * chart_w
        y = chart_h - (val * chart_h)
        parts.append(f'<line x1="{x}" y1="{chart_h}" x2="{x}" y2="{chart_h + 5}" stroke="#ffffff" stroke-width="1" />')
        parts.append(f'<text x="{x}" y="{chart_h + 15}" fill="#ffffff" font-family="sans-serif" font-size="10" text-anchor="middle">{val:.1f}</text>')
        parts.append(f'<line x1="0" y1="{y}" x2="-5" y2="{y}" stroke="#ffffff" stroke-width="1" />')
        parts.append(f'<text x="-10" y="{y + 4}" fill="#ffffff" font-family="sans-serif" font-size="10" text-anchor="end">{val:.1f}</text>')
    return parts
