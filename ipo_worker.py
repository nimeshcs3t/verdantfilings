"""IPO calendar job: reads every official feed and IPO website, writes two-line briefs, records first-day moves and
sends IPO alerts. Run by GitHub Actions twice a day (.github/workflows/ipos.yml), or manually: python ipo_worker.py
"""
import json
import logging
import os
import time

os.environ.setdefault("DIRECT_FETCH", "true")

import warnings  # noqa: E402

try:  # harmless notice from reading DART's XML with the HTML parser
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

from core.usage import set_state  # noqa: E402
from services import ipos  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    started = time.time()
    report = ipos.refresh()
    for source, result in report.items():
        logging.info("source %-20s %s", source, result if isinstance(result, str) else f"{result} IPOs read")
    briefs = ipos.enrich(limit=int(os.environ.get("IPO_BRIEFS_PER_RUN", "25")))
    logging.info("briefs written: %s", briefs)
    alerts = ipos.send_alerts()
    logging.info("alerts sent: %s", alerts)
    total = len(ipos.listing())
    logging.info("IPOs stored: %s; run took %.0f s", total, time.time() - started)
    set_state("ipo_job_last", json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "stored": total,
                                          "briefs": briefs, "alerts": alerts,
                                          "failed": [k for k, v in report.items() if isinstance(v, str)]}))
    if report and all(isinstance(v, str) for v in report.values()):
        raise SystemExit(1)
