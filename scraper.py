"""
Scraper module: leest sources.txt, haalt RSS-feeds op (met HTML-fallback),
filtert artikelen van de afgelopen 24 uur en beheert posted_urls.txt.
"""
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from config import (
    MAX_ARTICLES_FOR_SELECTION,
    POSTED_URLS_FILE,
    REQUEST_TIMEOUT,
    SOURCES_FILE,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

# Standaard RSS-paden om te proberen per domein
RSS_PATHS = [
    "/feed",
    "/feed/",
    "/rss",
    "/rss/",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/index.xml",
    "/feed/rss",
    "/news/rss.xml",
]


@dataclass
class Article:
    title: str
    url: str
    pub_date: datetime
    excerpt: str
    image_url: Optional[str] = None
    source: str = ""
    full_text: str = ""


# ---------------------------------------------------------------------------
# Beheer van al-geposte URLs
# ---------------------------------------------------------------------------

def load_posted_urls() -> set[str]:
    """Return the set of already-posted article URLs from file."""
    # pre: POSTED_URLS_FILE path is configured
    # post: returns empty set if file absent
    if not POSTED_URLS_FILE.exists():
        return set()
    return set(line.strip() for line in POSTED_URLS_FILE.read_text().splitlines() if line.strip())


def save_posted_url(url: str) -> None:
    """Append url to posted_urls.txt."""
    # pre: url is non-empty
    with open(POSTED_URLS_FILE, "a", encoding="utf-8") as f:
        f.write(url.strip() + "\n")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_headers() -> dict[str, str]:
    """Return HTTP request headers for scraping."""
    # post: always includes User-Agent and Accept keys
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "nl,en;q=0.9",
    }


def _make_session() -> requests.Session:
    """Create a requests.Session pre-configured with scraping headers."""
    # post: session.headers includes User-Agent
    session = requests.Session()
    session.headers.update(_get_headers())
    return session


# ---------------------------------------------------------------------------
# RSS-feed detectie
# ---------------------------------------------------------------------------

def try_rss_feed(base_url: str) -> Optional[feedparser.FeedParserDict]:
    """Probe common RSS paths for base_url; return first feed with entries or None."""
    # pre: base_url is a valid http(s) URL
    # post: returned feed has >= 1 entry, or None
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    candidates: list[str] = []

    # Als de URL een pad heeft (bijv. bloomberg.com/technology), probeer ook
    # paden relatief aan dat subpad
    if parsed.path and parsed.path not in ("/", ""):
        candidates += [base_url.rstrip("/") + p for p in RSS_PATHS]

    candidates += [root + p for p in RSS_PATHS]

    for feed_url in candidates:
        try:
            logger.debug("Probeer RSS-feed: %s", feed_url)
            feed = feedparser.parse(feed_url, agent=USER_AGENT)
            if feed.entries:
                logger.info("RSS-feed gevonden: %s", feed_url)
                return feed
        except Exception as exc:
            logger.debug("RSS-poging mislukt voor %s: %s", feed_url, exc)

    return None


# ---------------------------------------------------------------------------
# Afbeelding-extractie
# ---------------------------------------------------------------------------

def _image_from_entry(entry: feedparser.FeedParserDict) -> Optional[str]:
    """Extract image URL from feed entry metadata."""
    # post: returns None if no image found in any metadata field
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        return entry.media_thumbnail[0].get("url")

    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            if media.get("type", "").startswith("image"):
                return media.get("url")

    if hasattr(entry, "enclosures") and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href")

    # Probeer de summary/content op een <img> tag te doorzoeken
    for attr in ("summary", "content"):
        val = getattr(entry, attr, None)
        if isinstance(val, list):
            val = val[0].get("value", "") if val else ""
        if val:
            soup = BeautifulSoup(str(val), "html.parser")
            img = soup.find("img", src=True)
            if img:
                return img["src"]  # type: ignore[index]

    return None


def extract_image_from_page(url: str, session: requests.Session) -> Optional[str]:
    """Fetch og:image or first article <img> from a page URL."""
    # pre: session is a configured requests.Session
    # post: returns absolute URL or None on failure
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # og:image heeft prioriteit
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]  # type: ignore[index]

        # Eerste <img> binnen article/main
        for tag in ("article", "main"):
            container = soup.find(tag)
            if container:
                img = container.find("img", src=True)
                if img:
                    return urljoin(url, img["src"])  # type: ignore[index]

        # Laatste fallback: eerste <img> op de pagina
        img = soup.find("img", src=True)
        if img:
            return urljoin(url, img["src"])  # type: ignore[index]

    except Exception as exc:
        logger.warning("Afbeelding ophalen mislukt voor %s: %s", url, exc)

    return None


