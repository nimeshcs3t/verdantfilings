"""Terminal frame around every page: a command line, F-key page tabs, market clocks and a status bar.

Keys: F1-F10 open pages, "/" jumps to the command line, Esc clears it. The command line takes a page name
("insiders", "port") or a ticker with an optional market ("SENSO FI", "005930 KR", "AAPL"). Anything else
goes to Search."""
from __future__ import annotations

from datetime import date, timedelta

import streamlit as st
from sqlalchemy import func, or_, select

from . import nav
from .db import as_utc, companies, get_engine, run_log, utcnow

# (page id, tab label, F-key). Pages without a key still get a tab.
TABS = [("today", "Today", "F1"), ("portfolio", "Portfolio", "F2"), ("filings", "Filings", "F3"),
        ("journal", "Journal", "F4"), ("company", "Company", "F5"), ("watchlist", "Watchlist", "F6"),
        ("ideas", "Ideas", "F7"), ("banks", "Bank reports", "F8"), ("substack", "Substack", "F9"),
        ("news", "News", "F10"), ("insiders", "Insiders", ""), ("ipos", "IPOs", ""), ("calendar", "Calendar", ""),
        ("screener", "Screener", ""), ("compare", "Compare", ""), ("search", "Search", ""),
        ("account", "Account", ""), ("admin", "Admin", "")]

ALIASES = {
    "today": "today", "t": "today", "home": "today", "monitor": "today",
    "watchlist": "watchlist", "watch": "watchlist", "wl": "watchlist", "w": "watchlist",
    "company": "company", "companies": "company", "co": "company", "des": "company",
    "filings": "filings", "filing": "filings", "fil": "filings", "f": "filings", "all": "filings",
    "insiders": "insiders", "insider": "insiders", "ins": "insiders", "i": "insiders",
    "portfolio": "portfolio", "port": "portfolio", "pf": "portfolio", "p": "portfolio",
    "ideas": "ideas", "idea": "ideas", "letters": "ideas",
    "ipos": "ipos", "ipo": "ipos", "calendar": "calendar", "cal": "calendar", "c": "calendar",
    "screener": "screener", "screen": "screener", "scr": "screener", "compare": "compare", "cmp": "compare",
    "journal": "journal", "j": "journal", "notes": "journal", "search": "search", "s": "search", "find": "search",
    "account": "account", "acct": "account", "settings": "account", "admin": "admin",
    "banks": "banks", "bank": "banks", "research": "banks", "sellside": "banks", "brokers": "banks",
    "news": "news", "n": "news", "headlines": "news", "defence": "news", "defense": "news",
    "substack": "substack", "subs": "substack", "newsletters": "substack", "blogs": "substack",
}

# Clocks: label, IANA zone, local open and close (HH:MM), for the dot showing whether that market is trading.
# Clocks: label, IANA zone, then trading hours (HH:MM) by weekday Mon..Sun ("" = closed), for the open/closed dot.
CLOCKS = [("NY", "America/New_York", [("09:30", "16:00")] * 5 + [None, None]),
          ("SEOUL", "Asia/Seoul", [("09:00", "15:30")] * 5 + [None, None]),
          ("SYDNEY", "Australia/Sydney", [("10:00", "16:00")] * 5 + [None, None]),
          ("LONDON", "Europe/London", [("08:00", "16:30")] * 5 + [None, None]),
          ("TEL AVIV", "Asia/Jerusalem", [("09:59", "17:25")] * 4 + [("09:59", "14:00"), None, None])]

_CSS = """
:host { display:block; }
.vt { font-family:'Plex Mono', ui-monospace, Menlo, Consolas, monospace; font-size:13px; color:#D9DEE0;
      font-variant-numeric:tabular-nums; }
.row1 { display:flex; align-items:center; gap:14px; height:38px; padding:0 2px; }
.brand { color:#FFAE1A; font-weight:600; letter-spacing:.06em; white-space:nowrap; }
.cmd { flex:1; display:flex; align-items:center; height:28px; border:1px solid #FFAE1A; background:#1A1405;
       padding:0 8px; min-width:160px; }
.cmd span { color:#FFAE1A; margin-right:6px; }
.cmd input { flex:1; background:transparent; border:none; outline:none; color:#FFAE1A; font:inherit; font-weight:500;
             text-transform:uppercase; caret-color:#FFAE1A; min-width:0; }
.cmd input::placeholder { color:#7C878C; text-transform:none; font-weight:400; }
.clocks { display:flex; gap:12px; white-space:nowrap; color:#7C878C; }
.clocks b { color:#D9DEE0; font-weight:500; margin-left:4px; }
.dot { margin-left:4px; font-size:10px; }
.dot.on { color:#33D17A; } .dot.off { color:#FF5A4E; }
.keys { display:flex; flex-wrap:nowrap; overflow-x:auto; border-top:1px solid #22282B; border-bottom:1px solid #22282B;
        scrollbar-width:none; }
.keys::-webkit-scrollbar { display:none; }
.keys button { all:unset; cursor:pointer; padding:4px 10px; font-size:12.5px; color:#7C878C; border-right:1px solid #22282B;
               white-space:nowrap; font:inherit; }
.keys button b { color:#FFAE1A; margin-right:6px; font-weight:500; }
.keys button:hover { color:#D9DEE0; background:#0B0F11; }
.keys button:focus-visible { outline:1px solid #FFAE1A; outline-offset:-1px; }
.keys button.on { background:#FFAE1A; color:#000; } .keys button.on b { color:#000; }
@media (max-width:1100px) { .clocks span:nth-child(n+4) { display:none; } }
@media (max-width:900px) { .clocks { display:none; } }
@media (max-width:640px) { .brand { display:none; } .keys button { padding:6px 10px; } }
"""

