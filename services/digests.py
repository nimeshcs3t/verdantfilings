"""Scheduled messages: the morning brief, the weekly AI briefing and the monthly PDF report."""
from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select

from core.config import get_secret
from core.db import filings, get_engine, users, utcnow, watchlist
from core.usage import get_state, set_state

from . import deliver, personal, settings
from .classify import is_major
from .importance import label as importance_label, score

log = logging.getLogger(__name__)
esc = lambda s: html.escape(str(s or ""), quote=False)


def _members() -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(select(users).where(users.c.is_active == True)).mappings()]  # noqa: E712


def _now(user_id: int) -> datetime:
    return datetime.now(ZoneInfo(personal.get_prefs(user_id).get("tz") or "UTC"))


def _pairs(user_id: int) -> list[tuple[str, str]]:
    from . import portfolio
    with get_engine().connect() as conn:
        pairs = [tuple(r) for r in conn.execute(select(watchlist.c.market, watchlist.c.ticker).where(watchlist.c.user_id == user_id))]
    return pairs + [(h["market"], h["ticker"]) for h in portfolio.current_holdings(user_id) if (h["market"], h["ticker"]) not in pairs]


def _filings(pairs, since_created=None, since_date=None) -> list[dict]:
    if not pairs:
        return []
    cond = or_(*[and_(filings.c.market == m, filings.c.ticker == t) for m, t in pairs])
    q = select(filings).where(cond)
    if since_created:
        q = q.where(filings.c.created_at >= since_created)
    if since_date:
        q = q.where(filings.c.filed_date >= since_date)
    with get_engine().connect() as conn:
        rows = [dict(r) for r in conn.execute(q).mappings()]
    for r in rows:
        r["score"] = score(r)
    return sorted(rows, key=lambda r: (-r["score"], r["company_name"]))


def snapshot(user_id: int) -> dict | None:
    """Portfolio model in the member's home currency (or USD), or None without holdings."""
    from . import cash, portfolio
    txs = portfolio.list_transactions(user_id)
    if not txs:
        return None
    from views.portfolio import build
    base = portfolio.home_currency(user_id)
    try:
        model = build(txs, base, "SPY", cash.list_moves(user_id))
    except Exception as exc:
        log.warning("portfolio snapshot failed for user %s: %s", user_id, exc)
        return None
    return {**model, "base": base} if model.get("rows") else None


def _ai(prompt: str) -> str | None:
    from .summarize import _anthropic, _gemini
    for provider in (_gemini, _anthropic):
        try:
            out = provider(prompt)
            if out:
                return out.strip()
        except Exception:
            continue
    return None


# ---- morning brief -------------------------------------------------------------------------------------------------
def morning_briefs() -> int:
    from .events import expected_results, upcoming
    sent = 0
    for u in _members():
        prefs = settings.get(u["id"])
        if not prefs.get("morning_brief"):
            continue
        now = _now(u["id"])
        key = f"mb:{u['id']}"
        hour = prefs.get("morning_hour")
        if now.hour < int(7 if hour is None else hour) or get_state(key) == now.date().isoformat():
            continue
        pairs = _pairs(u["id"])
        lines = [f"<b>Good morning</b>  {now:%A %d %b}"]
        snap = snapshot(u["id"])
        if snap:
            today = (snap.get("money_returns") or [{}])[0].get("portfolio")
            movers = sorted([r for r in snap["rows"] if r.get("day") is not None and not r.get("is_cash")],
                            key=lambda r: -abs(r["day"]))[:3]
            lines += ["", f"<b>Portfolio</b> {snap['total']:,.0f} {snap['base']}"
                      + (f", last session {today * 100:+.2f}%" if today is not None else "")]
            lines += [f"• {esc(r['name'])} {r['day'] * 100:+.1f}%" for r in movers]
        overnight = _filings(pairs, since_created=utcnow() - timedelta(hours=18))
        lines += ["", f"<b>Overnight filings</b> ({len(overnight)})"]
        lines += [f"• [{importance_label(r['score'])}] {esc(r['company_name'])}: "
                  f'<a href="{html.escape(r["url"])}">{esc(r["title_en"])}</a>' for r in overnight[:10]] or ["• None"]
        dates = [e for e in upcoming(pairs, days=1)] + [e for e in expected_results(pairs, now.date(), now.date() + timedelta(days=6))
                                                         if e.get("estimate")]
        if dates:
            lines += ["", "<b>Coming up</b>"] + [f"• {e['event_date']:%a %d %b} {esc(e['company_name'])}: {esc(e['label'])}"
                                                 for e in dates[:8]]
        sent += deliver.send(u["id"], f"Morning brief {now:%d %b}", "\n".join(lines))
        set_state(key, now.date().isoformat())
    return sent