# ---------------------------------------------------------------------------
# Artikel-tekst ophalen voor AI-verwerking
# ---------------------------------------------------------------------------

def fetch_article_text(url: str, session: Optional[requests.Session] = None) -> str:
    """Fetch and return up to 4000 chars of main text from an article URL."""
    # pre: url points to an HTML page
    # post: returns empty string on error or non-HTML content type
    if session is None:
        session = _make_session()
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        # Skip niet-HTML content (bijv. PDF, binaire bestanden)
        content_type = resp.headers.get("Content-Type", "")
        if not any(ct in content_type for ct in ("text/html", "text/plain", "application/xhtml")):
            logger.debug("Overgeslagen: niet-HTML content-type '%s' voor %s", content_type, url)
            return ""

        soup = BeautifulSoup(resp.text, "lxml")

        # Verwijder scripts, stijlen en navigatie
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Prioriteit: <article>, daarna <main>, daarna <body>
        for selector in ("article", "main", "body"):
            container = soup.find(selector)
            if container:
                text = container.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return text[:4000]

        return soup.get_text(separator="\n", strip=True)[:4000]

    except Exception as exc:
        logger.warning("Artikeltekst ophalen mislukt voor %s: %s", url, exc)
        return ""


# ---------------------------------------------------------------------------
# Feed-parsing
# ---------------------------------------------------------------------------

def _parse_date(entry: feedparser.FeedParserDict) -> datetime:
    """Parse publication datetime from a feed entry; fallback to now()."""
    # post: always returns a timezone-aware UTC datetime
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def parse_feed_articles(
    feed: feedparser.FeedParserDict,
    source: str,
    cutoff: datetime,
) -> list[Article]:
    """Convert feed entries to Article objects filtered to after cutoff."""
    # pre: cutoff is timezone-aware; feed.entries is iterable
    # post: all returned articles have pub_date >= cutoff
    articles: list[Article] = []

    for entry in feed.entries:
        try:
            pub_date = _parse_date(entry)
            if pub_date < cutoff:
                continue

            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            if not title or not url:
                continue

            # Excerpt: summary of description
            excerpt = ""
            for attr in ("summary", "description"):
                val = getattr(entry, attr, None)
                if isinstance(val, list):
                    val = val[0].get("value", "") if val else ""
                if val:
                    soup = BeautifulSoup(str(val), "html.parser")
                    excerpt = soup.get_text(separator=" ", strip=True)[:600]
                    break

            image_url = _image_from_entry(entry)

            articles.append(
                Article(
                    title=title,
                    url=url,
                    pub_date=pub_date,
                    excerpt=excerpt,
                    image_url=image_url,
                    source=source,
                )
            )
        except Exception as exc:
            logger.warning("Entry-parsing mislukt voor %s: %s", source, exc)

    return articles


# ---------------------------------------------------------------------------
# HTML-fallback scraping
# ---------------------------------------------------------------------------

def scrape_html_fallback(
    url: str,
    session: requests.Session,
    cutoff: datetime,
) -> list[Article]:
    """Scrape article links heuristically from an HTML page (no-RSS fallback)."""
    # pre: url is a reachable HTML page
    # post: at most 10 articles returned; pub_date set to now()
    articles: list[Article] = []
    try:
        logger.info("HTML-fallback scraping voor: %s", url)
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        seen: set[str] = set()

        article_keywords = [
            "/article", "/news", "/story", "/post", "/blog",
            "/2024/", "/2025/", "/2026/",
        ]

        for a_tag in soup.find_all("a", href=True):
            href = urljoin(base, a_tag["href"])
            if href in seen:
                continue
            if not any(kw in href for kw in article_keywords):
                continue

            title = a_tag.get_text(separator=" ", strip=True)
            # Verwijder datum-achtervoegsel (bijv. "Artikel titel02/19/2026")
            title = re.sub(r"\s*\d{2}/\d{2}/\d{4}\s*$", "", title).strip()
            if len(title) < 25:
                continue

            seen.add(href)
            articles.append(
                Article(
                    title=title,
                    url=href,
                    pub_date=datetime.now(timezone.utc),
                    excerpt="",
                    source=parsed.netloc,
                )
            )

            if len(articles) >= 10:
                break

    except Exception as exc:
        logger.error("HTML-fallback mislukt voor %s: %s", url, exc)

    return articles


