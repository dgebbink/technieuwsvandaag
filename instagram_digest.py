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

from ai_processor import build_daily_ig_caption
from approval_store import update_instagram_permalink
from config import ENABLE_INSTAGRAM_POSTING
from social_poster import clear_instagram_queue, load_instagram_queue, post_instagram_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_IG_CAROUSEL_MAX = 10


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Simuleer, post niets naar Instagram")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if not ENABLE_INSTAGRAM_POSTING:
        logger.info("ENABLE_INSTAGRAM_POSTING staat uit — digest overgeslagen")
        return

    entries = load_instagram_queue()
    if not entries:
        logger.info("Geen artikelen in de Instagram-wachtrij vandaag — niets te posten")
        return

    if len(entries) > _IG_CAROUSEL_MAX:
        logger.warning(
            "Wachtrij heeft %d artikelen, carousel-limiet is %d — de laatste %d worden overgeslagen",
            len(entries), _IG_CAROUSEL_MAX, len(entries) - _IG_CAROUSEL_MAX,
        )
        entries = entries[:_IG_CAROUSEL_MAX]

    caption = build_daily_ig_caption(entries)
    image_urls = [entry["image_url"] for entry in entries]

    logger.info("Instagram-digest: %d artikel(en) → posten", len(entries))
    permalink = post_instagram_digest(image_urls, caption, dry_run=args.dry_run)

    if not permalink:
        logger.error("Instagram-digest mislukt — wachtrij blijft staan voor een volgende poging")
        return

    if not args.dry_run:
        for entry in entries:
            decline_token = entry.get("decline_token", "")
            if decline_token:
                update_instagram_permalink(decline_token, permalink)
        clear_instagram_queue()
        logger.info("Instagram-wachtrij geleegd na succesvolle digest-post")


if __name__ == "__main__":
    main()
