"""Background job: listings, filings, translations, overviews, alerts (instant, keyword, digest, weekly),
Telegram commands, financials, insider trades, events, AI briefs, price alerts and daily clean-up.
Run by GitHub Actions on a schedule (see .github/workflows/poll.yml), or manually: python worker.py
"""
import logging
import os

os.environ.setdefault("DIRECT_FETCH", "true")

from services.pipeline import run_once  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = run_once()
    logging.info("run finished: " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    if stats["errors"] and not stats["companies"]:
        raise SystemExit(1)
