"""
Social media publicatie: post nieuwe artikelen naar Bluesky (AT Protocol).
Wordt alleen uitgevoerd wanneer ENABLE_SOCIAL_POSTING=true.
"""
import logging

from config import BLUESKY_APP_PASSWORD, BLUESKY_HANDLE, ENABLE_SOCIAL_POSTING

logger = logging.getLogger(__name__)

BLUESKY_MAX_CHARS = 300


def post_to_bluesky(
    title: str,
    summary: str,
    keywords: str,
    post_url: str,
    dry_run: bool = False,
) -> bool:
    """Post one article announcement to Bluesky; return True on success.

    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are set when ENABLE_SOCIAL_POSTING=true
    Post: returns False on failure, when disabled, or missing credentials
    """
    if not ENABLE_SOCIAL_POSTING:
        return False

    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        logger.warning("Bluesky credentials niet geconfigureerd (BLUESKY_HANDLE / BLUESKY_APP_PASSWORD)")
        return False

    try:
        from atproto import Client  # type: ignore[import]
    except ImportError:
        logger.error("atproto pakket niet geïnstalleerd — voer 'pip install atproto' uit")
        return False

    # Bouw bericht op: titel + eerste 2 zinnen + URL + 3 hashtags
    sentences = summary.split(". ")
    intro = ". ".join(sentences[:2]).strip()
    if intro and not intro.endswith("."):
        intro += "."

    hashtags = " ".join(
        f"#{kw.strip().replace(' ', '')}"
        for kw in keywords.split(",")[:3]
        if kw.strip()
    )

    text = f"{title}\n\n{intro}\n\n{post_url}\n\n{hashtags}"

    # Inkorten tot Bluesky-limiet van 300 tekens
    if len(text) > BLUESKY_MAX_CHARS:
        overhead = len(title) + len(post_url) + len(hashtags) + 8
        budget = max(0, BLUESKY_MAX_CHARS - overhead)
        intro = intro[:budget] + "…"
        text = f"{title}\n\n{intro}\n\n{post_url}\n\n{hashtags}"

    text = text[:BLUESKY_MAX_CHARS]

    if dry_run:
        logger.info("[DRY RUN] Bluesky post (%d tekens):\n%s", len(text), text)
        return True

    try:
        client = Client()
        client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)
        client.send_post(text=text)
        logger.info("Bluesky post verstuurd: %s", title)
        return True
    except Exception as exc:
        logger.error("Bluesky post mislukt voor '%s': %s", title, exc)
        return False


def post_articles_to_social(results: list[dict], dry_run: bool = False) -> None:
    """Post all published articles to enabled social media channels.

    Pre:  results is a list of {article, post} dicts from publish_articles()
    Post: failures are logged and do not raise; no-op when ENABLE_SOCIAL_POSTING=false
    """
    if not ENABLE_SOCIAL_POSTING:
        return

    for item in results:
        article = item["article"]
        post = item["post"]
        post_url = post.get("link") or post.get("preview_url", "")
        post_to_bluesky(
            title=article.titel1,
            summary=article.samenvatting,
            keywords=article.trefwoorden,
            post_url=post_url,
            dry_run=dry_run,
        )
