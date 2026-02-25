"""
adhoc_processor.py — Verwerkt handmatig opgegeven URLs via Nextcloud WebDAV share.

Werking:
  - Leest adhoc.txt van Nextcloud (NEXTCLOUD_WEBDAV_URL in .env)
  - Verwerkt maximaal 2 URLs per run via dezelfde pipeline als main.py
  - Schrijft resterende URLs terug naar Nextcloud na verwerking
  - Bij NetworkError: logt fout en stopt (geen retry — volgende cron is over 10 min)
  - Logt naar logs/adhoc_{datum}.log

Gebruik:
  python adhoc_processor.py
  python adhoc_processor.py --dry-run
"""
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).parent
LOGS_DIR    = PROJECT_DIR / "logs"
MAX_PER_RUN = 2

logger = logging.getLogger(__name__)


def _setup_adhoc_logging() -> None:
    """Voeg een adhoc-specifiek logbestand toe aan de bestaande handlers."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"adhoc_{date_str}.log"

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)


# ---------------------------------------------------------------------------
# URL validatie
# ---------------------------------------------------------------------------

def validate_url(url: str) -> bool:
    """Retourneer True als de URL begint met http:// of https://."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


# ---------------------------------------------------------------------------
# Pagina ophalen en artikel-object bouwen
# ---------------------------------------------------------------------------

def fetch_adhoc_article(url: str) -> Optional[object]:
    """
    Haal de pagina op en bouw een Article-object.

    Extraheert: titel (og:title / <title>), tekst (article/main), og:image.
    """
    from scraper import Article  # lokale import om circulaire imports te vermijden

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; TechNieuwsVandaagBot/1.0; "
            "+https://technieuwsvandaag.nl)"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Pagina ophalen mislukt voor %s: %s", url, exc)
        return None

    soup = BeautifulSoup(resp.text, "lxml")

    # Titel
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    if not title:
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        title = url

    # og:image
    image_url: Optional[str] = None
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        image_url = og_image["content"].strip()

    # Volledige tekst
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    text = ""
    for selector in ("article", "main", "body"):
        container = soup.find(selector)
        if container:
            text = container.get_text(separator="\n", strip=True)
            if len(text) > 200:
                break

    text = text[:4000]

    from datetime import timezone
    article = Article(
        title=title,
        url=url,
        pub_date=datetime.now(timezone.utc),
        excerpt=text[:600],
        image_url=image_url,
        source=urlparse(url).netloc,
        full_text=text,
        source_lang="EN",
    )

    # Eenvoudige NL-domein detectie
    nl_domains = ("tweakers.net", "nu.nl", "bright.nl", "dutchcowboys.nl",
                  "androidworld.nl", "webwereld.nl", "computeridee.nl")
    if any(nd in article.source for nd in nl_domains):
        article.source_lang = "NL"

    logger.info("Artikel opgehaald: '%s' van %s [%s]", title, article.source, article.source_lang)
    return article


# ---------------------------------------------------------------------------
# Hoofdfunctie
# ---------------------------------------------------------------------------

