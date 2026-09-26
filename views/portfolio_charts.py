"""Drawing for the Portfolio page: performance lines and allocation donuts (plain SVG, theme-aware via CSS)."""
from __future__ import annotations

import math
from datetime import date

from core.ui import esc

SLICE_COLOURS = ["#1F6B4F", "#2E5E8C", "#C07A2C", "#6A4C93", "#9C3D54", "#2F7A7A", "#8A8F3B", "#7A4B2F"]


def performance_svg(portfolio: list[tuple[date, float]], benchmark: list[tuple[date, float]], bench_name: str) -> str:
    """Two lines rebased to 0% at the start of the period."""
    if len(portfolio) < 2:
        return ""
    w, h, pad_l, pad_r, pad_y = 720, 220, 44, 10, 14
    p0 = portfolio[0][1]
    pts = [(d, v / p0 - 1) for d, v in portfolio]
    bpts = []
    if benchmark and benchmark[0][1]:
        b0 = benchmark[0][1]
        bpts = [(d, v / b0 - 1) for d, v in benchmark]
    values = [v for _, v in pts] + [v for _, v in bpts] + [0.0]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 0.01
    lo, hi = lo - span * 0.08, hi + span * 0.08
    t0, t1 = pts[0][0].toordinal(), max(pts[-1][0].toordinal(), pts[0][0].toordinal() + 1)
    x = lambda d: pad_l + (d.toordinal() - t0) / (t1 - t0) * (w - pad_l - pad_r)
    y = lambda v: pad_y + (hi - v) / (hi - lo) * (h - 2 * pad_y)
    line = lambda series: " ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in series if t0 <= d.toordinal() <= t1)
    grid = []
    step = _nice_step(hi - lo)
    tick = math.ceil(lo / step) * step
    while tick <= hi:
        grid.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" class="grid"/>'
                    f'<text x="{pad_l - 6}" y="{y(tick) + 3:.1f}" text-anchor="end">{tick * 100:+.0f}%</text>')
        tick += step
    zero = f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>'
    bench_line = f'<polyline class="bench" fill="none" points="{line(bpts)}"/>' if bpts else ""
    legend_b = (f'<span><i class="sw bench"></i>{esc(bench_name)} {bpts[-1][1] * 100:+.1f}%</span>' if bpts else "")
    return (f'<div class="perf"><div class="perf-legend"><span><i class="sw port"></i>Portfolio {pts[-1][1] * 100:+.1f}%</span>'
            f'{legend_b}<span class="perf-range">{pts[0][0]:%d %b %Y} to {pts[-1][0]:%d %b %Y}</span></div>'
            f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" aria-label="Portfolio return versus benchmark">'
            f'{"".join(grid)}{zero}{bench_line}<polyline class="port" fill="none" points="{line(pts)}"/></svg></div>')


def _nice_step(span: float) -> float:
    raw = span / 4 or 0.01
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def donut_svg(title: str, items: list[tuple[str, float]]) -> str:
    if not items:
        return ""
    r, cx, cy, stroke = 52, 70, 70, 22
    circ = 2 * math.pi * r
    arcs, offset = [], 0.0
    for i, (_, share) in enumerate(items):
        length = share * circ
        arcs.append(f'<circle r="{r}" cx="{cx}" cy="{cy}" fill="none" stroke="{SLICE_COLOURS[i % len(SLICE_COLOURS)]}" '
                    f'stroke-width="{stroke}" stroke-dasharray="{length:.2f} {circ - length:.2f}" '
                    f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 {cx} {cy})"/>')
        offset += length
    legend = "".join(f'<div><i class="sw" style="background:{SLICE_COLOURS[i % len(SLICE_COLOURS)]}"></i>'
                     f'<span class="nm">{esc(name)}</span><b>{share * 100:.1f}%</b></div>' for i, (name, share) in enumerate(items))
    return (f'<div class="alloc"><div class="alloc-title">{esc(title)}</div><div class="alloc-body">'
            f'<svg viewBox="0 0 140 140" width="140" height="140" role="img" aria-label="{esc(title)}">{"".join(arcs)}</svg>'
            f'<div class="alloc-legend">{legend}</div></div></div>')
