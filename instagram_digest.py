#!/usr/bin/env python3
"""
Dagelijkse Instagram-digest: bundelt alle artikelen die main.py die dag in
INSTAGRAM_QUEUE_FILE heeft gezet tot één post (carousel bij 2+ artikelen,
los beeld bij 1). Vervangt de vroegere directe post per main.py-run — bij
lage volgersaantallen is 5 losse posts/dag te veel.

Gebruik:
  venv/bin/python3 instagram_digest.py            # echte run
  venv/bin/python3 instagram_digest.py --dry-run   # alleen loggen, niets posten
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from ai_processor import build_combined_ig_caption, fit_ig_entries
from approval_store import update_instagram_permalink
from config import ENABLE_INSTAGRAM_POSTING
from social_poster import (
    load_instagram_queue,
    post_instagram_digest,
    remove_from_instagram_queue,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_IG_CAROUSEL_MAX = 10
# Een mislukte digest laat de wachtrij staan voor een nieuwe poging, maar zonder
# bovengrens groeit die elke dag door met ~5 artikelen — dat maakte de caption
# structureel te lang en daarmee elke volgende poging óók kansloos (24-26 juli).
# Artikelen ouder dan dit zijn geen nieuws meer en gaan er hoe dan ook uit.
_IG_MAX_AGE_DAYS = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Simuleer, post niets naar Instagram")
    return parser.parse_args()


def _is_stale(entry: dict, now: datetime) -> bool:
    """True als de entry te oud is om nog als nieuws te posten.

    Een entry zonder (of met een onleesbare) queued_at wordt als vers
    behandeld: liever een keer te oud posten dan stilletjes weggooien.
    """
    queued_at = entry.get("queued_at", "")
    try:
        return datetime.fromisoformat(queued_at) < now - timedelta(days=_IG_MAX_AGE_DAYS)
    except ValueError:
        return False


def main() -> None:
    args = _parse_args()

    if not ENABLE_INSTAGRAM_POSTING:
        logger.info("ENABLE_INSTAGRAM_POSTING staat uit — digest overgeslagen")
        return

    queue = load_instagram_queue()
    if not queue:
        logger.info("Geen artikelen in de Instagram-wachtrij vandaag — niets te posten")
        return

    now = datetime.now(timezone.utc)
    stale = [entry for entry in queue if _is_stale(entry, now)]
    fresh = [entry for entry in queue if not _is_stale(entry, now)]
    if stale:
        logger.warning(
            "%d artikel(en) ouder dan %d dagen — uit de wachtrij gegooid: %s",
            len(stale), _IG_MAX_AGE_DAYS,
            ", ".join(str(entry.get("post_id")) for entry in stale),
        )
        if not args.dry_run:
            for entry in stale:
                remove_from_instagram_queue(entry["post_id"])

    if not fresh:
        logger.info("Alleen verlopen artikelen in de wachtrij — niets te posten")
        return

    # Nieuwste eerst selecteren (bij een backlog is verse tech het meest
    # relevant), daarna terug op chronologische volgorde voor de nummering.
    selected = list(reversed(fit_ig_entries(list(reversed(fresh)), _IG_CAROUSEL_MAX)))
    if len(selected) < len(fresh):
        logger.warning(
            "Wachtrij heeft %d verse artikelen, %d passen in één post — %d blijven staan voor morgen",
            len(fresh), len(selected), len(fresh) - len(selected),
        )

    caption = build_combined_ig_caption(selected)
    image_urls = [entry["image_url"] for entry in selected]

    logger.info("Instagram-digest: %d artikel(en) → posten (caption %d tekens)", len(selected), len(caption))
    permalink = post_instagram_digest(image_urls, caption, dry_run=args.dry_run)

    if not permalink:
        logger.error("Instagram-digest mislukt — wachtrij blijft staan voor een volgende poging")
        return

    if not args.dry_run:
        for entry in selected:
            decline_token = entry.get("decline_token", "")
            if decline_token:
                update_instagram_permalink(decline_token, permalink)
            # Alleen de daadwerkelijk geposte artikelen; de rest wacht op de
            # volgende digest i.p.v. ongepost te verdwijnen.
            remove_from_instagram_queue(entry["post_id"])
        logger.info("%d geposte artikel(en) uit de wachtrij verwijderd", len(selected))


if __name__ == "__main__":
    main()
