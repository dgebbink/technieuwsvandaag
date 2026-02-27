#!/usr/bin/env python3
"""
Log rotation / cleanup voor TechNieuwsVandaag.

Retentieregels:
  run_*.log               — 14 dagen
  cron_run_*.log          — 14 dagen
  email_fallback_*.html   — 14 dagen
  digest_*.html           — 7 dagen
  adhoc_*.log             — 14 dagen
  backfill_*.log          — 30 dagen
  scheduler.log           — 30 dagen
  telegram_bot.log        — roteren bij > 5 MB (max 1 backup)
  leeg (0 bytes)          — altijd verwijderen

Gebruik:
  python3 log_cleaner.py           # echte run
  python3 log_cleaner.py --dry-run # simulatie, niets verwijderd
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# (glob-patroon, max leeftijd in dagen)
RETENTION: list[tuple[str, int]] = [
    ("run_*.log",             14),
    ("cron_run_*.log",        14),
    ("email_fallback_*.html", 14),
    ("digest_*.html",          7),
    ("adhoc_*.log",           14),
    ("backfill_*.log",        30),
    ("scheduler.log",         30),
    ("daily_schedule.log",    30),
]

ROTATE_FILES = [
    ("telegram_bot.log", 5 * 1024 * 1024),  # roteren bij > 5 MB
]


def _age_days(path: Path) -> float:
    return (datetime.now().timestamp() - path.stat().st_mtime) / 86400


def clean(dry_run: bool = False) -> None:
    if not LOGS_DIR.exists():
        logger.warning("Logs-map niet gevonden: %s", LOGS_DIR)
        return

    removed = 0
    freed = 0

    # 1. Verwijder lege bestanden
    for path in LOGS_DIR.iterdir():
        if path.is_file() and path.stat().st_size == 0:
            size = 0
            logger.info("Leeg — verwijderen: %s", path.name)
            if not dry_run:
                path.unlink()
            removed += 1
            freed += size

    # 2. Verwijder stub-logs: run_*.log < 1 KB ouder dan 1 dag
    #    (runs zonder resultaat schrijven alleen een "already posted" regel)
    now = datetime.now()
    cutoff_stub = now - timedelta(days=1)
    for path in LOGS_DIR.glob("run_*.log"):
        if path.is_file() and path.stat().st_size < 1024:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if mtime < cutoff_stub:
                size = path.stat().st_size
                logger.info("Stub (< 1 KB) — verwijderen: %s", path.name)
                if not dry_run:
                    path.unlink()
                removed += 1
                freed += size

    # 3. Retentie op basis van leeftijd
    for pattern, max_days in RETENTION:
        cutoff = now - timedelta(days=max_days)
        for path in sorted(LOGS_DIR.glob(pattern)):
            if not path.is_file():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if mtime < cutoff:
                size = path.stat().st_size
                logger.info(
                    "Oud (%.0fd > %dd) — verwijderen: %s",
                    _age_days(path), max_days, path.name,
                )
                if not dry_run:
                    path.unlink()
                removed += 1
                freed += size

    # 3. Roteer grote logbestanden
    for filename, max_bytes in ROTATE_FILES:
        path = LOGS_DIR / filename
        if not path.exists():
            continue
        size = path.stat().st_size
        if size > max_bytes:
            backup = path.with_suffix(".log.1")
            logger.info(
                "Roteren: %s (%.1f MB) → %s",
                path.name, size / 1024 / 1024, backup.name,
            )
            if not dry_run:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
                path.touch()  # start nieuw leeg bestand
            freed += size

    label = "[DRY RUN] " if dry_run else ""
    logger.info(
        "%sKlaar — %d bestand(en) verwijderd, %.1f KB vrijgemaakt",
        label, removed, freed / 1024,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log cleanup")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    clean(dry_run=args.dry_run)
