"""Background job: refresh company listings, sync filings, write overviews and send Telegram alerts.
Run by GitHub Actions on a schedule (see .github/workflows/poll.yml), or manually: python worker.py
"""
import logging
import os

os.environ.setdefault("DIRECT_FETCH", "true")

from services.pipeline import run_once  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = run_once()
    logging.info("listings %(listed)s, companies %(companies)s, new filings %(new)s, overviews %(summaries)s, "
                 "alerts %(alerts)s, errors %(errors)s, titles translated %(retranslated)s", {"retranslated": 0, **stats})
    if stats["errors"] and not stats["companies"]:
        raise SystemExit(1)
