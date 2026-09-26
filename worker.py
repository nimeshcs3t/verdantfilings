"""Background job: sync every watched company and send Telegram alerts.
Run by GitHub Actions on a schedule (see .github/workflows/poll.yml), or manually: python worker.py
"""
import logging

from services.pipeline import run_once

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stats = run_once()
    logging.info("checked %(companies)s companies, %(new)s new filings, %(alerts)s alerts, %(errors)s errors", stats)
