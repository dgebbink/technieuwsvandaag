"""
backfill.py — Vult de site met historische berichten.

Scrapet alle beschikbare artikelen over een lange periode, verdeelt ze in
tijdsblokken van N dagen, selecteert per blok het meest nieuwswaardige artikel
via Claude, genereert een Nederlandse samenvatting en publiceert geantidateerd
op WordPress.

Gebruik:
  python3 backfill.py                    # 60 dagen terug, 1 bericht per 2 dagen
  python3 backfill.py --days 30          # laatste 30 dagen
  python3 backfill.py --interval 3       # 1 bericht per 3 dagen
  python3 backfill.py --dry-run          # simuleer zonder te posten
  python3 backfill.py --max-posts 10     # maximaal 10 posts aanmaken
"""
import argparse
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _setup_logging(logs_dir: Path) -> logging.Logger:
    """Configure file + stdout logging for backfill; return module logger."""
    # pre: logs_dir parent is writable
    # post: log file created at logs_dir/backfill_{timestamp}.log
    logs_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = logs_dir / f"backfill_{date_str}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger(__name__)


from config import ANTHROPIC_API_KEY, LOGS_DIR  # noqa: E402

logger = _setup_logging(LOGS_DIR)

import anthropic  # noqa: E402

from ai_processor import MODEL, ProcessedArticle, process_article  # noqa: E402
from scraper import (  # noqa: E402
    Article,
    _make_session,
    download_image,
    extract_image_from_page,
    save_posted_url,
    scrape_all_sources,
)
from wordpress_client import WordPressClient  # noqa: E402


# ---------------------------------------------------------------------------
# Groeperen in tijdsblokken
# ---------------------------------------------------------------------------

def group_by_interval(articles: list[Article], interval_days: int) -> dict[int, list[Article]]:
    """Group articles into interval_days buckets; key = periods ago (0 = most recent)."""
    # pre: interval_days >= 1; all articles have timezone-aware pub_date
    # post: bucket keys are non-negative integers
    now = datetime.now(timezone.utc)
    buckets: dict[int, list[Article]] = {}

    for article in articles:
        days_ago = max(0, (now - article.pub_date).days)
        bucket_key = days_ago // interval_days
        buckets.setdefault(bucket_key, []).append(article)

    return buckets


# ---------------------------------------------------------------------------
# Beste artikel per blok selecteren
# ---------------------------------------------------------------------------

def select_best_in_bucket(
    articles: list[Article],
    client: anthropic.Anthropic,
) -> Article:
    """Ask Claude to pick the most newsworthy article from a bucket; fallback to first."""
    # pre: len(articles) >= 1; client is authenticated
    # post: always returns a valid Article from the input list
    if len(articles) == 1:
        return articles[0]

    article_list = "\n".join(
        f"{i + 1}. [{_domain(a.source)}] {a.title}\n   {a.excerpt[:200]}"
        for i, a in enumerate(articles)
    )

    prompt = (
        "Je bent redacteur van een Nederlandse tech-nieuwswebsite. "
        "Kies het MEEST nieuwswaardige en impactvolle artikel voor een Nederlands publiek "
        "uit de onderstaande lijst. Geef ALLEEN het nummer terug als integer, bijv: 3\n\n"
        f"Artikelen:\n{article_list}"
    )

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()  # type: ignore[union-attr]
        match = re.search(r"\d+", text)
        if match:
            idx = int(match.group()) - 1
            if 0 <= idx < len(articles):
                return articles[idx]
    except Exception as exc:
        logger.warning("Selectie mislukt, eerste artikel genomen: %s", exc)

    return articles[0]


# ---------------------------------------------------------------------------
# Hulpfunctie
# ---------------------------------------------------------------------------

def _domain(url: str) -> str:
    """Return the netloc (domain) from a URL string."""
    # post: returns url unchanged on parse failure
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url
    except Exception:
        return url