_JS = """
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  let root = parentElement.querySelector('.vt');
  if (!root) {
    root = document.createElement('div');
    root.className = 'vt';
    root.innerHTML = '<div class="row1"><div class="brand"></div><label class="cmd"><span>&gt;</span>' +
      '<input type="text" spellcheck="false" autocomplete="off" aria-label="Command line"></label>' +
      '<div class="clocks"></div></div><nav class="keys" aria-label="Pages"></nav>';
    parentElement.appendChild(root);
  }
  const input = root.querySelector('input');
  input.placeholder = 'ticker + market (SENSO FI, 005930 KR) or a page name, then Enter.  "/" jumps here';
  root.querySelector('.brand').textContent = data.brand || 'VERDANT';

  const keys = root.querySelector('.keys');
  keys.innerHTML = '';
  for (const p of data.pages) {
    const b = document.createElement('button');
    b.type = 'button';
    if (p.id === data.active) b.className = 'on';
    if (p.fkey) { const k = document.createElement('b'); k.textContent = p.fkey; b.appendChild(k); }
    b.appendChild(document.createTextNode(p.label));
    b.title = p.fkey ? p.label + ' (' + p.fkey + ')' : p.label;
    b.onclick = () => setTriggerValue('go', p.id);
    keys.appendChild(b);
  }

  const clocks = root.querySelector('.clocks');
  const tick = () => {
    const now = new Date();
    clocks.innerHTML = '';
    for (const c of data.clocks) {
      const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: c.tz, hour: '2-digit',
        minute: '2-digit', weekday: 'short', hour12: false }).formatToParts(now).map(x => [x.type, x.value]));
      const hm = (parts.hour === '24' ? '00' : parts.hour) + ':' + parts.minute;
      const day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(parts.weekday);
      const h = c.hours[day];
      const open = !!h && hm >= h[0] && hm < h[1];
      const s = document.createElement('span');
      s.textContent = c.label;
      const t = document.createElement('b'); t.textContent = hm; s.appendChild(t);
      const d = document.createElement('i'); d.className = 'dot ' + (open ? 'on' : 'off'); d.textContent = '●';
      d.title = open ? 'Market open' : 'Market closed'; s.appendChild(d);
      clocks.appendChild(s);
    }
  };
  tick();
  const timer = setInterval(tick, 15000);

  input.onkeydown = (e) => {
    if (e.key === 'Enter' && input.value.trim()) {
      setTriggerValue('cmd', input.value.trim());
      input.value = '';
      input.blur();
    } else if (e.key === 'Escape') {
      input.value = '';
      input.blur();
    }
  };
  const onKey = (e) => {
    if (/^F([1-9]|10)$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const p = data.pages.find(x => x.fkey === e.key);
      if (p) { e.preventDefault(); e.stopPropagation(); setTriggerValue('go', p.id); }
      return;
    }
    const t = (e.composedPath && e.composedPath()[0]) || e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey) { e.preventDefault(); input.focus(); }
  };
  document.addEventListener('keydown', onKey, true);
  return () => { document.removeEventListener('keydown', onKey, true); clearInterval(timer); };
}
"""

_bar = None


def _component():
    global _bar
    if _bar is None:
        _bar = st.components.v2.component("verdant_terminal_bar", css=_CSS, js=_JS)
    return _bar


def bar(pages: dict, current) -> None:
    """Draw the top bar and act on what was typed or pressed."""
    active = next((pid for pid, p in pages.items() if p is current or getattr(p, "url_path", None) == getattr(current, "url_path", "")), "")
    tabs = [{"id": pid, "label": label, "fkey": fkey} for pid, label, fkey in TABS if pid in pages]
    clocks = [{"label": l, "tz": tz, "hours": [list(h) if h else None for h in hours]} for l, tz, hours in CLOCKS]
    from .config import app_name
    with st.container(key="vt-bar"):
        res = _component()(data={"pages": tabs, "active": active, "clocks": clocks,
                                 "brand": app_name().upper()},
                           key="vt", on_go_change=lambda: None, on_cmd_change=lambda: None)
    go = getattr(res, "go", None)
    cmd = getattr(res, "cmd", None)
    if go and go in pages and go != active:
        nav.go(go)
    elif cmd:
        run_command(cmd, pages)