# ---- weekly AI briefing ----------------------------------------------------------------------------------------------
def weekly_briefings() -> int:
    """Mondays at the member's digest hour: the week's portfolio move, top movers, key filings, AI summary, next week."""
    from .events import expected_results, upcoming
    sent = 0
    for u in _members():
        prefs, p = settings.get(u["id"]), personal.get_prefs(u["id"])
        if not (p.get("weekly") and prefs.get("weekly_ai", True)):
            continue
        now = _now(u["id"])
        key = f"wk:{u['id']}"
        hour = p.get("digest_hour")
        if now.weekday() != 0 or now.hour < int(18 if hour is None else hour) or get_state(key) == now.date().isoformat():
            continue
        pairs = _pairs(u["id"])
        week = _filings(pairs, since_date=now.date() - timedelta(days=7))
        major = [r for r in week if is_major(r)][:12]
        lines = [f"<b>Your week</b>  {now - timedelta(days=7):%d %b} to {now:%d %b}"]
        snap = snapshot(u["id"])
        if snap:
            wk = next((r for r in snap["money_returns"] if r["label"] == "1M"), None)
            lines += ["", f"<b>Portfolio</b> {snap['total']:,.0f} {snap['base']}"]
            hist_week = [r for r in snap["daily"] if r["date"] >= now.date() - timedelta(days=7)]
            if len(hist_week) >= 2:
                change = hist_week[-1]["value"] - hist_week[0]["value"] - sum(r["flow"] for r in hist_week[1:])
                lines.append(f"Week change {change:+,.0f} {snap['base']}")
            if wk and wk.get("portfolio") is not None:
                lines.append(f"Last month {wk['portfolio'] * 100:+.2f}%")
        lines += ["", f"<b>{len(week)} filing{'s' if len(week) != 1 else ''}</b> from your companies, {len(major)} major"]
        lines += [f'• {esc(r["company_name"])}: <a href="{html.escape(r["url"])}">{esc(r["title_en"])}</a>' for r in major]
        if major and (get_secret("GEMINI_API_KEY") or get_secret("ANTHROPIC_API_KEY")):
            summary = _ai("In 3 to 5 sentences, summarise what these companies disclosed this week for an investor who "
                          "holds them. Plain English, key figures, no investment advice.\n\n"
                          + "\n".join(f"- {r['company_name']}: {r['title_en']} | {(r.get('summary_en') or '')[:300]}" for r in major))
            if summary:
                lines += ["", "<b>In short</b>", esc(summary)]
        nxt = upcoming(pairs, days=7) + [e for e in expected_results(pairs, now.date(), now.date() + timedelta(days=7)) if e.get("estimate")]
        if nxt:
            lines += ["", "<b>Next week</b>"] + [f"• {e['event_date']:%a %d %b} {esc(e['company_name'])}: {esc(e['label'])}" for e in nxt[:10]]
        sent += deliver.send(u["id"], "Your week in filings", "\n".join(lines))
        set_state(key, now.date().isoformat())
    return sent


# ---- monthly PDF report ----------------------------------------------------------------------------------------------
def monthly_pdf(user_id: int) -> tuple[str, bytes] | None:
    from . import report_pdf
    from .events import expected_results, upcoming
    snap = snapshot(user_id)
    if not snap:
        return None
    today = date.today()
    month_end = today.replace(day=1) - timedelta(days=1) if today.day <= 3 else today
    pairs = _pairs(user_id)
    month_filings = [r for r in _filings(pairs, since_date=month_end.replace(day=1)) if is_major(r)]
    nxt = upcoming(pairs, days=35) + [e for e in expected_results(pairs, today, today + timedelta(days=35)) if e.get("estimate")]
    user = None
    with get_engine().connect() as conn:
        user = conn.execute(select(users.c.username).where(users.c.id == user_id)).scalar()
    data = report_pdf.build(month_end, snap["base"], snap, month_filings, sorted(nxt, key=lambda e: e["event_date"]), user or "")
    return f"verdant-report-{month_end:%Y-%m}.pdf", data


def monthly_reports() -> int:
    sent = 0
    for u in _members():
        if not settings.get(u["id"]).get("monthly_pdf", True):
            continue
        now = _now(u["id"])
        key = f"pdf:{u['id']}"
        if now.day > 3 or now.hour < 8 or get_state(key) == f"{now:%Y-%m}":
            continue
        made = monthly_pdf(u["id"])
        set_state(key, f"{now:%Y-%m}")
        if made:
            name, data = made
            sent += deliver.send(u["id"], f"Your portfolio report, {now - timedelta(days=4):%B %Y}",
                                 "<b>Your monthly portfolio report</b> is attached.", attachment=(name, data, "application/pdf"))
    return sent