# ---------------------------------------------------------------------------
# Hoofd-functie
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the backfill pipeline: scrape → group → select → process → publish."""
    # post: returns 0 on success, 1 on fatal error
    parser = argparse.ArgumentParser(
        description="TechNieuwsVandaag Backfill — historische berichten genereren",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--days", type=int, default=60, help="Terugkijkperiode in dagen (standaard 60)")
    parser.add_argument("--interval", type=int, default=2, help="Dagen per bericht (standaard 2)")
    parser.add_argument("--max-posts", type=int, default=40, help="Max aantal posts (standaard 40)")
    parser.add_argument("--dry-run", action="store_true", help="Simuleer zonder te posten")
    args = parser.parse_args()

    max_expected = args.days // args.interval
    logger.info("=== Backfill gestart ===")
    logger.info("Periode  : %d dagen terug", args.days)
    logger.info("Interval : 1 bericht per %d dag(en)", args.interval)
    logger.info("Max posts: %d (verwacht ~%d)", args.max_posts, max_expected)

    if not ANTHROPIC_API_KEY:
        logger.critical("ANTHROPIC_API_KEY niet ingesteld")
        return 1

    # ------------------------------------------------------------------
    # Stap 1: Scrape alles — geen artikel-limiet voor backfill
    # ------------------------------------------------------------------
    logger.info("── Stap 1: Scrapen ──")
    articles = scrape_all_sources(lookback_days=args.days, max_articles=None)
    logger.info("%d artikelen beschikbaar na scraping", len(articles))

    if not articles:
        logger.error("Geen artikelen gevonden")
        return 1

    # ------------------------------------------------------------------
    # Stap 2: Groepeer per tijdsblok
    # ------------------------------------------------------------------
    buckets = group_by_interval(articles, args.interval)
    logger.info("%d tijdsblokken met artikelen", len(buckets))

    # Verwerk van oud naar nieuw (hoogste bucket_key = verst terug)
    sorted_buckets = sorted(buckets.items(), reverse=True)

    # Limiteer op max_posts
    if len(sorted_buckets) > args.max_posts:
        logger.info("Gelimiteerd tot %d blokken van de %d beschikbaar", args.max_posts, len(sorted_buckets))
        sorted_buckets = sorted_buckets[:args.max_posts]

    # ------------------------------------------------------------------
    # Stap 3: Per blok: selecteer → verwerk → publiceer
    # ------------------------------------------------------------------
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    wp = WordPressClient()
    http_session = _make_session()

    published = 0
    skipped = 0

    for i, (bucket_key, bucket_articles) in enumerate(sorted_buckets):
        period_label = f"{bucket_key * args.interval}–{(bucket_key + 1) * args.interval} dag(en) geleden"
        logger.info(
            "── Blok %d/%d (%s, %d artikel(en)) ──",
            i + 1, len(sorted_buckets), period_label, len(bucket_articles),
        )

        # Selecteer beste artikel
        try:
            best = select_best_in_bucket(bucket_articles, client)
        except Exception as exc:
            logger.warning("Selectie mislukt: %s — eerste genomen", exc)
            best = bucket_articles[0]

        logger.info("Geselecteerd: %s (%s)", best.title, best.pub_date.strftime("%Y-%m-%d"))

        # Samenvatting genereren
        try:
            processed = process_article(best, client)
        except Exception as exc:
            logger.error("AI-verwerking mislukt voor '%s': %s", best.title, exc)
            skipped += 1
            continue

        if not processed:
            logger.warning("AI gaf geen resultaat voor '%s'", best.title)
            skipped += 1
            continue

        # Afbeelding ophalen
        img_url = best.image_url
        if not img_url:
            img_url = extract_image_from_page(best.url, http_session)

        if img_url:
            dest = f"/tmp/tnv_bf_{i}.jpg"
            processed.image_path = download_image(img_url, dest)

        # Publiceren
        if args.dry_run:
            logger.info(
                "[DRY RUN] Zou publiceren: '%s' met datum %s",
                processed.titel1,
                best.pub_date.strftime("%Y-%m-%d"),
            )
            published += 1
        else:
            post = wp.create_draft(processed, dry_run=False)
            if post:
                save_posted_url(best.url)
                published += 1
                logger.info(
                    "Gepubliceerd: '%s' (ID %d, datum %s)",
                    processed.titel1,
                    post["id"],
                    best.pub_date.strftime("%Y-%m-%d"),
                )
            else:
                logger.error("WordPress publiceren mislukt voor '%s'", processed.titel1)
                skipped += 1

        # Korte pauze — beleefd naar API's
        time.sleep(3)

    # ------------------------------------------------------------------
    # Samenvatting
    # ------------------------------------------------------------------
    logger.info("=== Backfill klaar ===")
    logger.info("Gepubliceerd : %d", published)
    logger.info("Overgeslagen : %d", skipped)

    return 0


if __name__ == "__main__":
    sys.exit(main())
