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
from config import LOGS_DIR  # noqa: E402

logger = _setup_logging(LOGS_DIR)

from ai_processor import InsufficientCreditsError, process_articles  # noqa: E402
from mailer import send_balance_warning, send_notification  # noqa: E402
from scraper import download_image, fetch_article_text, save_posted_url, scrape_all_sources  # noqa: E402
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
    return parser.parse_args()


def main() -> int:
    """Run the full scrape → AI → publish → notify pipeline; return exit code."""
    # post: returns 0 on success, 1 on fatal error
    args = _parse_args()

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
    # Stap 3: Afbeeldingen downloaden
    # ------------------------------------------------------------------
    logger.info("── Stap 3: Afbeeldingen downloaden ──")
    for i, processed in enumerate(processed_articles):
        if processed.original.image_url:
            dest = f"/tmp/tnv_image_{i}.jpg"
            local_path = download_image(processed.original.image_url, dest)
            processed.image_path = local_path
            if not local_path:
                logger.warning(
                    "Afbeelding downloaden mislukt voor artikel '%s' — doorgaan zonder",
                    processed.titel1,
                )
        else:
            # Probeer alsnog van de artikelpagina
            logger.info(
                "Geen afbeelding in feed voor '%s', probeer van artikelpagina",
                processed.titel1,
            )
            from scraper import extract_image_from_page, _make_session  # type: ignore[attr-defined]

            session = _make_session()
            img_url = extract_image_from_page(processed.original.url, session)
            if img_url:
                dest = f"/tmp/tnv_image_{i}.jpg"
                processed.image_path = download_image(img_url, dest)
            else:
                logger.warning("Geen afbeelding gevonden voor: %s", processed.titel1)

    # ------------------------------------------------------------------
    # Stap 4: WordPress drafts aanmaken
    # ------------------------------------------------------------------
    logger.info("── Stap 4: WordPress drafts aanmaken ──")
    results = publish_articles(processed_articles, dry_run=args.dry_run)

    if not args.dry_run:
        for result in results:
            url = result["article"].original.url
            save_posted_url(url)
            logger.info(
                "URL opgeslagen als gepost: %s → draft: %s",
                url,
                result["post"]["preview_url"],
            )
    else:
        for result in results:
            logger.info(
                "[DRY RUN] Draft preview URL: %s", result["post"]["preview_url"]
            )

    if not results and processed_articles:
        warning_message = (
            warning_message + " WordPress upload mislukt voor alle artikelen."
            if warning_message
            else "WordPress upload mislukt voor alle artikelen."
        )

    # ------------------------------------------------------------------
    # Stap 5: Notificatiemail
    # ------------------------------------------------------------------
    logger.info("── Stap 5: Notificatiemail versturen ──")
    send_notification(results, warning_message=warning_message, dry_run=args.dry_run)

    # ------------------------------------------------------------------
    # Samenvatting
    # ------------------------------------------------------------------
    logger.info("=== TechNieuwsVandaag Bot klaar ===")
    logger.info(
        "Verwerkt: %d artikel(en) | Drafts aangemaakt: %d",
        len(processed_articles),
        len(results),
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
