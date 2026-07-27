#!/usr/bin/env python3
"""
Wekelijkse Instagram Reel: silent 9:16-slideshow met één artikel per dag over
de afgelopen 7 dagen (zie INSTAGRAM_PLAN.md fase 7). Reels zijn Instagrams
belangrijkste ontdekkingskanaal voor niet-volgers — de dagelijkse digest-post
(instagram_digest.py) bereikt vooral bestaande volgers.

Minimale versie: geen audio, geen Ken Burns-zoom — statische slides met harde
cuts. Losstaand van de dag-wachtrij: leest artikelen rechtstreeks uit
WordPress, dus onafhankelijk van instagram_queue.json (dat wordt elke avond
al geleegd door instagram_digest.py).

Gebruik:
  venv/bin/python3 weekly_reel.py            # echte run
  venv/bin/python3 weekly_reel.py --dry-run  # bouwt de video, post niets
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import requests

from ai_processor import build_combined_ig_caption
from config import (
    BASE_DIR,
    ENABLE_INSTAGRAM_POSTING,
    REEL_AUDIO_FILE,
    REEL_CYCLE_DAYS,
    is_reel_day,
    next_reel_day,
)
from instagram_image import compose_instagram_image, compose_reel_card
from instagram_reel import SLIDE_SECONDS, build_reel_video
from social_poster import post_instagram_reel, publish_video_publicly
from wordpress_client import fetch_posts_for_reel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_TMP_DIR = BASE_DIR / "tmp"
_TMP_DIR.mkdir(exist_ok=True)

# Gelijk aan de cadans: bij een venster van 7 dagen op een 6-daagse cyclus zou
# telkens één dag in twee opeenvolgende Reels terugkomen.
REEL_DAYS = REEL_CYCLE_DAYS
REEL_CANVAS = (1080, 1920)
MIN_SLIDES = 2  # onder 2 slides is een "slideshow" zinloos
CARD_SECONDS = 1.5   # intro: kort houden, anders scrollt de kijker weg
OUTRO_SECONDS = 2.5  # afsluiter mag iets langer, die moet gelezen worden
# Onderrand van de tekstbalk op 3/4 van de hoogte. Instagram legt onderin z'n
# eigen interface over de video — caption, accountnaam, muziekregel, knoppen —
# waardoor tekst tegen de onderkant wegvalt. De onderste kwart (480px) blijft
# daarom leeg.
REEL_BAND_BOTTOM = 0.75

_NL_WEEKDAGEN = ["MAANDAG", "DINSDAG", "WOENSDAG", "DONDERDAG", "VRIJDAG", "ZATERDAG", "ZONDAG"]


def _kicker_for(date_str: str) -> str:
    """Dutch weekday label for the slide kicker (locale-onafhankelijk)."""
    return _NL_WEEKDAGEN[datetime.strptime(date_str, "%Y-%m-%d").weekday()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Bouw de video, post niets naar Instagram")
    parser.add_argument("--force", action="store_true", help="Post ook als vandaag geen cyclusdag is")
    return parser.parse_args()


def _download(url: str, dest: Path) -> bool:
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        return True
    except Exception as exc:
        logger.warning("Slide-afbeelding downloaden mislukt (%s): %s", url, exc)
        return False


def main() -> None:
    args = _parse_args()

    if not ENABLE_INSTAGRAM_POSTING:
        logger.info("ENABLE_INSTAGRAM_POSTING staat uit — reel overgeslagen")
        return

    # Tweede slot op de cyclus, náást scheduler.py. Blijft de crontab een dag
    # staan (scheduler niet gedraaid), dan voorkomt dit een dubbele post.
    # --force omzeilt het voor een handmatige run.
    if not args.force and not is_reel_day():
        logger.info(
            "Vandaag is geen Reel-dag — eerstvolgende: %s (gebruik --force om toch te posten)",
            next_reel_day().isoformat(),
        )
        return

    posts = fetch_posts_for_reel(days=REEL_DAYS)
    if len(posts) < MIN_SLIDES:
        logger.info(
            "Te weinig artikelen deze week (%d) voor een Reel — overgeslagen", len(posts)
        )
        return

    slide_paths: list[str] = []
    for i, post in enumerate(posts):
        src = _TMP_DIR / f"reel_src_{i}.jpg"
        if not _download(post["image_url"], src):
            continue
        dest = _TMP_DIR / f"reel_slide_{i}.jpg"
        composed = compose_instagram_image(
            str(src), post["title"], _kicker_for(post["date"]), str(dest),
            canvas_w=REEL_CANVAS[0], canvas_h=REEL_CANVAS[1],
            band_bottom_frac=REEL_BAND_BOTTOM,
        )
        if composed:
            slide_paths.append(composed)

    if len(slide_paths) < MIN_SLIDES:
        logger.error(
            "Te weinig slides gecomponeerd (%d/%d) — Reel overgeslagen",
            len(slide_paths), len(posts),
        )
        return

    # Merkkaarten om de artikelen heen: een korte intro met het wordmark en een
    # afsluiter die naar de site verwijst. Bewust korter dan een artikelslide —
    # een statisch logo van 3 seconden kost kijkers in de eerste scroll.
    intro = compose_reel_card(
        str(_TMP_DIR / "reel_intro.jpg"),
        canvas_w=REEL_CANVAS[0], canvas_h=REEL_CANVAS[1],
    )
    outro = compose_reel_card(
        str(_TMP_DIR / "reel_outro.jpg"),
        subtitle="Elke dag het belangrijkste tech-nieuws in het Nederlands",
        canvas_w=REEL_CANVAS[0], canvas_h=REEL_CANVAS[1],
    )

    durations = [SLIDE_SECONDS] * len(slide_paths)
    if intro:
        slide_paths.insert(0, intro)
        durations.insert(0, CARD_SECONDS)
    if outro:
        slide_paths.append(outro)
        durations.append(OUTRO_SECONDS)

    video_path = str(_TMP_DIR / "weekly_reel.mp4")
    audio_path = ""
    if REEL_AUDIO_FILE:
        candidate = Path(REEL_AUDIO_FILE)
        audio_path = str(candidate if candidate.is_absolute() else BASE_DIR / candidate)

    video = build_reel_video(
        slide_paths, video_path, audio_path=audio_path, durations=durations,
    )
    if not video:
        logger.error("Video bouwen mislukt — Reel overgeslagen")
        return

    entries = [{"ig_tekst": p["title"], "trefwoorden": ""} for p in posts]
    caption = "📅 Deze week bij TechNieuwsVandaag:\n\n" + build_combined_ig_caption(entries)
    # Muzieknaamsvermelding staat bewust niet hier maar op de colofonpagina
    # (assets/pagina-colofon.html) — één centrale plek i.p.v. in elke caption.

    if args.dry_run:
        logger.info(
            "[DRY RUN] Weekly reel klaar: %s (%d slides)\nCaption:\n%s",
            video, len(slide_paths), caption,
        )
        return

    video_url = publish_video_publicly(video)
    if not video_url:
        logger.error("Video hosten mislukt — Reel overgeslagen")
        return

    permalink = post_instagram_reel(video_url, caption)
    logger.info("Weekly reel: %s", permalink or "MISLUKT")


if __name__ == "__main__":
    main()