# ---------------------------------------------------------------------------
# Afbeelding downloaden
# ---------------------------------------------------------------------------

def download_image(url: str, dest_path: str = "/tmp/artikel_image.jpg") -> Optional[str]:
    """Download image from url to dest_path; return dest_path or None on failure."""
    # pre: url points to an image resource
    # post: file written at dest_path on success
    try:
        session = _make_session()
        resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            logger.warning("Verwacht een afbeelding, maar kreeg: %s voor %s", content_type, url)

        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info("Afbeelding gedownload naar: %s", dest_path)
        return dest_path

    except Exception as exc:
        logger.warning("Afbeelding downloaden mislukt van %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Sources laden
# ---------------------------------------------------------------------------

def load_sources() -> list[tuple[str, Optional[str]]]:
    """Load and normalise source URLs from sources.txt.

    Lines may optionally include a pipe-separated RSS feed URL:
      tweakers.net|https://tweakers.net/feeds/mixed.xml
    Returns list of (website_url, rss_override_or_None).
    """
    # pre: SOURCES_FILE is a readable UTF-8 file
    # post: each returned website_url starts with 'http'
    if not SOURCES_FILE.exists():
        logger.error("sources.txt niet gevonden: %s", SOURCES_FILE)
        return []

    sources: list[tuple[str, Optional[str]]] = []
    for line in SOURCES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        rss_override: Optional[str] = None
        if "|" in line:
            website, rss_override = line.split("|", 1)
            website = website.strip().replace(" ", "").replace("_", "")
            rss_override = rss_override.strip()
        else:
            website = line.replace(" ", "").replace("_", "")

        if not website.startswith("http"):
            website = "https://" + website

        sources.append((website, rss_override))

    return sources


# ---------------------------------------------------------------------------
# Hoofd-scraping functie
# ---------------------------------------------------------------------------

def scrape_all_sources(
    test_source: Optional[str] = None,
    lookback_days: int = 1,
    max_articles: Optional[int] = MAX_ARTICLES_FOR_SELECTION,
) -> list[Article]:
    """Scrape all configured sources; return new articles not yet in posted_urls."""
    # pre: lookback_days >= 1
    # post: result sorted newest-first; already-posted URLs excluded
    sources = load_sources()

    if test_source:
        filtered = [(s, r) for s, r in sources if test_source.lower() in s.lower()]
        if filtered:
            sources = filtered
        else:
            # Gebruik opgegeven URL direct
            url = test_source if test_source.startswith("http") else "https://" + test_source
            sources = [(url, None)]
        logger.info("Test-modus: alleen bron %s", [s for s, _ in sources])

    posted_urls = load_posted_urls()
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    logger.info("Zoek artikelen vanaf: %s (%d dag(en) terug)", cutoff.strftime("%Y-%m-%d %H:%M UTC"), lookback_days)
    all_articles: list[Article] = []

    session = _make_session()

    for source_url, rss_override in sources:
        logger.info("Verwerken: %s", source_url)
        try:
            if rss_override:
                logger.info("Directe RSS-feed opgegeven: %s", rss_override)
                raw = feedparser.parse(rss_override, agent=USER_AGENT)
                feed = raw if raw.entries else None
                if not feed:
                    logger.warning("Directe RSS-feed leeg of onbereikbaar: %s", rss_override)
            else:
                feed = try_rss_feed(source_url)

            if feed:
                articles = parse_feed_articles(feed, source_url, cutoff)
            else:
                logger.warning("Geen RSS-feed gevonden voor %s, HTML-fallback", source_url)
                articles = scrape_html_fallback(source_url, session, cutoff)

            new_articles = [a for a in articles if a.url not in posted_urls]
            logger.info(
                "%d nieuw(e) artikel(en) gevonden voor %s (van %d totaal)",
                len(new_articles),
                source_url,
                len(articles),
            )
            all_articles.extend(new_articles)

            time.sleep(1)  # Beleefd crawlen

        except Exception as exc:
            logger.error("Bron mislukt, overgeslagen — %s: %s", source_url, exc)

    # Sorteer op publicatiedatum (nieuwste eerst)
    all_articles.sort(key=lambda a: a.pub_date, reverse=True)
    if max_articles is not None and len(all_articles) > max_articles:
        logger.info(
            "Artikelen gelimiteerd van %d naar %d",
            len(all_articles),
            max_articles,
        )
        all_articles = all_articles[:max_articles]

    return all_articles
