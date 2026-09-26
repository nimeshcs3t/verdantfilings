"""Styling and small HTML helpers. All user or remote text passes through esc()."""
from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from .db import as_utc, utcnow

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&family=Noto+Sans+KR:wght@400;500&display=swap');
:root { --ink:#1B2430; --muted:#5F6B7A; --rule:#E3E7E5; --pine:#1F6B4F; --pine-soft:#EDF4F0; --amber:#A8641A; }
.stApp, .stApp p, .stApp li, .stApp input, .stApp textarea, .stApp label, .stApp button,
.stApp h1, .stApp h2, .stApp h3 { font-family:'Public Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif; }
[data-testid="stMainBlockContainer"], .block-container { max-width:1120px; padding-top:2rem; }
.stAppDeployButton, [data-testid="stDecoration"], footer { display:none !important; }
.ko { font-family:'Noto Sans KR', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; }

.page-title { font-size:2rem; font-weight:700; letter-spacing:-0.015em; color:var(--ink); margin:0 0 .15rem; line-height:1.15; }
.page-sub { color:var(--muted); margin:0 0 1.25rem; font-size:1rem; }
.section { font-size:1.05rem; font-weight:600; color:var(--ink); margin:1.5rem 0 .4rem; }

.fl-row { display:grid; grid-template-columns:58px 1fr auto; gap:16px; padding:14px 0 6px; border-top:1px solid var(--rule); }
.fl-date { color:var(--muted); font-size:.85rem; padding-top:2px; font-variant-numeric:tabular-nums; }
.fl-co { font-weight:600; color:var(--ink); font-size:.93rem; }
.fl-tk { color:var(--muted); font-weight:400; margin-left:6px; font-size:.85rem; font-variant-numeric:tabular-nums; }
.fl-title { font-size:1.03rem; color:var(--ink); margin-top:2px; line-height:1.4; }
.fl-orig { color:var(--muted); font-size:.85rem; margin-top:2px; }
.fl-tk + .fl-tk { margin-left:10px; }
.fl-flag { display:inline-block; font-size:.72rem; font-weight:600; color:var(--amber); border:1px solid var(--amber); border-radius:3px; padding:0 5px; margin-left:8px; vertical-align:2px; }
.fl-link a { color:var(--pine); text-decoration:none; font-size:.86rem; font-weight:500; white-space:nowrap; }
.fl-link a:hover, .fl-link a:focus-visible { text-decoration:underline; }
.fl-new { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--amber); margin-right:7px; vertical-align:2px; }

.summary { background:var(--pine-soft); border-left:3px solid var(--pine); padding:10px 14px; margin:2px 0 10px; }
.summary ul { margin:0; padding-left:1.1rem; } .summary li { margin:3px 0; line-height:1.45; }
.body-en { max-height:320px; overflow:auto; white-space:pre-wrap; font-size:.9rem; line-height:1.55; color:#2d3643;
  border:1px solid var(--rule); padding:10px 12px; }

.news { padding:10px 0; border-top:1px solid var(--rule); }
.news a { color:var(--ink); text-decoration:none; font-weight:500; line-height:1.38; }
.news a:hover, .news a:focus-visible { color:var(--pine); text-decoration:underline; }
.news-meta { color:var(--muted); font-size:.8rem; margin-top:3px; }
.news-meta span + span { margin-left:10px; }
.news-orig { color:var(--muted); font-size:.82rem; margin-top:2px; }

.co-name { font-size:2rem; font-weight:700; letter-spacing:-0.015em; color:var(--ink); margin:0; line-height:1.15; }
.co-meta { color:var(--muted); margin:.25rem 0 1rem; }
.co-meta span + span { margin-left:14px; }

.msg { padding:10px 0; border-top:1px solid var(--rule); }
.msg-who { font-weight:600; font-size:.88rem; color:var(--ink); }
.msg-when { color:var(--muted); font-size:.8rem; margin-left:8px; font-weight:400; }
.msg-body { margin-top:3px; white-space:pre-wrap; overflow-wrap:anywhere; line-height:1.5; }

.empty { border:1px dashed #C9D1CC; padding:18px 20px; color:var(--muted); margin:8px 0 16px; }
.brand { font-size:2.2rem; font-weight:700; letter-spacing:-0.02em; color:var(--ink); margin:3rem 0 .3rem; }
@media (max-width:640px) { .fl-row { grid-template-columns:1fr auto; } .fl-date { grid-column:1 / -1; } }
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


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


def summary_html(text: str) -> str:
    items = [ln.strip().lstrip("-*• ").strip() for ln in (text or "").splitlines() if ln.strip()]
    items = [i.replace("**", "") for i in items if i]
    return '<div class="summary"><ul>' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul></div>"


def filing_row_html(f: dict, show_company: bool = True, is_new: bool = False, country: str = "") -> str:
    dot = '<span class="fl-new" title="Added in the last 2 hours"></span>' if is_new else ""
    where = f'<span class="fl-tk">{esc(country)}</span>' if country else ""
    company = (f'<div class="fl-co">{dot}{esc(f["company_name"])}<span class="fl-tk">{esc(f["ticker"])}</span>'
               f'{where}</div>' if show_company else "")
    title_style = "" if show_company else ' style="margin-top:0"'
    title_dot = dot if not show_company else ""
    orig = ""
    if f.get("title_local") and f["title_local"] != f.get("title_en"):
        orig = f'<div class="fl-orig ko">{esc(f["title_local"])}</div>'
    flag = '<span class="fl-flag">Price sensitive</span>' if f.get("price_sensitive") else ""
    return (f'<div class="fl-row"><div class="fl-date">{short_date(f["filed_date"])}</div>'
            f'<div>{company}<div class="fl-title"{title_style}>{title_dot}{esc(f["title_en"])}{flag}</div>{orig}</div>'
            f'<div class="fl-link"><a href="{esc(f["url"])}" target="_blank" rel="noopener noreferrer">Original</a></div></div>')


def news_html(items: list[dict]) -> str:
    out = []
    for n in items:
        orig = f'<div class="news-orig ko">{esc(n["title_local"])}</div>' if n.get("title_local") else ""
        meta = "".join(f"<span>{esc(x)}</span>" for x in (n.get("source"), relative_time(n.get("published"))) if x)
        out.append(f'<div class="news"><a href="{esc(n["link"])}" target="_blank" rel="noopener noreferrer">'
                   f'{esc(n["title"])}</a>{orig}<div class="news-meta">{meta}</div></div>')
    return "".join(out)
