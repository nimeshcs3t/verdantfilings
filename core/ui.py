"""Styling and small HTML helpers. All user or remote text passes through esc()."""
from __future__ import annotations

import hashlib
import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from .db import as_utc, utcnow

LIGHT = {"ink": "#1B2430", "muted": "#5F6B7A", "rule": "#E3E7E5", "pine": "#1F6B4F", "pine-soft": "#EDF4F0",
         "amber": "#A8641A", "up": "#1C7C4A", "down": "#B3261E", "card": "#FFFFFF", "body-en": "#2D3643"}
DARK = {"ink": "#E4E9E6", "muted": "#9AA6A0", "rule": "#2A322F", "pine": "#5DBE93", "pine-soft": "#15302A",
        "amber": "#E3A45C", "up": "#5CCB8A", "down": "#F2837A", "card": "#141A18", "body-en": "#CDD5D1"}
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
    chips = "".join(f".chip.c-{k}{{background:{c[2] if dark else c[0]};color:{c[3] if dark else c[1]};}}"
                    for k, c in CHIP.items())
    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&family=Noto+Sans+KR:wght@400;500&family=Noto+Sans+JP:wght@400;500&display=swap');
:root {{ {tokens} }}
.stApp, .stApp p, .stApp li, .stApp input, .stApp textarea, .stApp label, .stApp button,
.stApp h1, .stApp h2, .stApp h3 {{ font-family:'Public Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }}
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
.fl-flag {{ display:inline-block; font-size:.72rem; font-weight:600; color:var(--amber); border:1px solid var(--amber);
           border-radius:10px; padding:0 7px; margin-left:8px; vertical-align:2px; }}
.logo {{ display:inline-flex; width:24px; height:24px; border-radius:6px; align-items:center; justify-content:center;
        font-size:.66rem; font-weight:700; color:#fff; margin-right:9px; vertical-align:-6px; letter-spacing:.02em;
        flex-shrink:0; }}
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

.empty {{ border:1px dashed var(--rule); padding:18px 20px; color:var(--muted); margin:8px 0 16px; border-radius:6px; }}
.brand {{ font-size:2.2rem; font-weight:700; letter-spacing:-0.02em; color:var(--ink); margin:3rem 0 .3rem; }}
@media (max-width:640px) {{
  [data-testid="stMainBlockContainer"], .block-container {{ padding-left:1rem; padding-right:1rem; padding-top:1rem; }}
  .page-title, .co-name {{ font-size:1.55rem; }}
  .fl-row {{ grid-template-columns:1fr auto; gap:8px; }}
  .fl-date {{ grid-column:1 / -1; padding-top:0; }}
  .fl-title {{ font-size:.98rem; }}
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


def logo_html(name: str, ticker: str, large: bool = False) -> str:
    """Letter logo: initials on a colour picked from the ticker, so each company keeps the same one."""
    words = [w for w in (name or ticker or "?").replace("-", " ").split() if w[:1].isalnum()]
    initials = "".join(w[0] for w in words[:2]).upper() or (ticker or "?")[:2].upper()
    colour = LOGO_COLOURS[int(hashlib.md5((ticker or name or "").encode()).hexdigest(), 16) % len(LOGO_COLOURS)]
    return f'<span class="logo{" lg" if large else ""}" style="background:{colour}" aria-hidden="true">{esc(initials)}</span>'


def chip_html(key: str) -> str:
    from services.classify import label
    return f'<span class="chip c-{esc(key)}">{esc(label(key))}</span>'


def summary_html(text: str) -> str:
    items = [ln.strip().lstrip("-*• ").strip() for ln in (text or "").splitlines() if ln.strip()]
    items = [i.replace("**", "") for i in items if i]
    return '<div class="summary"><ul>' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul></div>"


def filing_row_html(f: dict, show_company: bool = True, is_new: bool = False, country: str = "",
                    category: str = "other", move: str = "", starred: bool = False) -> str:
    dot = '<span class="fl-new" title="Added in the last 2 hours"></span>' if is_new else ""
    star = '<span class="fl-star" title="Starred">★</span>' if starred else ""
    where = f'<span class="fl-tk">{esc(country)}</span>' if country else ""
    company = (f'<div class="fl-co">{dot}{star}{logo_html(f["company_name"], f["ticker"])}{esc(f["company_name"])}'
               f'<span class="fl-tk">{esc(f["ticker"])}</span>{where}</div>' if show_company else "")
    lead = "" if show_company else dot + star
    flag = '<span class="fl-flag">Price sensitive</span>' if f.get("price_sensitive") else ""
    orig = ""
    if f.get("title_local") and f["title_local"] != f.get("title_en"):
        orig = f'<div class="fl-orig ko">{esc(f["title_local"])}</div>'
    return (f'<div class="fl-row"><div class="fl-date">{short_date(f["filed_date"])}</div>'
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
