from datetime import timedelta

import streamlit as st

from core.ui import esc, html_block, logo_html, page_header
from services import portfolio, valuation, watch
from services.pipeline import get_company
from sources import get_source

from .components import price_history

COLOURS = ["#1F6B4F", "#2E5E8C", "#C07A2C"]


def _options(uid: int) -> dict[str, tuple[str, str]]:
    out = {}
    for w in watch.get_watchlist(uid):
        out[f'{w["name_en"]} ({w["ticker"]}, {get_source(w["market"]).country})'] = (w["market"], w["ticker"])
    for h in portfolio.current_holdings(uid):
        if (h["market"], h["ticker"]) not in out.values() and h["market"] != portfolio.OTHER:
            name = (get_company(h["market"], h["ticker"], resolve=False) or {}).get("name_en", h["ticker"])
            src = get_source(h["market"])
            out[f'{name} ({h["ticker"]}, {src.country if src else h["market"]})'] = (h["market"], h["ticker"])
    return out


def race_svg(series: list[tuple[str, list]]) -> str:
    """Prices rebased to 0% at the start, one line per company."""
    series = [(n, h) for n, h in series if len(h) > 5]
    if not series:
        return ""
    w, ht, pad_l, pad = 720, 220, 44, 12
    start = max(h[0][0] for _, h in series)
    pts = [(n, [(d, v / next(x for dd, x in h if dd >= start) - 1) for d, v in h if d >= start]) for n, h in series]
    vals = [v for _, p in pts for _, v in p] + [0]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 0.01
    t0, t1 = start.toordinal(), max(p[-1][0].toordinal() for _, p in pts)
    x = lambda d: pad_l + (d.toordinal() - t0) / max(1, t1 - t0) * (w - pad_l - pad)
    y = lambda v: pad + (hi - v) / span * (ht - 2 * pad)
    lines = "".join(f'<polyline fill="none" stroke="{COLOURS[i]}" stroke-width="2" vector-effect="non-scaling-stroke" '
                    f'points="{" ".join(f"{x(d):.1f},{y(v):.1f}" for d, v in p)}"/>' for i, (_, p) in enumerate(pts))
    legend = "".join(f'<span><i class="sw" style="background:{COLOURS[i]}"></i>{esc(n)} {p[-1][1] * 100:+.1f}%</span>'
                     for i, (n, p) in enumerate(pts))
    zero = f'<line x1="{pad_l}" x2="{w - pad}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>'
    return (f'<div class="perf"><div class="perf-legend">{legend}<span class="perf-range">since {start:%d %b %Y}</span></div>'
            f'<svg viewBox="0 0 {w} {ht}" preserveAspectRatio="none" role="img" aria-label="Price comparison">{zero}{lines}</svg></div>')


def page() -> None:
    user = st.session_state["user"]
    page_header("Compare", "Two or three companies side by side: valuation, growth, margins and price performance.")
    options = _options(user["id"])
    if len(options) < 2:
        html_block('<div class="empty">Add at least two companies to your watchlist or portfolio to compare them.</div>')
        return
    picked = st.multiselect("Companies", list(options), max_selections=3, default=list(options)[:2], key="cmp-pick")
    if len(picked) < 2:
        st.caption("Pick two or three companies.")
        return
    cols = []
    for label in picked:
        m, t = options[label]
        comp = get_company(m, t, resolve=False) or {"name_en": t}
        hist = price_history(m, t)
        cols.append((comp["name_en"], m, t, hist, valuation.quick_view(m, t, hist, portfolio.currency_for(m, t))))
    head = "".join(f'<th class="num">{logo_html(n, t, market=m)}{esc(n)}</th>' for n, m, t, _, _ in cols)
    body = "".join(f'<tr><td>{esc(label)}</td>' + "".join(f'<td class="num">{esc(valuation.fmt(q, key, kind))}</td>'
                                                           for *_, q in cols) + "</tr>" for label, key, kind in valuation.ROWS)
    periods = "".join(f'<td class="num">{esc(q.get("fin_period") or "–")}</td>' for *_, q in cols)
    html_block(f'<div class="tbl-wrap"><table class="tbl"><tr><th></th>{head}</tr>{body}'
               f'<tr><td>Financial year</td>{periods}</tr></table></div>')
    html_block(race_svg([(n, h) for n, _, _, h, _ in cols]))
    st.caption("Prices and ratios in each share's trading currency; financial figures converted at today's exchange rate. "
               "Figures from official sources where available (Korea, USA, Taiwan, Europe/UK). Not investment advice.")