def run_command(text: str, pages: dict) -> None:
    words = text.strip().split()
    if not words:
        return
    first = words[0].lower().lstrip("/")
    if len(words) == 1 and first in ALIASES and ALIASES[first] in pages:
        nav.go(ALIASES[first])
        return
    hit = find_company(text)
    if hit:
        nav.open_company(*hit)
        return
    st.session_state["s-text"] = text.strip()            # not a page or a ticker: search for it
    nav.go("search")


def find_company(text: str) -> tuple[str, str] | None:
    """'SENSO FI', '005930 KR', '005930', 'AAPL US', 'samsung'. Prefers companies on the member's watchlist."""
    words = text.strip().split()
    market = None
    if len(words) >= 2 and len(words[-1]) <= 3 and words[-1].isalpha():
        market, words = words[-1].upper(), words[:-1]
    ticker = " ".join(words)
    user = st.session_state.get("user") or {}
    mine = set()
    try:
        from services import watch
        mine = {(w["market"], w["ticker"]) for w in watch.get_watchlist(user["id"])} if user else set()
    except Exception:
        pass
    with get_engine().connect() as conn:
        q = select(companies.c.market, companies.c.ticker).where(func.upper(companies.c.ticker) == ticker.upper())
        if market:
            q = q.where(companies.c.market == market)
        rows = [tuple(r) for r in conn.execute(q.limit(20))]
        if not rows and len(ticker) >= 3:
            like = f"%{ticker.lower()}%"
            q = select(companies.c.market, companies.c.ticker).where(or_(
                func.lower(companies.c.name_en).like(like), func.lower(companies.c.name_local).like(like)))
            if market:
                q = q.where(companies.c.market == market)
            rows = [tuple(r) for r in conn.execute(q.limit(20))]
    if not rows:
        return None
    return next((r for r in rows if r in mine), rows[0])


@st.cache_data(ttl=60, show_spinner=False)
def _status(user_id: int) -> dict:
    out = {"last_run": None, "today": 0, "companies": 0}
    try:
        with get_engine().connect() as conn:
            out["last_run"] = conn.execute(select(run_log.c.started_at).order_by(run_log.c.id.desc()).limit(1)).scalar()
    except Exception:
        pass
    try:
        from services import watch
        from services.pipeline import filings_for
        pairs = [(w["market"], w["ticker"]) for w in watch.get_watchlist(user_id)]
        rows = filings_for(pairs, date.today()) if pairs else []
        out["today"], out["companies"] = len(rows), len(pairs)
    except Exception:
        pass
    return out


def status_bar(user: dict) -> None:
    from .ui import esc, relative_time
    s = _status(user["id"])
    last = as_utc(s["last_run"]) if s["last_run"] else None
    if last is None:
        run = '<span class="dim">Worker: no runs recorded yet</span>'
    else:
        late = utcnow() - last > timedelta(hours=3)
        run = f'<span class="{"dn" if late else "up"}">● Worker ran {esc(relative_time(last))}</span>'
    st.html(f'<div class="vt-status">{run}<span>Filings today {s["today"]}</span>'
            f'<span>Following {s["companies"]}</span><span>{esc(user.get("username") or "")}</span>'
            '<span class="r">F1–F10 pages · / command line · Esc clears</span></div>')


_PANEL_JS = """
export default function(component) {
  const { data, setTriggerValue, parentElement } = component;
  let box = parentElement.querySelector('.vt-panel');
  if (!box) { box = document.createElement('div'); box.className = 'vt-panel'; parentElement.appendChild(box); }
  box.innerHTML = data;
  box.onclick = (e) => {
    const a = e.target.closest('[data-open]');
    if (a) { e.preventDefault(); setTriggerValue('open', a.getAttribute('data-open')); }
  };
}
"""
_panel = None


def panel(markup: str, key: str) -> None:
    """Show trusted, escaped HTML; links with data-open="MARKET|TICKER" open that company in this session."""
    global _panel
    if _panel is None:
        _panel = st.components.v2.component("verdant_terminal_panel", js=_PANEL_JS, isolate_styles=False)
    res = _panel(data=markup, key=key, on_open_change=lambda: None)
    hit = getattr(res, "open", None)
    if hit and "|" in hit:
        m, t = hit.split("|", 1)
        nav.open_company(m, t)


def open_link(market: str, ticker: str, text: str) -> str:
    from .ui import esc
    return f'<a class="op" href="#" data-open="{esc(market)}|{esc(ticker)}">{esc(text)}</a>'
