"""
adhoc_processor.py — Verwerkt een enkele URL naar een WordPress draft.

Gebruikt door de approval server (approval_server.py) via process_single_url().
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
import requests

LOGS_DIR = Path(__file__).parent / "logs"

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
        source_lang="EN",  # adhoc URLs behandelen als EN tenzij NL-domein
    )

    # Eenvoudige NL-domein detectie
    nl_domains = ("tweakers.net", "nu.nl", "bright.nl", "dutchcowboys.nl",
                  "androidworld.nl", "webwereld.nl", "computeridee.nl",
                  "volkskrant.nl", "nos.nl", "rtlnieuws.nl", "fd.nl",
                  "telegraaf.nl", "ad.nl")
    if any(nd in article.source for nd in nl_domains):
        article.source_lang = "NL"

    logger.info("Artikel opgehaald: '%s' van %s [%s]", title, article.source, article.source_lang)
    return article


def process_single_url(url: str) -> dict | None:
    """Verwerkt één URL naar een WordPress draft.
    Pre:  url is een geldige https URL
    Post: geeft dict terug met wp_url, title, post_id, success=True
          of None als verwerking volledig mislukt
    """
    _setup_adhoc_logging()
    logger.info("process_single_url gestart: %s", url)

    if not validate_url(url):
        logger.error("Ongeldige URL: %s", url)
        return None

    # Stap 1: Artikel ophalen
    article = fetch_adhoc_article(url)
    if not article:
        logger.error("Artikel ophalen mislukt: %s", url)
        return None

    # Stap 2: AI-verwerking
    from ai_processor import process_article

    processed = process_article(article)
    if not processed:
        logger.error("AI-verwerking mislukt: %s", url)
        return None

    # Stap 3: Afbeelding
    from config import IMAGE_STRATEGY

    if IMAGE_STRATEGY == "generate":
        from image_generator import generate_image_for_article
        processed.image_path = generate_image_for_article(
            title=processed.titel,
            article_text=processed.samenvatting,
            dest_path="/tmp/tnv_telegram_image.jpg",
            dry_run=False,
        )
    else:
        from scraper import extract_image_from_page, download_image, _make_session
        session = _make_session()
        img_url = processed.original.image_url
        if not img_url:
            img_url = extract_image_from_page(processed.original.url, session)
        if img_url:
            processed.image_path = download_image(img_url, "/tmp/tnv_telegram_image.jpg")

    # Stap 4: WordPress draft aanmaken
    from wordpress_client import publish_articles, publish_post
    results = publish_articles([processed], dry_run=False)

    if not results:
        logger.error("WordPress publicatie mislukt: %s", url)
        return None

    result  = results[0]
    post    = result["post"]
    post_id = post.get("id")

    # Stap 5: Draft direct publiceren
    try:
        pub_result = publish_post(post_id)
        public_url = pub_result.get("link", post.get("preview_url", ""))
        logger.info("Post direct gepubliceerd (dashboard): %s", public_url)
    except Exception as exc:
        logger.error("Publiceren mislukt voor post %s: %s — preview URL gebruikt", post_id, exc)
        public_url = post.get("preview_url", "")

    post["link"] = public_url

    from scraper import save_posted_url
    save_posted_url(url)

    # Stap 6: Notificatiemail met Decline / Nieuwe afbeelding knoppen
    try:
        from mailer import build_action_buttons, send_notification
        meta = {
            "article_text": processed.samenvatting,
            "categorieen":  processed.categorieen,
            "trefwoorden":  processed.trefwoorden,
            "source_url":   url,
            "image_url":    post.get("image_url", ""),
        }
        buttons_html, _, _ = build_action_buttons(post_id, processed.titel, public_url, meta)
        result["buttons_html"] = buttons_html
        send_notification([result], subject_prefix="[Dashboard]")
        logger.info("Notificatiemail verstuurd voor dashboard-post %s", post_id)
    except Exception as exc:
        logger.error("Notificatiemail mislukt voor post %s: %s", post_id, exc)

    logger.info("process_single_url klaar: %s → %s", url, public_url)

    return {
        "wp_url":   public_url,
        "title":    processed.titel,
        "summary":  processed.samenvatting,
        "keywords": processed.trefwoorden,
        "post_id":  post_id,
        "success":  True,
    }
