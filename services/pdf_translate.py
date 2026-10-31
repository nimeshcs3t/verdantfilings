"""Translate a whole PDF into English while keeping its layout: each block of text is replaced, in the same place,
by its translation (backgrounds, pictures, charts and tables' lines stay as they are). Works best for presentations
and reports with real text; scanned pages (pictures of text) can't be translated.

Uses the free Google translation already used for titles (no AI, so it doesn't count towards AI limits). The PDF is
processed in memory and not stored."""
from __future__ import annotations

import html
import re
import time

MAX_PAGES = 80
MAX_CHARS = 250_000
CHUNK = 4200


class TranslateProblem(ValueError):
    pass


def _hex(color: int) -> str:
    return f"#{color:06x}" if isinstance(color, int) else "#000000"


def _units(block: dict) -> list[list[dict]]:
    """Split a text block into groups of lines that sit in the same column (PDFs often merge side-by-side boxes)."""
    groups: list[list[dict]] = []
    for line in block.get("lines", []):
        if not "".join(sp.get("text", "") for sp in line.get("spans", [])).strip():
            continue
        x0, _, x1, _ = line["bbox"]
        for g in groups:
            gx0 = min(l["bbox"][0] for l in g)
            gx1 = max(l["bbox"][2] for l in g)
            last_y1 = g[-1]["bbox"][3]
            overlap = min(x1, gx1) - max(x0, gx0)
            if overlap > 0.3 * min(x1 - x0, gx1 - gx0) and line["bbox"][1] - last_y1 < 2.5 * max(1, line["bbox"][3] - line["bbox"][1]):
                g.append(line)
                break
        else:
            groups.append([line])
    return groups


def _blocks(page) -> list[dict]:
    import pymupdf
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES          # "ﬀ" -> "ff"
    out = []
    for blk in page.get_text("dict", flags=flags)["blocks"]:
        if blk.get("type") != 0:
            continue
        for lines in _units(blk):
            spans = [s for line in lines for s in line.get("spans", []) if s.get("text", "").strip()]
            text = re.sub(r"\s+", " ", " ".join(" ".join(s["text"] for s in line.get("spans", [])) for line in lines)).strip()
            if not spans or not re.search(r"[^\W\d_]", text):     # numbers / symbols only: leave as they are
                continue
            main = max(spans, key=lambda s: len(s["text"]))
            rect = pymupdf.Rect(lines[0]["bbox"])
            for line in lines[1:]:
                rect |= pymupdf.Rect(line["bbox"])
            out.append({"rect": tuple(rect), "text": text, "size": round(main.get("size", 10), 1),
                        "color": _hex(main.get("color", 0)), "bold": bool(main.get("flags", 0) & 16), "lines": len(lines)})
    return out


def _translate_all(texts: list[str], target: str, progress=None) -> list[str]:
    """Translate many short texts in few requests (joined by new lines); falls back one by one if a batch's lines
    don't come back one-for-one."""
    from deep_translator import GoogleTranslator
    tr = GoogleTranslator(source="auto", target=target)
    out: list[str] = [""] * len(texts)
    batches, cur, size = [], [], 0
    for i, t in enumerate(texts):
        if cur and size + len(t) + 1 > CHUNK:
            batches.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += len(t) + 1
    if cur:
        batches.append(cur)
    for n, batch in enumerate(batches, 1):
        joined = "\n".join(texts[i].replace("\n", " ") for i in batch)
        lines = []
        for attempt in range(3):
            try:
                lines = (tr.translate(joined) or "").split("\n")
                break
            except Exception as exc:
                if attempt == 2:
                    raise TranslateProblem("The translation service refused or timed out. Try again in a few minutes.") from exc
                time.sleep(2 + attempt * 3)
        if len(lines) != len(batch):
            lines = []
            for i in batch:
                try:
                    lines.append(tr.translate(texts[i]) or texts[i])
                except Exception:
                    lines.append(texts[i])
        for i, line in zip(batch, lines):
            out[i] = line.strip() or texts[i]
        if progress:
            progress(n / len(batches))
    return out


def translate_pdf(data: bytes, target: str = "en", progress=None) -> tuple[bytes, dict]:
    """(translated PDF, {'pages', 'blocks'}). Raises TranslateProblem with a message for the member."""
    import pymupdf
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise TranslateProblem("This PDF can't be opened. It may be damaged.") from exc
    if doc.needs_pass:
        raise TranslateProblem("This PDF is password-protected. Save an unprotected copy and upload that.")
    if doc.page_count > MAX_PAGES:
        raise TranslateProblem(f"This PDF has {doc.page_count} pages; whole-document translation is limited to "
                               f"{MAX_PAGES}. Save just the pages you need (most PDF readers can 'print to PDF' a page range).")
    pages = [_blocks(p) for p in doc]
    texts = [b["text"] for blocks in pages for b in blocks]
    if not texts:
        raise TranslateProblem("No text found in this PDF. It may be scanned pages (pictures), which can't be translated here.")
    if sum(len(t) for t in texts) > MAX_CHARS:
        raise TranslateProblem("This PDF has too much text to translate at once. Save a shorter part and try again.")
    english = _translate_all(texts, target, progress)
    k = 0
    for page, blocks in zip(doc, pages):
        for b in blocks:
            page.add_redact_annot(pymupdf.Rect(b["rect"]), fill=False)      # remove the text, keep what's behind it
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        for b in blocks:
            text = english[k]
            k += 1
            rect = pymupdf.Rect(b["rect"])
            if b["lines"] == 1:                     # one-line labels: allow some extra width for longer English
                rect.x1 = min(page.rect.x1 - 4, rect.x1 + rect.width * 0.35)
            css = (f"* {{font-family: sans-serif; font-size: {b['size']}px; color: {b['color']}; line-height: 1.15;"
                   f"{' font-weight: bold;' if b['bold'] else ''}}}")
            try:
                page.insert_htmlbox(rect, html.escape(text), css=css)
            except Exception:
                page.insert_textbox(rect, text, fontsize=max(4, b["size"] * 0.8), fontname="helv")
    out = doc.tobytes(garbage=3, deflate=True)
    return out, {"pages": doc.page_count, "blocks": len(texts)}
