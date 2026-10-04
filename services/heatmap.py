"""Market heatmap: a treemap of holdings (size = value, colour = today's move)."""
from __future__ import annotations

from core.ui import esc


def _squarify(values: list[float], x: float, y: float, w: float, h: float) -> list[tuple[float, float, float, float]]:
    """Squarified treemap rectangles for values (largest first), filling the box."""
    rects, items = [], list(values)
    total = sum(items) or 1
    scale = w * h / total
    areas = [v * scale for v in items]

    def worst(row, side):
        s = sum(row)
        return max(max(side * side * r / (s * s), (s * s) / (side * side * r)) for r in row) if s and side else float("inf")

    while areas:
        side = min(w, h)
        row = [areas.pop(0)]
        while areas and worst(row + [areas[0]], side) <= worst(row, side):
            row.append(areas.pop(0))
        s = sum(row)
        if w >= h:
            col = s / h if h else 0
            yy = y
            for a in row:
                hh = a / col if col else 0
                rects.append((x, yy, col, hh))
                yy += hh
            x, w = x + col, w - col
        else:
            rw = s / w if w else 0
            xx = x
            for a in row:
                ww = a / rw if rw else 0
                rects.append((xx, y, ww, rw))
                xx += ww
            y, h = y + rw, h - rw
    return rects


def colour(move: float | None) -> str:
    if move is None:
        return "#8A938F"
    m = max(-0.05, min(0.05, move)) / 0.05
    if m >= 0:
        return f"rgb({int(232 - 190 * m)},{int(240 - 70 * m)},{int(234 - 160 * m)})"
    m = -m
    return f"rgb({int(244 - 60 * m)},{int(232 - 170 * m)},{int(230 - 170 * m)})"


def svg(items: list[dict]) -> str:
    """items: name, ticker, value (any currency, same base), move (fraction or None)."""
    items = sorted([i for i in items if (i.get("value") or 0) > 0], key=lambda i: -i["value"])
    if not items:
        return ""
    w, h = 720, 340
    rects = _squarify([i["value"] for i in items], 0, 0, w, h)
    parts = []
    for i, (x, y, rw, rh) in zip(items, rects):
        move = i.get("move")
        label = f"{move * 100:+.1f}%" if move is not None else ""
        dark = move is not None and abs(move) > 0.03
        fill = "#fff" if dark else "#1B2430"
        text = ""
        if rw > 46 and rh > 28:
            size = max(10, min(16, rw / 7, rh / 3))
            text = (f'<text x="{x + rw / 2:.1f}" y="{y + rh / 2 - 2:.1f}" text-anchor="middle" font-size="{size:.0f}" '
                    f'font-weight="700" fill="{fill}">{esc(i["ticker"])}</text>'
                    f'<text x="{x + rw / 2:.1f}" y="{y + rh / 2 + size:.1f}" text-anchor="middle" font-size="{size * 0.8:.0f}" '
                    f'fill="{fill}">{label}</text>')
        parts.append(f'<g><rect x="{x:.1f}" y="{y:.1f}" width="{rw:.1f}" height="{rh:.1f}" fill="{colour(move)}" '
                     f'stroke="var(--card)" stroke-width="2"><title>{esc(i["name"])}: {label}</title></rect>{text}</g>')
    return (f'<div class="heat"><svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Holdings heatmap">{"".join(parts)}</svg></div>')
