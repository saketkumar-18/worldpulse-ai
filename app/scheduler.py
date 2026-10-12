"""Entry point for scheduled runs. Invoke from OS cron, Windows Task Scheduler, or a managed cron.

Examples:
    python -m app.scheduler collect            # ingest + summarize + rank (every 30-60 min)
    python -m app.scheduler briefing morning   # full cycle + morning briefing (07:00 Asia/Kolkata)
    python -m app.scheduler briefing evening   # full cycle + evening briefing (19:00 Asia/Kolkata)
    python -m app.scheduler purge              # retention cleanup (daily)

Runs are idempotent per recipient per briefing; duplicate sends are blocked by a unique constraint.
"""
from __future__ import annotations

import json
import logging
import sys

from .config import get_settings
from . import db
from .pipeline import deliver_briefing, purge_old, run_cycle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worldpulse.scheduler")


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("collect", "briefing", "purge"):
        print(__doc__)
        return 2
    settings = get_settings()
    db.configure(settings.database_url)
    session = db.session_factory()()
    try:
        if argv[0] == "purge":
            removed = purge_old(session, settings.retention_days)
            print(json.dumps({"purged_articles": removed}))
            return 0
        if argv[0] == "collect":
            report = run_cycle(session, settings, edition=None)
        else:
            edition = argv[1] if len(argv) > 1 else "morning"
            if edition not in ("morning", "evening"):
                print("edition must be morning or evening")
                return 2
            report = run_cycle(session, settings, edition=edition, deliver=True)
        print(json.dumps(report, default=str, ensure_ascii=False, indent=2))
        failed = [r for r in report.get("ingestion", []) if r.get("status") == "failed"]
        return 1 if failed and not any(r.get("status") in ("success", "partial") for r in report.get("ingestion", [])) else 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
