"""Background job: refresh company listings, sync filings, write overviews and send Telegram alerts,
keyword alerts, daily digests and weekly reports.
Run by GitHub Actions on a schedule (see .github/workflows/poll.yml), or manually: python worker.py
"""
import logging
import os

os.environ.setdefault("DIRECT_FETCH", "true")

from services.pipeline import run_once  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = {"retranslated": 0, "keyword_alerts": 0, "digests": 0, "weekly": 0, **run_once()}
    logging.info("listings %(listed)s, companies %(companies)s, new filings %(new)s, overviews %(summaries)s, "
                 "alerts %(alerts)s, keyword alerts %(keyword_alerts)s, digests %(digests)s, weekly %(weekly)s, "
                 "errors %(errors)s, titles translated %(retranslated)s", stats)
    if stats["errors"] and not stats["companies"]:
        raise SystemExit(1)