def run_adhoc(dry_run: bool = False) -> int:
    """
    Voer de adhoc-verwerking uit. Retourneert 0 bij succes, 1 bij fout.
    """
    _setup_adhoc_logging()
    start = time.monotonic()
    logger.info("=== Adhoc check gestart (dry_run=%s) ===", dry_run)

    # Stap 1: URLs laden van Nextcloud
    from nextcloud_client import read_adhoc, write_adhoc

    try:
        all_urls = read_adhoc()
    except Exception as exc:
        logger.error("Nextcloud lezen mislukt — stop: %s", exc)
        return 1

    if not all_urls:
        logger.info("Adhoc check: leeg — skip")
        return 0

    to_process = all_urls[:MAX_PER_RUN]
    to_keep    = all_urls[MAX_PER_RUN:]

    logger.info(
        "Nextcloud gelezen: %d URL(s) — verwerk %d, bewaar %d",
        len(all_urls), len(to_process), len(to_keep),
    )

    # Stap 2: Valideer URLs
    valid_urls = []
    for url in to_process:
        if validate_url(url):
            valid_urls.append(url)
        else:
            logger.warning("Ongeldige URL overgeslagen: %s", url)

    if not valid_urls:
        logger.warning("Geen geldige URLs — skip")
        return 0

    logger.info("Te verwerken: %s", ", ".join(valid_urls))

    # Stap 3: Artikelen ophalen
    articles    = []
    failed_urls = []
    for url in valid_urls:
        article = fetch_adhoc_article(url)
        if article:
            articles.append(article)
        else:
            failed_urls.append(url)

    # Mislukte fetches terug aan het begin van de queue
    if failed_urls:
        to_keep = failed_urls + to_keep

    if not articles:
        logger.error("Geen artikelen succesvol opgehaald")
        try:
            write_adhoc(to_keep)
        except Exception as exc:
            logger.error("Nextcloud schrijven mislukt: %s", exc)
        return 1

    # Stap 4: AI-verwerking
    from ai_processor import process_article
    import anthropic
    from config import ANTHROPIC_API_KEY

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    processed_articles = []

    for article in articles:
        logger.info("AI verwerkt: %s", article.title)
        result = process_article(article, client)
        if result:
            processed_articles.append(result)
        else:
            logger.warning("AI-verwerking mislukt voor: %s", article.url)
            to_keep.insert(0, article.url)

    if not processed_articles:
        logger.error("Geen artikelen succesvol verwerkt door AI")
        try:
            write_adhoc(to_keep)
        except Exception as exc:
            logger.error("Nextcloud schrijven mislukt: %s", exc)
        return 1

    # Stap 5: Afbeeldingen
    from config import IMAGE_STRATEGY

    if IMAGE_STRATEGY == "generate":
        from image_generator import generate_image_for_article
        for i, processed in enumerate(processed_articles):
            dest = f"/tmp/tnv_adhoc_image_{i}.jpg"
            processed.image_path = generate_image_for_article(
                title=processed.titel1,
                article_text=processed.samenvatting,
                dest_path=dest,
                dry_run=dry_run,
            )
            if not processed.image_path:
                logger.warning("Afbeelding genereren mislukt voor '%s' — doorgaan zonder", processed.titel1)
    else:
        from scraper import extract_image_from_page, download_image, _make_session
        session = _make_session()
        for i, processed in enumerate(processed_articles):
            dest = f"/tmp/tnv_adhoc_image_{i}.jpg"
            img_url = processed.original.image_url
            if not img_url:
                img_url = extract_image_from_page(processed.original.url, session)
            if img_url:
                processed.image_path = download_image(img_url, dest)

    # Stap 6: WordPress drafts
    from wordpress_client import publish_articles
    results = publish_articles(processed_articles, dry_run=dry_run)

    if not dry_run:
        from scraper import save_posted_url
        for result in results:
            save_posted_url(result["article"].original.url)
            logger.info(
                "Verwerkt: %s → WordPress draft ID %s",
                result["article"].original.url,
                result["post"].get("id", "?"),
            )
    else:
        for result in results:
            logger.info("[DRY RUN] Adhoc draft: %s", result["post"]["preview_url"])

    # Stap 7: Nextcloud bijwerken (resterende URLs)
    try:
        write_adhoc(to_keep)
        logger.info("Nextcloud bijgewerkt: %d URL(s) resterend", len(to_keep))
    except Exception as exc:
        logger.error("Nextcloud schrijven mislukt: %s", exc)

    # Stap 8: Social media
    from social_poster import post_articles_to_social
    post_articles_to_social(results, dry_run=dry_run)

    # Stap 9: Notificatiemail
    from mailer import send_notification
    send_notification(results, subject_prefix="[ADHOC]", dry_run=dry_run)

    elapsed = time.monotonic() - start
    logger.info(
        "=== Adhoc check voltooid in %.1fs: %d artikel(en) gepost ===",
        elapsed, len(results),
    )
    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from config import LOGS_DIR as _LOGS_DIR
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    parser = argparse.ArgumentParser(description="Adhoc URL verwerker via Nextcloud")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simuleer — geen WordPress posts of mails")
    args = parser.parse_args()
    sys.exit(run_adhoc(dry_run=args.dry_run))
