"""Styling and small HTML helpers. All user or remote text passes through esc()."""
from __future__ import annotations

import hashlib
import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from .db import as_utc, utcnow

LIGHT = {"ink": "#17212B", "muted": "#5E6A73", "rule": "#E2E7E4", "pine": "#1E6A4E", "pine-soft": "#EAF2EE",
         "amber": "#A9621B", "up": "#1B7A47", "down": "#B4321F", "card": "#FFFFFF", "body-en": "#2B3540",
         "surface": "#F4F6F5", "rule-strong": "#C9D2CD"}
DARK = {"ink": "#E6ECE9", "muted": "#96A39D", "rule": "#27302D", "pine": "#62C59A", "pine-soft": "#16302A",
        "amber": "#E0A35E", "up": "#5FCB8C", "down": "#F08A7E", "card": "#151C1A", "body-en": "#CBD4D0",
        "surface": "#18201E", "rule-strong": "#38433F"}
# Category chip colours: (light background, light text, dark background, dark text)
CHIP = {
    "earnings": ("#E3F4EA", "#17613A", "#173726", "#8FD8AE"), "dividend": ("#E0F2F1", "#11625C", "#12332F", "#86D4CC"),
    "buyback": ("#E6EEFB", "#24508F", "#18263D", "#9EBCEB"), "insider": ("#EEF0F2", "#4A5563", "#262B31", "#B7C0CB"),
    "ownership": ("#EFE9FA", "#5A3E93", "#2A2140", "#C7B4EE"), "capital": ("#FBF0DF", "#8A5310", "#3A2A14", "#EDC08A"),
    "mna": ("#FBE7E4", "#9A3324", "#3D1F1B", "#F0A99D"), "contract": ("#E2F3F8", "#1B6178", "#15313B", "#8DCDE2"),
    "periodic": ("#ECEFF3", "#3C4A5C", "#232A33", "#AEBBCB"), "meeting": ("#EEF1F7", "#44527A", "#232838", "#AFBBE0"),
    "board": ("#F4EEE8", "#6B4E33", "#30271F", "#D6BC9F"), "legal": ("#FCE8E8", "#9C2B2B", "#3D1C1C", "#F2A3A3"),
    "other": ("#F1F3F2", "#5F6B7A", "#242A28", "#A9B3AE"),
}
LOGO_COLOURS = ["#1F6B4F", "#2E5E8C", "#8A5A2B", "#6A4C93", "#9C3D54", "#2F7A7A", "#5B6B2E", "#7A4B2F"]


def is_dark() -> bool:
    try:
        return (st.context.theme.type or "light") == "dark"
    except Exception:
        return False


