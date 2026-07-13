"""
TechNieuwsVandaag — hoofd-orchestratie script.

Dagelijkse pijplijn:
  1. Scrape tech-nieuwsbronnen
  2. AI-selectie en samenvatting via Claude
  3. Afbeeldingen downloaden
  4. WordPress drafts aanmaken
  5. Notificatiemail versturen

Gebruik:
  python main.py                          # Normale uitvoering
  python main.py --dry-run                # Simuleer alles, geen WordPress/mail
  python main.py --test-source techcrunch # Test één bron
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path


def _setup_logging(logs_dir: Path) -> logging.Logger:
    """Configure file + stdout logging; return module logger."""
    # pre: logs_dir parent is writable
    # post: log file created at logs_dir/run_{timestamp}.log
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = logs_dir / f"run_{date_str}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)


# Logging initialiseren vóór alle andere imports, zodat module-loggers correct
# worden geconfigureerd bij het importeren
from config import BASE_DIR, LOGS_DIR  # noqa: E402

_TMP_DIR = BASE_DIR / "tmp"
_TMP_DIR.mkdir(exist_ok=True)

logger = _setup_logging(LOGS_DIR)

from ai_processor import InsufficientCreditsError, ensure_ig_fields, process_articles, save_posted_title  # noqa: E402
from approval_store import update_bluesky_uri, update_instagram_permalink  # noqa: E402
from config import (  # noqa: E402
    BLUESKY_POST_DELAY_SECONDS,
    ENABLE_INSTAGRAM_POSTING,
    ENABLE_SOCIAL_POSTING,
    IMAGE_STRATEGY,
    INSTAGRAM_POST_DELAY_SECONDS,
)
from mailer import build_action_buttons, send_balance_warning, send_fal_balance_warning, send_notification  # noqa: E402
from scraper import download_image, fetch_article_text, save_posted_url, scrape_all_sources  # noqa: E402
from social_poster import post_to_bluesky, post_to_instagram  # noqa: E402
from wordpress_client import publish_articles  # noqa: E402


def _parse_args() -> argparse.Namespace:
    """Parse and return CLI arguments."""
    # post: args.lookback_days >= 1 (default 1)
    parser = argparse.ArgumentParser(
        description="TechNieuwsVandaag — automatisch nieuws scrapen en publiceren",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Voer alles uit maar post niets naar WordPress en stuur geen mail",
    )
    parser.add_argument(
        "--test-source",
        metavar="DOMEIN",
        default=None,
        help="Test slechts één bron (bijv. --test-source techcrunch.com)",
    )
    parser.add_argument(
        "--lookback-days",
        metavar="DAGEN",
        type=int,
        default=1,
        help="Aantal dagen terug om artikelen te zoeken (standaard 1 = 24 uur)",
    )
    parser.add_argument(
        "--test-bluesky",
        action="store_true",
        help="Test Bluesky posting met het meest recente gepubliceerde artikel",
    )
    return parser.parse_args()


def main() -> int:
    """Run the full scrape → AI → publish → notify pipeline; return exit code."""
    # post: returns 0 on success, 1 on fatal error
    args = _parse_args()

    # Bluesky test: post het meest recente gepubliceerde artikel
    if args.test_bluesky:
        import base64 as _b64  # noqa: PLC0415

        import requests as _req  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
        from config import WP_APP_PASSWORD, WP_URL, WP_USERNAME  # noqa: PLC0415

        _token = _b64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
        _resp = _req.get(
            f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts",
            params={"per_page": 1, "status": "publish", "orderby": "date", "order": "desc"},
            headers={"Authorization": f"Basic {_token}"},
            timeout=15,
        )
        _posts = _resp.json()
        if not _posts:
            logger.error("Geen gepubliceerde artikelen gevonden voor Bluesky test")
            return 1
        _p = _posts[0]
        _excerpt = BeautifulSoup(_p.get("excerpt", {}).get("rendered", ""), "html.parser").get_text()
        # Haal tag namen op (API geeft alleen IDs terug)
        _tag_ids = _p.get("tags", [])
        _tag_names: list[str] = []
        for _tid in _tag_ids[:5]:
            try:
                _tr = _req.get(
                    f"{WP_URL.rstrip('/')}/wp-json/wp/v2/tags/{_tid}",
                    headers={"Authorization": f"Basic {_token}"},
                    timeout=10,
                )
                _tag_names.append(_tr.json().get("name", ""))
            except Exception:
                pass
        _success = post_to_bluesky(
            title=_p["title"]["rendered"],
            summary=_excerpt,
            keywords=", ".join(_tag_names),
            post_url=_p["link"],
            dry_run=args.dry_run,
        )
        print(f"Bluesky test: {'✅ geslaagd' if _success else '❌ mislukt'}")
        return 0 if _success else 1

    if args.dry_run:
        logger.info("=" * 60)
        logger.info("DRY RUN — geen WordPress posts of mails worden verstuurd")
        logger.info("=" * 60)

    logger.info("=== TechNieuwsVandaag Bot gestart ===")

    # ------------------------------------------------------------------
    # Stap 1: Scraping
    # ------------------------------------------------------------------
    logger.info("── Stap 1: Artikelen scrapen ──")
    try:
        articles = scrape_all_sources(
            test_source=args.test_source,
            lookback_days=args.lookback_days,
        )
    except Exception as exc:
        logger.critical("Scraping volledig mislukt: %s", exc)
        send_notification(
            [],
            warning_message=f"Scraping mislukt: {exc}",
            dry_run=args.dry_run,
        )
        return 1

    logger.info("Totaal %d nieuw(e) artikel(en) gevonden", len(articles))

    warning_message = ""
    if len(articles) < 2:
        warning_message = (
            f"Slechts {len(articles)} artikel(en) gevonden. "
            "Mogelijk zijn niet alle bronnen bereikbaar of zijn alle artikelen al gepost."
        )
        logger.warning(warning_message)

    if not articles:
        logger.error("Geen artikelen gevonden — script gestopt")
        send_notification([], warning_message=warning_message, dry_run=args.dry_run)
        return 1

    # ------------------------------------------------------------------
    # Stap 2: AI-verwerking
    # ------------------------------------------------------------------
    logger.info("── Stap 2: AI-verwerking via Claude ──")
    try:
        processed_articles = process_articles(articles)
    except InsufficientCreditsError as exc:
        logger.critical("Anthropic tegoed op: %s", exc)
        send_balance_warning()
        return 1
    except Exception as exc:
        logger.critical("AI-verwerking volledig mislukt: %s", exc)
        send_notification(
            [],
            warning_message=f"AI-verwerking mislukt: {exc}",
            dry_run=args.dry_run,
        )
        return 1

    if not processed_articles:
        logger.error("Geen artikelen succesvol verwerkt door AI — script gestopt")
        send_notification(
            [],
            warning_message="AI-verwerking leverde geen resultaten op.",
            dry_run=args.dry_run,
        )
        return 1

    logger.info("%d artikel(en) verwerkt door AI", len(processed_articles))

    # ------------------------------------------------------------------
    # Stap 3: Afbeeldingen ophalen (strategie: generate of scrape)
    # ------------------------------------------------------------------
    logger.info("── Stap 3: Afbeeldingen ophalen (strategie: %s) ──", IMAGE_STRATEGY)

    if IMAGE_STRATEGY == "generate":
        from image_generator import generate_image_for_article, is_fal_balance_low  # noqa: PLC0415

        if not args.dry_run and is_fal_balance_low():
            from config import FAL_CREDIT_THRESHOLD  # noqa: PLC0415
            from image_generator import check_fal_balance  # noqa: PLC0415
            bal = check_fal_balance()
            logger.warning("FAL.ai tegoed laag ($%.4f) — waarschuwingsmail verstuurd", bal or 0)
            send_fal_balance_warning(bal or 0.0)

        for i, processed in enumerate(processed_articles):
            dest = str(_TMP_DIR / f"tnv_image_{i}.jpg")
            variant: dict = {}
            processed.image_path = generate_image_for_article(
                title=processed.titel,
                article_text=processed.samenvatting,
                dest_path=dest,
                dry_run=args.dry_run,
                variant_out=variant,
            )
            processed.image_variant = variant or None
            if not processed.image_path:
                logger.warning("FAL.ai afbeelding mislukt voor '%s' — doorgaan zonder", processed.titel)
    else:
        # Scrape-modus: og:image van bronpagina
        from scraper import extract_image_from_page, _make_session  # type: ignore[attr-defined]  # noqa: PLC0415
        from urllib.parse import urlparse  # noqa: PLC0415
        session = _make_session()
        for i, processed in enumerate(processed_articles):
            dest = str(_TMP_DIR / f"tnv_image_{i}.jpg")
            img_url = processed.original.image_url

            if not img_url:
                logger.info("Geen afbeelding in feed voor '%s', probeer artikelpagina", processed.titel)
                img_url = extract_image_from_page(processed.original.url, session)

            if img_url:
                local_path = download_image(img_url, dest)
                processed.image_path = local_path
                if local_path:
                    # Caption + bron-URL voor copyright-vermelding
                    domain = urlparse(processed.original.source).netloc or processed.original.source
                    processed.image_caption = (
                        f"Afbeelding: {domain} — Alle rechten voorbehouden aan de oorspronkelijke eigenaar."
                    )
                    processed.bron_image_url = img_url
                else:
                    logger.warning("Afbeelding downloaden mislukt voor '%s' — doorgaan zonder", processed.titel)
            else:
                logger.warning("Geen afbeelding gevonden voor: %s", processed.titel)

    # ------------------------------------------------------------------
    # Stap 4: WordPress artikelen publiceren
    # ------------------------------------------------------------------
    logger.info("── Stap 4: WordPress artikelen publiceren ──")
    results = publish_articles(processed_articles, dry_run=args.dry_run)

    if not args.dry_run:
        for result in results:
            article = result["article"]
            url = article.original.url
            save_posted_url(url)
            save_posted_title(article.titel, url)
            logger.info(
                "URL opgeslagen als gepost: %s → %s",
                url,
                result["post"].get("link", result["post"]["preview_url"]),
            )
    else:
        for result in results:
            logger.info(
                "[DRY RUN] Gepubliceerd: %s", result["post"]["preview_url"]
            )

    if not results and processed_articles:
        warning_message = (
            warning_message + " WordPress publiceren mislukt voor alle artikelen."
            if warning_message
            else "WordPress publiceren mislukt voor alle artikelen."
        )

    # ------------------------------------------------------------------
    # Stap 5: Actie-knoppen aanmaken en notificatiemail versturen
    # ------------------------------------------------------------------
    logger.info("── Stap 5: Notificatiemail versturen ──")

    # Bouw tokens per artikel en sla decline_token op voor Bluesky-koppeling
    decline_tokens: dict[int, str] = {}
    if not args.dry_run:
        for result in results:
            post    = result["post"]
            article = result["article"]
            meta    = {
                "article_text": article.samenvatting,
                "categorieen":  article.categorieen,
                "trefwoorden":  article.trefwoorden,
                "source_url":   article.original.url,
                "image_url":    post.get("image_url", ""),
            }
            buttons_html, decline_token, _ = build_action_buttons(
                post["id"], article.titel,
                post.get("link", post["preview_url"]), meta,
            )
            result["buttons_html"] = buttons_html
            decline_tokens[post["id"]] = decline_token

    send_notification(results, warning_message=warning_message, dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Stap 6: Social publiceren (Bluesky + Instagram, met vertraging)
    # ------------------------------------------------------------------
    if not args.dry_run and ENABLE_SOCIAL_POSTING and results:
        logger.info(
            "── Stap 6: Social publiceren (Bluesky %ds, Instagram +%ds vertraging) ──",
            BLUESKY_POST_DELAY_SECONDS, INSTAGRAM_POST_DELAY_SECONDS,
        )
        for result in results:
            article  = result["article"]
            post     = result["post"]
            post_url = post.get("link", post.get("preview_url", ""))
            post_id  = post["id"]

            logger.info(
                "Wacht %ds voor Bluesky post: %s",
                BLUESKY_POST_DELAY_SECONDS, article.titel,
            )
            time.sleep(BLUESKY_POST_DELAY_SECONDS)

            bsky_uri = post_to_bluesky(
                title=article.titel,
                summary=article.samenvatting,
                keywords=article.trefwoorden,
                post_url=post_url,
                dry_run=False,
            )
            logger.info("Bluesky URI: %s", bsky_uri or "MISLUKT")

            if bsky_uri and post_id in decline_tokens:
                update_bluesky_uri(decline_tokens[post_id], bsky_uri)
                logger.info(
                    "Bluesky URI opgeslagen voor decline token (post %d)", post_id
                )

            # Instagram: eigen vertraging na Bluesky, eigen toggle. De permalink
            # gaat naar de token store zodat de decline-pagina kan herinneren
            # aan handmatig verwijderen (IG-posts kunnen niet via de API weg).
            if ENABLE_INSTAGRAM_POSTING:
                logger.info(
                    "Wacht %ds voor Instagram post: %s",
                    INSTAGRAM_POST_DELAY_SECONDS, article.titel,
                )
                time.sleep(INSTAGRAM_POST_DELAY_SECONDS)

                ensure_ig_fields(article)
                ig_ref = post_to_instagram(
                    ig_kop=article.ig_kop,
                    ig_caption=article.ig_caption,
                    kicker=" · ".join(article.categorieen[:2]),
                    src_image_path=article.image_path or "",
                    dry_run=False,
                )
                logger.info("Instagram: %s", ig_ref or "MISLUKT/OVERGESLAGEN")

                if ig_ref and post_id in decline_tokens:
                    update_instagram_permalink(decline_tokens[post_id], ig_ref)

    # ------------------------------------------------------------------
    # Samenvatting
    # ------------------------------------------------------------------
    logger.info("=== TechNieuwsVandaag Bot klaar ===")
    logger.info(
        "Verwerkt: %d artikel(en) | Gepubliceerd: %d",
        len(processed_articles),
        len(results),
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
