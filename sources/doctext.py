"""Turn a downloaded document (PDF or web page) into plain text for overviews."""
from __future__ import annotations

import io
import re

from bs4 import BeautifulSoup


def pdf_text(content: bytes, pages: int = 15) -> str:
    if content[:4] != b"%PDF":
        return ""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(content))
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:pages])
    return "\n".join(ln for ln in (re.sub(r"\s+", " ", x).strip() for x in text.splitlines()) if ln)


def html_text(markup: str) -> str:
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        tr.replace_with("\n" + " | ".join(c for c in cells if c) + "\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln and ln != "|")


def any_text(content: bytes, content_type: str = "") -> str:
    if content[:4] == b"%PDF" or "pdf" in content_type:
        return pdf_text(content)
    return html_text(content.decode("utf-8", "ignore"))