def css() -> str:
    dark = is_dark()
    v = DARK if dark else LIGHT
    tokens = "".join(f"--{k}:{val};" for k, val in v.items())
    chips = "".join(f".chip.c-{k}{{color:{c[3] if dark else c[1]};}}"
                    f".fl-row.c-{k}{{box-shadow:inset 3px 0 0 {c[3] if dark else c[1]};}}"
                    for k, c in CHIP.items())
    if dark:
        chips += (".k-results{background:#173726 !important}.k-meeting{background:#232838 !important}"
                  ".k-dividend{background:#12332F !important}.k-other{background:#242A28 !important}")
    return f"""
<style>
/* IBM Plex (SIL Open Font License), served with the app: no third-party font requests */
@font-face {{ font-family:'IBM Plex Sans'; font-style:normal; font-weight:400; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-latin-400-normal.woff2') format('woff2'); unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD; }}
@font-face {{ font-family:'IBM Plex Sans'; font-style:normal; font-weight:400; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-latin-ext-400-normal.woff2') format('woff2'); unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF; }}
@font-face {{ font-family:'IBM Plex Sans'; font-style:normal; font-weight:500; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-latin-500-normal.woff2') format('woff2'); unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD; }}
@font-face {{ font-family:'IBM Plex Sans'; font-style:normal; font-weight:500; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-latin-ext-500-normal.woff2') format('woff2'); unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF; }}
@font-face {{ font-family:'IBM Plex Sans'; font-style:normal; font-weight:600; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-latin-600-normal.woff2') format('woff2'); unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD; }}
@font-face {{ font-family:'IBM Plex Sans'; font-style:normal; font-weight:600; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-latin-ext-600-normal.woff2') format('woff2'); unicode-range:U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF; }}
@font-face {{ font-family:'IBM Plex Sans Condensed'; font-style:normal; font-weight:500; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-condensed-latin-500-normal.woff2') format('woff2'); }}
@font-face {{ font-family:'IBM Plex Sans Condensed'; font-style:normal; font-weight:600; font-display:swap; src:url('app/static/fonts/ibm-plex-sans-condensed-latin-600-normal.woff2') format('woff2'); }}
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500&family=Noto+Sans+JP:wght@400;500&display=swap');
:root {{ {tokens} }}
.stApp, .stApp p, .stApp li, .stApp input, .stApp textarea, .stApp label, .stApp button,
.stApp h1, .stApp h2, .stApp h3, .stApp td, .stApp th {{ font-family:'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }}
[data-testid="stMainBlockContainer"], .block-container {{ max-width:1120px; padding-top:2rem; }}
.stAppDeployButton, [data-testid="stDecoration"], footer {{ display:none !important; }}
.ko {{ font-family:'Noto Sans KR', 'Noto Sans JP', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; }}

.page-title {{ font-size:2rem; font-weight:700; letter-spacing:-0.015em; color:var(--ink); margin:0 0 .15rem; line-height:1.15; }}
.page-sub {{ color:var(--muted); margin:0 0 1rem; font-size:1rem; }}
.section {{ font-size:1.05rem; font-weight:600; color:var(--ink); margin:1.5rem 0 .4rem; }}

.brief {{ display:flex; flex-wrap:wrap; gap:6px; margin:0 0 14px; }}
.brief .chip {{ margin-left:0; font-size:.78rem; padding:3px 9px; }}
.chip {{ display:inline-block; font-size:.72rem; font-weight:600; padding:1px 8px; border-radius:10px; margin-left:8px;
        vertical-align:2px; white-space:nowrap; }}
{chips}
.fl-flag.imp {{ color:var(--pine); border-color:var(--pine); }}
.fl-mark {{ display:inline-block; font-size:.7rem; font-weight:600; border-radius:999px; padding:0 7px; margin-left:6px; vertical-align:1px; }}
.fl-mark.keep {{ background:var(--pine); color:#fff; }} .fl-mark.note {{ border:1px solid var(--pine); color:var(--pine); }}
.jn-prev {{ font-size:.84rem; border-left:2px solid var(--rule); padding:2px 0 2px 8px; margin:0 0 8px; }}
.jn-prev div {{ color:var(--body-en); margin-top:2px; white-space:pre-wrap; }}
.jn-filing {{ font-size:.8rem; color:var(--muted); margin-top:2px; }} .jn-filing a {{ color:var(--pine); }} .fl-mark.once {{ border:1px dashed var(--muted); color:var(--muted); }}
div[class*="st-key-flag-"] button {{ font-size:.74rem !important; min-height:0 !important; padding:1px 9px !important; }}
div[class*="st-key-flag-"] {{ margin:-6px 0 2px; }}
.cal-ev.est {{ background:transparent !important; border:1px dashed var(--muted); color:var(--muted); }}
.heat {{ border:1px solid var(--rule); border-radius:8px; padding:4px; margin:4px 0 14px; }}
.heat svg {{ width:100%; height:340px; display:block; }}
.goal-hit {{ fill:var(--amber); }}
.fl-flag {{ display:inline-block; font-size:.72rem; font-weight:600; color:var(--amber); border:1px solid var(--amber);
           border-radius:10px; padding:0 7px; margin-left:8px; vertical-align:2px; }}
.logo {{ display:inline-flex; width:24px; height:24px; border-radius:6px; align-items:center; justify-content:center;
        font-size:.66rem; font-weight:700; color:#fff; margin-right:9px; vertical-align:-6px; letter-spacing:.02em;
        flex-shrink:0; }}
.logo {{ position:relative; overflow:hidden; }}
.logo img {{ position:absolute; inset:0; width:100%; height:100%; object-fit:contain; background:#fff; padding:2px;
            box-sizing:border-box; border-radius:inherit; }}
.logo.lg {{ width:44px; height:44px; font-size:1rem; border-radius:10px; margin-right:14px; vertical-align:-10px; }}

.fl-row {{ display:grid; grid-template-columns:58px 1fr auto; gap:16px; padding:14px 0 6px; border-top:1px solid var(--rule); }}
.fl-date {{ color:var(--muted); font-size:.85rem; padding-top:3px; font-variant-numeric:tabular-nums; }}
.fl-co {{ font-weight:600; color:var(--ink); font-size:.93rem; display:flex; align-items:center; flex-wrap:wrap; }}
.fl-tk {{ color:var(--muted); font-weight:400; margin-left:6px; font-size:.85rem; font-variant-numeric:tabular-nums; }}
.fl-tk + .fl-tk {{ margin-left:10px; }}
.fl-title {{ font-size:1.03rem; color:var(--ink); margin-top:3px; line-height:1.4; }}
.fl-orig {{ color:var(--muted); font-size:.85rem; margin-top:2px; }}
.fl-side {{ text-align:right; display:flex; flex-direction:column; align-items:flex-end; gap:4px; }}
.fl-link a {{ color:var(--pine); text-decoration:none; font-size:.86rem; font-weight:500; white-space:nowrap; }}
.fl-link a:hover, .fl-link a:focus-visible {{ text-decoration:underline; }}
.fl-new {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--amber); margin-right:7px; vertical-align:2px; }}
.fl-star {{ color:var(--amber); margin-right:6px; }}
.mv {{ font-size:.8rem; font-weight:600; white-space:nowrap; font-variant-numeric:tabular-nums; }}
.mv.up {{ color:var(--up); }} .mv.down {{ color:var(--down); }} .mv.flat {{ color:var(--muted); }}
.spark polyline {{ stroke:var(--muted); }} .spark.up polyline {{ stroke:var(--up); }} .spark.down polyline {{ stroke:var(--down); }}

.chart {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px 8px; margin:6px 0 14px; }}
.chart svg {{ width:100%; height:170px; display:block; }}
.chart .line {{ stroke:var(--pine); stroke-width:1.8; vector-effect:non-scaling-stroke; }}
.chart .area {{ fill:var(--pine-soft); }}
.chart .dots circle {{ fill:var(--amber); stroke:var(--card); stroke-width:1.5; vector-effect:non-scaling-stroke; }}
.chart-head {{ display:flex; gap:14px; align-items:baseline; font-size:.85rem; color:var(--muted); margin-bottom:4px; flex-wrap:wrap; }}
.chart-last {{ margin-left:auto; }}
.chart-foot {{ font-size:.75rem; color:var(--muted); margin-top:4px; }}

.summary {{ background:var(--pine-soft); border-left:3px solid var(--pine); padding:10px 14px; margin:2px 0 10px; color:var(--ink); }}
.summary ul {{ margin:0; padding-left:1.1rem; }} .summary li {{ margin:3px 0; line-height:1.45; }}
.answer {{ border-left:3px solid var(--amber); padding:8px 12px; margin:6px 0 10px; color:var(--ink); line-height:1.5; }}
.body-en {{ max-height:320px; overflow:auto; white-space:pre-wrap; font-size:.9rem; line-height:1.55; color:var(--body-en);
           border:1px solid var(--rule); padding:10px 12px; }}

.group-head {{ display:flex; align-items:center; margin:22px 0 2px; font-weight:600; color:var(--ink); }}
.group-head .fl-tk {{ margin-left:8px; }}
.group-head .count {{ color:var(--muted); font-weight:400; font-size:.85rem; margin-left:10px; }}

.news {{ padding:10px 0; border-top:1px solid var(--rule); }}
.news a {{ color:var(--ink); text-decoration:none; font-weight:500; line-height:1.38; }}
.news a:hover, .news a:focus-visible {{ color:var(--pine); text-decoration:underline; }}
.news-meta {{ color:var(--muted); font-size:.8rem; margin-top:3px; }}
.news-meta span + span {{ margin-left:10px; }}
.news-orig {{ color:var(--muted); font-size:.82rem; margin-top:2px; }}

.co-head {{ display:flex; align-items:center; }}
.co-name {{ font-size:2rem; font-weight:700; letter-spacing:-0.015em; color:var(--ink); margin:0; line-height:1.15; }}
.co-meta {{ color:var(--muted); margin:.35rem 0 1rem; }}
.co-meta span + span {{ margin-left:14px; }}

.wl-row {{ display:flex; align-items:center; gap:10px; }}
.wl-price {{ display:flex; align-items:center; gap:8px; justify-content:flex-end; }}

.msg {{ padding:10px 0; border-top:1px solid var(--rule); }}
.msg-who {{ font-weight:600; font-size:.88rem; color:var(--ink); }}
.msg-when {{ color:var(--muted); font-size:.8rem; margin-left:8px; font-weight:400; }}
.msg-body {{ margin-top:3px; white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.5; color:var(--ink); }}

.brief-box {{ border:1px solid var(--rule); border-left:3px solid var(--pine); border-radius:6px; padding:12px 14px;
              margin:4px 0 12px; color:var(--ink); line-height:1.55; }}
.brief-box .meta {{ color:var(--muted); font-size:.78rem; margin-top:6px; }}
.events {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 12px; }}
.event {{ border:1px solid var(--rule); border-radius:6px; padding:6px 10px; font-size:.85rem; color:var(--ink); }}
.event b {{ color:var(--pine); margin-right:6px; font-variant-numeric:tabular-nums; }}
.ev-row {{ padding:8px 0; border-top:1px solid var(--rule); font-size:.9rem; color:var(--ink); }}
.ev-row b {{ color:var(--pine); margin-right:8px; font-variant-numeric:tabular-nums; }}
.ev-row span {{ color:var(--muted); }}
.fin-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:12px; margin:6px 0 10px; }}
.fin {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px 6px; }}
.fin-head {{ display:flex; justify-content:space-between; font-size:.85rem; color:var(--muted); margin-bottom:4px; }}
.fin-head b {{ color:var(--ink); }}
.fin svg {{ width:100%; height:120px; display:block; }}
.fin rect.pos {{ fill:var(--pine); }} .fin rect.neg {{ fill:var(--down); }}
.fin .axis {{ stroke:var(--rule); }}
.fin text {{ font-size:9px; fill:var(--muted); }}
.tbl {{ width:100%; border-collapse:collapse; font-size:.9rem; color:var(--ink); }}
.tbl th {{ text-align:left; color:var(--muted); font-weight:500; font-size:.8rem; padding:6px 8px; border-bottom:1px solid var(--rule); }}
.tbl td {{ padding:8px; border-bottom:1px solid var(--rule); vertical-align:middle; font-variant-numeric:tabular-nums; }}
.tbl td.num, .tbl th.num {{ text-align:right; }}
.tbl-wrap {{ overflow-x:auto; }}
.stat-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin:6px 0 12px; }}
.stat {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px; }}
.stat .k {{ color:var(--muted); font-size:.78rem; }} .stat .v {{ font-size:1.25rem; font-weight:600; color:var(--ink); }}
.ret-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(96px, 1fr)); gap:8px; margin:4px 0 14px; }}
.ret {{ border:1px solid var(--rule); border-radius:8px; padding:8px 10px; }}
.ret .lbl {{ color:var(--muted); font-size:.78rem; font-weight:600; }}
.ret .p {{ font-size:1.08rem; font-weight:600; margin-top:2px; font-variant-numeric:tabular-nums; }}
.ret .b {{ color:var(--muted); font-size:.75rem; margin-top:2px; font-variant-numeric:tabular-nums; }}
.ret .p.up, .ret .b .up {{ color:var(--up); }} .ret .p.down, .ret .b .down {{ color:var(--down); }}
.ret .note {{ color:var(--muted); font-size:.68rem; }}
.perf {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px 8px; margin:4px 0 14px; }}
.perf svg {{ width:100%; height:220px; display:block; }}
.perf .port {{ stroke:var(--pine); stroke-width:2.2; vector-effect:non-scaling-stroke; }}
.perf .bench {{ stroke:var(--muted); stroke-width:1.6; stroke-dasharray:5 4; vector-effect:non-scaling-stroke; }}
.perf .grid {{ stroke:var(--rule); vector-effect:non-scaling-stroke; }}
.perf .zero {{ stroke:var(--muted); stroke-width:1; opacity:.5; vector-effect:non-scaling-stroke; }}
.perf text {{ font-size:10px; fill:var(--muted); }}
.perf-legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:.85rem; color:var(--ink); margin-bottom:6px; align-items:center; }}
.perf-range {{ margin-left:auto; color:var(--muted); font-size:.78rem; }}
.sw {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px; vertical-align:-1px; }}
.sw.port {{ background:var(--pine); }} .sw.bench {{ background:var(--muted); }}
.alloc-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px; margin:4px 0 14px; }}
.alloc {{ border:1px solid var(--rule); border-radius:8px; padding:10px 12px; }}
.alloc-title {{ font-weight:600; color:var(--ink); font-size:.92rem; margin-bottom:6px; }}
.alloc-body {{ display:flex; gap:14px; align-items:center; }}
.alloc-legend {{ flex:1; font-size:.84rem; color:var(--ink); min-width:0; }}
.alloc-legend div {{ display:flex; align-items:center; padding:2px 0; }}
.alloc-legend .nm {{ flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-right:8px; }}
.alloc-legend b {{ font-weight:600; font-variant-numeric:tabular-nums; }}
.tbl td.buy {{ color:var(--up); font-weight:600; }} .tbl td.sell {{ color:var(--down); font-weight:600; }}
.cal-title {{ text-align:center; font-size:1.25rem; font-weight:700; color:var(--ink); }}
.cal-grid {{ display:grid; grid-template-columns:repeat(7, minmax(0, 1fr)); border-top:1px solid var(--rule);
            border-left:1px solid var(--rule); margin:8px 0 14px; }}
.cal-dow {{ font-size:.75rem; color:var(--muted); font-weight:600; padding:6px; border-right:1px solid var(--rule);
           border-bottom:1px solid var(--rule); }}
.cal-day {{ min-height:96px; padding:4px 5px; border-right:1px solid var(--rule); border-bottom:1px solid var(--rule); }}
.cal-day.out {{ opacity:.45; }} .cal-day.today {{ background:var(--pine-soft); }}
.cal-num {{ font-size:.8rem; color:var(--muted); font-weight:600; margin-bottom:2px; }}
.cal-ev {{ display:block; font-size:.72rem; line-height:1.25; padding:2px 4px; margin:2px 0; border-radius:4px;
          text-decoration:none; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:var(--ink); }}
.cal-ev:hover {{ text-decoration:underline; }}
.cal-more {{ font-size:.7rem; color:var(--muted); }}
.k-results {{ background:#E3F4EA; }} .k-meeting {{ background:#EEF1F7; }} .k-dividend {{ background:#E0F2F1; }} .k-other {{ background:#F1F3F2; }}
.cal-dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin:0 8px 0 2px; }}
.cal-agenda {{ display:block; }}
.dash {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:10px; margin:2px 0 14px; }}
.dash .stat .sub {{ color:var(--muted); font-size:.78rem; margin-top:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
.ipo {{ border-top:1px solid var(--rule); padding:14px 0 10px; }}
.ipo .fl-co {{ font-size:1rem; }}
.ipo-facts {{ display:flex; flex-wrap:wrap; gap:6px 18px; margin:6px 0 4px; font-size:.86rem; color:var(--ink); }}
.ipo-facts b {{ color:var(--muted); font-weight:500; margin-right:4px; }}
.ipo-ov {{ font-size:.9rem; line-height:1.5; color:var(--body-en); margin:4px 0; }}
.ss-post {{ border-top:1px solid var(--rule); padding:10px 0; }}
.ss-post a {{ font-weight:600; color:var(--ink); text-decoration:none; }} .ss-post a:hover {{ color:var(--pine); text-decoration:underline; }}
.ss-sum {{ font-size:.88rem; color:var(--body-en); margin:3px 0; line-height:1.45; }}
.ss-meta {{ font-size:.78rem; color:var(--muted); }}
.ss-tag {{ font-size:.68rem; font-weight:600; border:1px solid var(--rule); border-radius:999px; padding:1px 7px; margin-left:8px; vertical-align:2px; color:var(--muted); }}
.ss-tag.mine {{ color:var(--pine); border-color:var(--pine); }} .ss-tag.paid {{ color:var(--amber); border-color:var(--amber); }}
.ipo-brief {{ font-size:.92rem; line-height:1.5; color:var(--body-en); margin:6px 0 4px; max-width:860px; }}
.ipo-brief.none {{ color:var(--muted); font-size:.82rem; font-style:italic; }}
.ipo-src {{ color:var(--muted); font-size:.74rem; }}
.fl-flag.open {{ background:var(--up); color:#fff; border-color:var(--up); }}
.ipo-srcs {{ display:flex; flex-wrap:wrap; gap:6px 8px; }}
.ipo-srcs span {{ border:1px solid var(--rule); border-radius:999px; padding:2px 10px; font-size:.82rem; }}
.ipo-srcs span::before {{ content:"●"; margin-right:6px; }}
.ipo-srcs .s-ok::before {{ color:var(--up); }} .ipo-srcs .s-warning::before, .ipo-srcs .s-paused::before {{ color:var(--amber); }}
.ipo-srcs .s-problem::before {{ color:var(--down); }} .ipo-srcs .s-idle::before {{ color:var(--muted); }}
.ipo-meta {{ font-size:.82rem; color:var(--muted); }}
.ipo-meta a {{ color:var(--pine); text-decoration:none; font-weight:500; }}
.ipo .mv {{ margin-left:8px; }}
.chart .lvl {{ stroke-width:1.4; stroke-dasharray:6 4; vector-effect:non-scaling-stroke; }}
.chart .lvl.target {{ stroke:var(--up); }} .chart .lvl.fair {{ stroke:var(--amber); }}
.lvl-key {{ display:inline-block; margin-right:12px; font-weight:600; }}
.lvl-key.target {{ color:var(--up); }} .lvl-key.fair {{ color:var(--amber); }}
.qv {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(118px, 1fr)); gap:8px; margin:4px 0 12px; }}
.qv .stat .v {{ font-size:1.02rem; }}
.qr svg {{ width:190px; height:auto; border-radius:8px; display:block; }}
.empty {{ border:1px dashed var(--rule); padding:18px 20px; color:var(--muted); margin:8px 0 16px; border-radius:6px; }}
.brand {{ font-size:2.2rem; font-weight:700; letter-spacing:-0.02em; color:var(--ink); margin:3rem 0 .3rem; }}
.st-key-bottomnav {{ display:none !important; }}
@media (max-width:640px) {{
  .st-key-bottomnav {{ display:flex !important; position:fixed; left:0; right:0; bottom:0; z-index:1000; margin:0 !important;
                      background:var(--card); border-top:1px solid var(--rule); justify-content:space-around;
                      padding:2px 2px calc(4px + env(safe-area-inset-bottom)); box-shadow:0 -2px 10px rgba(0,0,0,.06); }}
  .st-key-bottomnav > div {{ flex:1 1 0; min-width:0; }}
  .st-key-bottomnav a {{ flex-direction:column !important; gap:0 !important; padding:4px 2px !important; justify-content:center; }}
  .st-key-bottomnav a p {{ font-size:.66rem !important; }}
  [data-testid="stMainBlockContainer"], .block-container {{ padding-bottom:84px !important; }}
  .tbl {{ font-size:.8rem; }} .tbl td, .tbl th {{ padding:6px 4px; }}
  .tbl .opt {{ display:none; }}
  .stat-grid {{ grid-template-columns:repeat(2, 1fr); gap:6px; }} .stat {{ padding:8px 10px; }} .stat .v {{ font-size:1.05rem; }}
  .ret-grid {{ grid-template-columns:repeat(3, 1fr); gap:6px; }} .ret {{ padding:6px 8px; }} .ret .p {{ font-size:.95rem; }}
  .alloc-body svg {{ width:110px; height:110px; }}
  .fl-row {{ padding:10px 0 4px; }} .chip {{ font-size:.68rem; padding:1px 6px; }}
  .cal-grid {{ display:none; }} .cal-agenda {{ display:block; }}
  .dash {{ grid-template-columns:repeat(2, minmax(0, 1fr)); gap:6px; }}
}}
@media (max-width:640px) {{
  [data-testid="stMainBlockContainer"], .block-container {{ padding-left:1rem; padding-right:1rem; padding-top:1rem; }}
  .page-title, .co-name {{ font-size:1.55rem; }}
  .fl-row {{ grid-template-columns:1fr auto; gap:8px; }}
  .fl-date {{ grid-column:1 / -1; padding-top:0; }}
  .fl-title {{ font-size:.98rem; }}
}}

/* ---- v22 design layer: a quiet register; category shown as a thin coloured spine ---- */
.imp-help {{ font-size:.85rem; color:var(--muted); line-height:1.5; margin-bottom:.6rem; }}
.imp-help b {{ color:var(--ink); font-weight:500; }}
.imp-help ul {{ margin:.25rem 0 .5rem 1.1rem; padding:0; }}

@media (max-width: 640px) {{
  .st-key-today-filters [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap !important; gap:.5rem .75rem !important; }}
  .st-key-today-filters [data-testid="stColumn"] {{ min-width:calc(50% - .4rem) !important; flex:1 1 calc(50% - .4rem) !important; width:calc(50% - .4rem) !important; }}
  .st-key-today-filters [data-testid="stColumn"]:empty, .st-key-today-filters [data-testid="stColumn"]:not(:has(*)) {{ display:none; }}
}}
.stApp {{ color:var(--ink); }}
.page-title, .co-name, .brand {{ font-family:'IBM Plex Sans Condensed', 'IBM Plex Sans', sans-serif; font-weight:600;
  letter-spacing:-0.005em; }}
.page-title, .stApp h1.page-title {{ font-size:1.9rem !important; line-height:1.15 !important; margin:0 0 .25rem !important;
  padding:0 !important; font-family:'IBM Plex Sans Condensed', 'IBM Plex Sans', sans-serif !important; font-weight:600 !important; }}
.page-sub {{ max-width:68ch; font-size:.98rem; line-height:1.5; }}
.co-name {{ font-size:1.9rem; }}
.stApp h2, .stApp h3 {{ font-weight:600; letter-spacing:-0.005em; }}
.stApp [data-testid="stHeading"] h3 {{ font-size:1.12rem; }}

/* filings */
.fl-row {{ grid-template-columns:52px 1fr auto; gap:14px; padding:12px 0 2px 14px; border-top:1px solid var(--rule); }}
.fl-row.nodate {{ grid-template-columns:1fr auto; }}
.fl-date {{ font-size:.8rem; }}
.fl-co {{ font-size:.9rem; font-weight:600; }}
.fl-co .logo {{ width:20px; height:20px; font-size:.58rem; border-radius:5px; margin-right:8px; vertical-align:-5px; }}
.fl-title {{ font-size:1rem; line-height:1.42; margin-top:2px; }}
.chip {{ background:none !important; padding:0; border-radius:0; font-size:.76rem; font-weight:600; margin-left:10px; vertical-align:0; }}
.fl-flag {{ border:none; padding:0; border-radius:0; font-size:.76rem; margin-left:10px; vertical-align:0; }}
.fl-flag.imp {{ color:var(--pine); }}
.fl-mark {{ border:none !important; background:none !important; padding:0; margin-left:10px; font-size:.76rem; vertical-align:0; }}
.fl-mark.keep {{ color:var(--pine); }} .fl-mark.once {{ color:var(--muted); font-style:italic; }} .fl-mark.note {{ color:var(--pine); }}
.fl-link a {{ font-size:.82rem; }}
.day-head {{ display:flex; align-items:baseline; gap:10px; margin:26px 0 0; padding-bottom:2px; }}
.day-head b {{ font-family:'IBM Plex Sans Condensed', 'IBM Plex Sans', sans-serif; font-weight:600; font-size:1.02rem; color:var(--ink); }}
.day-head span {{ color:var(--muted); font-size:.8rem; }}
.day-head + div .fl-row, .day-head + .fl-row {{ border-top:none; }}

/* the quiet action line under each filing */
div[class*="st-key-fil-"] {{ gap:.1rem !important; }}
div[class*="st-key-fil-"] [data-testid="stMarkdownContainer"], div[class*="st-key-fil-"] [data-testid="stMarkdown"] {{ margin-bottom:0 !important; }}
div[class*="st-key-fil-"] .fl-row {{ padding-bottom:6px; }}
div[class*="st-key-fil-"] [data-testid="stElementContainer"] {{ margin:0 !important; }}
div[class*="st-key-frow-"] {{ padding-left:14px; gap:2px !important; margin:0 0 2px; }}
div[class*="st-key-flag-"] {{ margin:0 !important; }}
div[class*="st-key-flag-"] [data-testid="stButtonGroup"] > div {{ gap:6px !important; }}
div[class*="st-key-flag-"] button {{ border:1px solid var(--rule-strong) !important; background:var(--card, transparent) !important;
  color:var(--ink) !important; border-radius:999px !important; padding:0 10px !important; min-height:26px !important;
  height:26px !important; box-shadow:none !important; transition:background .12s, border-color .12s, color .12s; }}
div[class*="st-key-flag-"] button p {{ font-size:.78rem !important; font-weight:500 !important; }}
div[class*="st-key-flag-"] button:hover {{ border-color:var(--ink) !important; }}
/* chosen flag: filled, each in its own colour (Important amber, Keep green, Read once grey) */
div[class*="st-key-flag-"] button[aria-checked="true"], div[class*="st-key-flag-"] button[aria-checked="true"] p {{ color:var(--card) !important; }}
div[class*="st-key-flag-"] button[aria-checked="true"] p {{ font-weight:600 !important; }}
div[class*="st-key-flag-"] button:nth-of-type(1)[aria-checked="true"] {{ background:var(--amber) !important; border-color:var(--amber) !important; }}
div[class*="st-key-flag-"] button:nth-of-type(2)[aria-checked="true"] {{ background:var(--pine) !important; border-color:var(--pine) !important; }}
div[class*="st-key-flag-"] button:nth-of-type(3)[aria-checked="true"] {{ background:var(--muted) !important; border-color:var(--muted) !important; }}
div[class*="st-key-frow-"] [data-testid="stPopover"] button {{ border:none !important; background:none !important;
  color:var(--muted) !important; font-size:.78rem !important; min-height:26px !important; padding:0 6px !important; box-shadow:none !important; }}
div[class*="st-key-frow-"] [data-testid="stPopover"] button p {{ font-size:.78rem !important; }}
div[class*="st-key-frow-"] {{ flex-wrap:wrap !important; }}
div[class*="st-key-fil-"] [data-testid="stExpander"] {{ margin-left:0; width:auto !important; }}
div[class*="st-key-frow-"] > div:has([data-testid="stExpander"]) {{ width:auto !important; flex:0 1 auto !important; }}
div[class*="st-key-frow-"] > div:has(details[open]) {{ flex-basis:100% !important; width:100% !important; }}
div[class*="st-key-fil-"] [data-testid="stExpander"] summary {{ padding:4px 6px !important; }}
div[class*="st-key-fil-"] [data-testid="stExpander"] details {{ border:none !important; background:none !important; }}
div[class*="st-key-fil-"] [data-testid="stExpander"] summary {{ font-size:.8rem; color:var(--muted); min-height:0; }}
div[class*="st-key-fil-"] [data-testid="stExpander"] summary p {{ font-size:.8rem !important; }}
div[class*="st-key-fil-"] [data-testid="stExpander"] summary:hover {{ color:var(--pine); }}
div[class*="st-key-fil-"] [data-testid="stExpanderDetails"] {{ padding:0 0 10px !important; }}

/* figures: a plain strip, not boxed cards */
.stat-grid, .dash, .qv {{ display:flex; flex-wrap:wrap; gap:0; border-top:1px solid var(--rule); border-bottom:1px solid var(--rule);
  margin:6px 0 16px; padding:10px 0; }}
.stat-grid .stat, .dash .stat, .qv .stat {{ border:none; border-radius:0; padding:2px 22px 2px 0; margin:0 22px 0 0;
  border-right:1px solid var(--rule); min-width:0; }}
.stat-grid .stat:last-child, .dash .stat:last-child, .qv .stat:last-child {{ border-right:none; margin-right:0; }}
.stat .k {{ font-size:.78rem; }}
.stat .v {{ font-size:1.18rem; font-weight:600; font-variant-numeric:tabular-nums; margin-top:1px; }}
.dash .stat {{ flex:1 1 0; }}
.dash .stat .sub {{ max-width:260px; }}
.ret, .alloc, .perf, .chart, .fin, .heat {{ border-radius:6px; }}
.empty {{ background:var(--surface); border:none; border-radius:6px; color:var(--muted); }}

/* category filter on Today: outlined, coloured text */
.st-key-today-cats button {{ background:none !important; border:1px solid var(--rule) !important; }}

/* inputs and tabs a touch calmer */
.stApp [data-baseweb="tab-list"] {{ gap:18px; }}
.stApp [data-baseweb="tab"] p {{ font-size:.92rem; }}
@media (max-width:640px) {{
  .fl-row, .fl-row.nodate {{ grid-template-columns:1fr auto; padding-left:12px; }}
  .stat-grid .stat, .dash .stat, .qv .stat {{ flex:1 1 40%; border-right:none; margin:0 0 8px; padding-right:8px; }}
  .page-title, .co-name {{ font-size:1.5rem; }}
}}
</style>
"""


