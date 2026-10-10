"""Monthly portfolio report as a one- to two-page PDF (fpdf2, built-in fonts, so names are shown in English)."""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta

from fpdf import FPDF

PINE = (31, 107, 79)
MUTED = (95, 107, 122)
UP, DOWN = (28, 124, 74), (179, 38, 30)


def _t(text) -> str:
    """Built-in PDF fonts only cover Western European characters; simplify everything else."""
    s = unicodedata.normalize("NFKD", str(text or "")).replace("–", "-").replace("—", "-").replace("’", "'")
    return s.encode("latin-1", "ignore").decode("latin-1")


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:+.2f}%"


def _money(v, cur) -> str:
    return "-" if v is None else f"{v:,.0f} {cur}"


THEMES = {"light": {"bg": None, "ink": (27, 36, 48), "rule": (227, 231, 229), "pine": PINE, "up": UP, "down": DOWN},
          "dark": {"bg": (15, 20, 19), "ink": (228, 233, 230), "rule": (52, 62, 58), "pine": (93, 190, 147),
                   "up": (92, 203, 138), "down": (242, 131, 122)}}


class Report(FPDF):
    def __init__(self, theme: str = "light", **kw):
        super().__init__(**kw)
        t = THEMES.get(theme, THEMES["light"])
        self.bg, self.ink, self.rule, self.pine, self.up, self.down = t["bg"], t["ink"], t["rule"], t["pine"], t["up"], t["down"]

    def header(self):
        if self.bg:                          # dark theme: fill the page first
            self.set_fill_color(*self.bg)
            self.rect(0, 0, self.w, self.h, "F")
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(*MUTED)
        self.cell(0, 6, "Verdant Filings - monthly portfolio report", align="R")
        self.ln(8)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}. Prices from free sources; not investment advice.", align="C")


def build(month_end: date, base: str, model: dict, filings: list[dict], upcoming: list[dict], name: str = "",
          theme: str = "light") -> bytes:
    pdf = Report(theme=theme, format="A4")
    pdf.set_auto_page_break(True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*pdf.ink)
    pdf.cell(0, 10, _t(f"{month_end:%B %Y}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 6, _t(f"Portfolio report{(' for ' + name) if name else ''}, values in {base}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    returns = {r["label"]: r for r in model.get("money_returns") or []}
    total = model["total"]
    gain = total - sum(r["flow"] for r in model["daily"])
    added = sum(max(r["flow"], 0.0) for r in model["daily"])
    tiles = [("Value", _money(total, base)), ("Total gain", f"{_money(gain, base)} ({_pct(gain / added if added else None)})"),
             ("This month", _pct((returns.get("1M") or {}).get("portfolio"))),
             ("Year to date", _pct((returns.get("YTD") or {}).get("portfolio")))]
    w = (pdf.w - pdf.l_margin - pdf.r_margin) / 4
    pdf.set_draw_color(*pdf.rule)
    y = pdf.get_y()
    for i, (k, v) in enumerate(tiles):
        x = pdf.l_margin + i * w
        pdf.rect(x, y, w - 3, 18)
        pdf.set_xy(x + 2, y + 2)
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(w - 6, 4, _t(k))
        pdf.set_xy(x + 2, y + 8)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*pdf.ink)
        pdf.cell(w - 6, 6, _t(v))
    pdf.set_y(y + 23)
    bench = (returns.get("1M") or {}).get("benchmark")
    if bench is not None:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, _t(f"Same money in SPY this month: {_pct(bench)}"), new_x="LMARGIN", new_y="NEXT")

    def section(title):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*pdf.pine)
        pdf.cell(0, 7, _t(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*pdf.ink)

    def table(headers, rows, widths, aligns):
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*MUTED)
        for h, wd, a in zip(headers, widths, aligns):
            pdf.cell(wd, 6, _t(h), border="B", align=a)
        pdf.ln()
        pdf.set_font("Helvetica", "", 9)
        for row in rows:
            for (value, colour), wd, a in zip(row, widths, aligns):
                pdf.set_text_color(*(colour or pdf.ink))
                pdf.cell(wd, 6, _t(value)[:60], align=a)
            pdf.ln()

    section("Holdings")
    rows = model["rows"][:15]
    table(["Company", "Value", "Weight", "Gain", "Last day"],
          [[(f"{r['name']} ({r['ticker']})", None), (_money(r["value_base"], base), None), (f"{r['weight'] * 100:.1f}%", None),
            (_pct(r.get("gain_pct")), pdf.up if (r.get("gain_pct") or 0) >= 0 else pdf.down), (_pct(r.get("day")), None)] for r in rows],
          [78, 34, 20, 24, 24], ["L", "R", "R", "R", "R"])

    by_country: dict[str, float] = {}
    for r in model["rows"]:
        by_country[r["country"]] = by_country.get(r["country"], 0) + (r["value_base"] or 0)
    if total:
        section("Allocation by country")
        table(["Country", "Share"], [[(k, None), (f"{v / total * 100:.1f}%", None)]
                                     for k, v in sorted(by_country.items(), key=lambda kv: -kv[1])], [90, 30], ["L", "R"])

    if filings:
        section("Key filings this month")
        pdf.set_font("Helvetica", "", 9)
        for f in filings[:15]:
            pdf.multi_cell(0, 5, _t(f"{f['filed_date']:%d %b}  {f['company_name']}: {f['title_en']}"), new_x="LMARGIN", new_y="NEXT")
    if upcoming:
        section("Coming up")
        pdf.set_font("Helvetica", "", 9)
        for e in upcoming[:15]:
            est = " (estimate)" if e.get("estimate") else ""
            pdf.multi_cell(0, 5, _t(f"{e['event_date']:%d %b}  {e['company_name']}: {e['label']}{est}"), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
