"""Technical chart for the company page: price with EMA 21 / EMA 50 and filing dates, volume, and RSI 14."""
from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from core.ui import esc, html_block, is_dark
from services import technicals

RANGES = {"3M": 92, "6M": 183, "1Y": 366, "2Y": 732}


@st.cache_data(ttl=3600, show_spinner=False)
def _bars(market: str, ticker: str) -> list[dict]:
    return technicals.with_indicators(technicals.ohlcv(market, ticker, "2y"))


def _colors() -> dict:
    return {"price": "#D9DEE0", "e21": "#FFAE1A", "e50": "#59C3E8", "up": "#33D17A", "down": "#FF5A4E",
            "grid": "#1A1F22", "muted": "#7C878C", "band": "#0E1A1F", "filing": "#FFAE1A", "rsi": "#C792EA"}


def technical_chart(market: str, ticker: str, filing_dates: list[date], key: str) -> None:
    with st.spinner("Loading price and volume"):
        rows = _bars(market, ticker)
    if not rows:
        st.caption("No daily price and volume data for this share.")
        return
    period = st.segmented_control("Period", list(RANGES), default="6M", key=f"tc-range-{key}",
                                  label_visibility="collapsed") or "6M"
    since = rows[-1]["date"] - timedelta(days=RANGES[period])
    df = pd.DataFrame([r for r in rows if r["date"] >= since])
    df["date"] = pd.to_datetime(df["date"])
    df["dir"] = (df["close"] >= df["open"]).map({True: "up", False: "down"})
    c = _colors()
    axis = dict(gridColor=c["grid"], domainColor=c["grid"], tickColor=c["grid"], labelColor=c["muted"],
                titleColor=c["muted"], labelFontSize=11)
    x = alt.X("date:T", title=None, axis=alt.Axis(format="%d %b", labelOverlap=True, **axis))
    x_quiet = alt.X("date:T", title=None, axis=alt.Axis(labels=False, ticks=False, **axis))   # date labels on the bottom panel only
    zoom = alt.selection_interval(bind="scales", encodings=["x"])
    tip = [alt.Tooltip("date:T", title="Date", format="%d %b %Y"), alt.Tooltip("close:Q", title="Close", format=",.2f"),
           alt.Tooltip("ema21:Q", title="EMA 21", format=",.2f"), alt.Tooltip("ema50:Q", title="EMA 50", format=",.2f"),
           alt.Tooltip("volume:Q", title="Volume", format=",.0f"), alt.Tooltip("rsi:Q", title="RSI 14", format=".0f")]

    long = df.melt(id_vars=["date"], value_vars=["ema21", "ema50"], var_name="series", value_name="value").dropna()
    names = {"ema21": "EMA 21", "ema50": "EMA 50"}
    long["series"] = long["series"].map(names)
    lo, hi = float(df[["low", "ema21", "ema50"]].min(numeric_only=True).min()), float(df["high"].max())
    pad = (hi - lo) * 0.06
    price = alt.Chart(long).mark_line(strokeWidth=1.6).encode(
        x=x_quiet, y=alt.Y("value:Q", title=None, scale=alt.Scale(domain=[lo - pad, hi + pad], zero=False), axis=alt.Axis(**axis)),
        color=alt.Color("series:N", scale=alt.Scale(domain=list(names.values()), range=[c["e21"], c["e50"]]),
                        legend=alt.Legend(orient="top", direction="horizontal", title=None, labelColor=c["muted"],
                                          symbolType="stroke", offset=4)))
    yscale = alt.Scale(domain=[lo - pad, hi + pad], zero=False)
    candle_color = alt.Color("dir:N", scale=alt.Scale(domain=["up", "down"], range=[c["up"], c["down"]]), legend=None)
    wick = alt.Chart(df).mark_rule(strokeWidth=1).encode(
        x=x_quiet, y=alt.Y("low:Q", scale=yscale, title=None), y2="high:Q", color=candle_color)
    body = alt.Chart(df).mark_bar(size=max(1.5, min(7, 520 / max(len(df), 1)))).encode(
        x=x_quiet, y=alt.Y("open:Q", scale=yscale, title=None), y2="close:Q", color=candle_color, tooltip=tip)
    hover = alt.Chart(df).mark_rule(opacity=0).encode(x=x_quiet, tooltip=tip)
    layers = [wick, body, price, hover]
    marks = [d for d in filing_dates if d >= since]
    if marks:
        fdf = pd.DataFrame({"date": pd.to_datetime(sorted(set(marks)))})
        layers.append(alt.Chart(fdf).mark_rule(color=c["filing"], strokeDash=[2, 3], opacity=0.6).encode(
            x=x_quiet, tooltip=[alt.Tooltip("date:T", title="Filing", format="%d %b %Y")]))
    top = alt.layer(*layers).resolve_scale(color="independent").properties(height=300).add_params(zoom)

    vol = alt.Chart(df).mark_bar(opacity=0.75).encode(
        x=x_quiet, y=alt.Y("volume:Q", title="Volume", axis=alt.Axis(format="~s", tickCount=3, **axis)),
        color=alt.Color("dir:N", scale=alt.Scale(domain=["up", "down"], range=[c["up"], c["down"]]), legend=None),
        tooltip=tip).properties(height=90).add_params(zoom)

    band = alt.Chart(pd.DataFrame({"y": [30], "y2": [70]})).mark_rect(color=c["band"], opacity=0.9).encode(
        y=alt.Y("y:Q", scale=alt.Scale(domain=[0, 100])), y2="y2:Q")
    lines = alt.Chart(pd.DataFrame({"y": [30, 70]})).mark_rule(color=c["muted"], strokeDash=[3, 3], opacity=0.7).encode(y="y:Q")
    rsi_line = alt.Chart(df.dropna(subset=["rsi"])).mark_line(color=c["rsi"], strokeWidth=1.3).encode(
        x=x, y=alt.Y("rsi:Q", title="RSI 14", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(values=[30, 50, 70], **axis)),
        tooltip=tip)
    bottom = alt.layer(band, lines, rsi_line).properties(height=110).add_params(zoom)

    chart = (alt.vconcat(top, vol, bottom, spacing=6).resolve_scale(x="shared", color="independent")
             .configure_view(stroke=None).configure(background="transparent", font="Plex Mono"))
    st.altair_chart(chart, width="stretch")
    st.caption("Dotted amber lines mark filing dates.") if marks else None

    r = technicals.reading(rows)
    if r:
        parts = []
        if r.get("ema21") and r.get("ema50"):
            above = [n for n, v in (("EMA 21", r["ema21"]), ("EMA 50", r["ema50"])) if r["close"] > v]
            parts.append("Price is above " + " and ".join(above) if above else "Price is below EMA 21 and EMA 50")
            parts.append("EMA 21 is " + ("above" if r["ema21"] > r["ema50"] else "below") + " EMA 50")
        if r.get("rsi") is not None:
            zone = "overbought (above 70)" if r["rsi"] > 70 else "oversold (below 30)" if r["rsi"] < 30 else "neutral"
            parts.append(f"RSI {r['rsi']:.0f}, {zone}")
        if r.get("vol_ratio"):
            parts.append(f"last volume {r['vol_ratio']:.1f}× its 50-day average")
        html_block(f'<div class="tech-read">{esc("; ".join(parts))}. As of {r["date"]:%d %b %Y}. '
                   'Drag on the chart to zoom, double-click to reset. Not investment advice.</div>')