def inject_css() -> None:
    st.markdown(css(), unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value or ""))


def html_block(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = "") -> None:
    sub = f'<p class="page-sub">{esc(subtitle)}</p>' if subtitle else ""
    html_block(f'<h1 class="page-title">{esc(title)}</h1>{sub}')


def short_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def long_date(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def relative_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    delta = utcnow() - as_utc(dt)
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)} min ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} h ago"
    return short_date(as_utc(dt).astimezone(ZoneInfo("Asia/Seoul")).date())


@st.cache_data(ttl=900, show_spinner=False)
def _logo_urls() -> dict:
    try:
        from services.logos import all_logos
        found = all_logos()
    except Exception:
        return {}
    by_ticker = {}
    for (m, t), url in found.items():
        by_ticker.setdefault(t, url)
    return {"pair": found, "ticker": by_ticker}


def logo_html(name: str, ticker: str, large: bool = False, market: str | None = None) -> str:
    """Company logo where one is known, over a letter tile (initials on a colour picked from the ticker).
    If the image fails to load, the letter tile underneath shows instead."""
    words = [w for w in (name or ticker or "?").replace("-", " ").split() if w[:1].isalnum()]
    initials = "".join(w[0] for w in words[:2]).upper() or (ticker or "?")[:2].upper()
    colour = LOGO_COLOURS[int(hashlib.md5((ticker or name or "").encode()).hexdigest(), 16) % len(LOGO_COLOURS)]
    urls = _logo_urls()
    url = (urls.get("pair", {}).get((market, ticker)) if market else None) or urls.get("ticker", {}).get(ticker)
    img = f'<img src="{esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">' if url else ""
    return (f'<span class="logo{" lg" if large else ""}{" has-img" if url else ""}" style="background:{colour}" '
            f'aria-hidden="true">{esc(initials)}{img}</span>')


def chip_html(key: str) -> str:
    from services.classify import label
    return f'<span class="chip c-{esc(key)}">{esc(label(key))}</span>'


def summary_html(text: str) -> str:
    items = [ln.strip().lstrip("-*• ").strip() for ln in (text or "").splitlines() if ln.strip()]
    items = [i.replace("**", "") for i in items if i]
    return '<div class="summary"><ul>' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul></div>"


def filing_row_html(f: dict, show_company: bool = True, is_new: bool = False, country: str = "",
                    category: str = "other", move: str = "", starred: bool = False, important: bool = False,
                    mark: str | None = None, notes: int = 0, show_date: bool = True) -> str:
    dot = '<span class="fl-new" title="Added in the last 2 hours"></span>' if is_new else ""
    star = '<span class="fl-star" title="You marked this Important">★</span>' if starred else ""
    where = f'<span class="fl-tk">{esc(country)}</span>' if country else ""
    company = (f'<div class="fl-co">{dot}{star}{logo_html(f["company_name"], f["ticker"], market=f.get("market"))}{esc(f["company_name"])}'
               f'<span class="fl-tk">{esc(f["ticker"])}</span>{where}</div>' if show_company else "")
    lead = "" if show_company else dot + star
    flag = '<span class="fl-flag">Price sensitive</span>' if f.get("price_sensitive") else ""
    if important:
        flag += '<span class="fl-flag imp" title="Likely to matter, by type and size of the filing">High impact</span>'
    if mark == "keep":
        flag += '<span class="fl-mark keep" title="Kept: never deleted">Keep</span>'
    elif mark == "read_once":
        flag += '<span class="fl-mark once" title="Deleted when an admin frees up space">Read once</span>'
    if notes:
        flag += f'<span class="fl-mark note" title="Your journal notes on this filing">✎ {notes}</span>'
    orig = ""
    if f.get("title_local") and f["title_local"] != f.get("title_en"):
        orig = f'<div class="fl-orig ko">{esc(f["title_local"])}</div>'
    date_cell = f'<div class="fl-date">{short_date(f["filed_date"])}</div>' if show_date else ""
    return (f'<div class="fl-row c-{esc(category)}{"" if show_date else " nodate"}">{date_cell}'
            f'<div>{company}<div class="fl-title">{lead}{esc(f["title_en"])}{chip_html(category)}{flag}</div>{orig}</div>'
            f'<div class="fl-side"><span class="fl-link"><a href="{esc(f["url"])}" target="_blank" '
            f'rel="noopener noreferrer">Original</a></span>{move}</div></div>')


def news_html(items: list[dict]) -> str:
    out = []
    for n in items:
        orig = f'<div class="news-orig ko">{esc(n["title_local"])}</div>' if n.get("title_local") else ""
        meta = "".join(f"<span>{esc(x)}</span>" for x in (n.get("source"), relative_time(n.get("published"))) if x)
        out.append(f'<div class="news"><a href="{esc(n["link"])}" target="_blank" rel="noopener noreferrer">'
                   f'{esc(n["title"])}</a>{orig}<div class="news-meta">{meta}</div></div>')
    return "".join(out)


def day_heading(d: date, count: int) -> None:
    """A day in a list of filings: 'Friday 30 October' and how many filings."""
    today = date.today()
    name = "Today" if d == today else "Yesterday" if d == today - timedelta(days=1) else f"{d:%A} {d.day} {d:%B}"
    html_block(f'<div class="day-head"><b>{esc(name)}</b><span>{count} filing{"s" if count != 1 else ""}</span></div>')
