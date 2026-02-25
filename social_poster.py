"""
Social media publicatie: post nieuwe artikelen naar Bluesky (AT Protocol).
Wordt alleen uitgevoerd wanneer ENABLE_SOCIAL_POSTING=true.

Implementeert rich link cards via app.bsky.embed.external:
- Post body bevat titel + excerpt + hashtags (geen URL in tekst)
- Embed toont link card met titel, beschrijving en thumbnail
"""
import logging
import re
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from config import BLUESKY_APP_PASSWORD, BLUESKY_HANDLE, ENABLE_SOCIAL_POSTING

logger = logging.getLogger(__name__)

BLUESKY_HOST = "https://bsky.social"
BLUESKY_MAX_GRAPHEMES = 300


# ---------------------------------------------------------------------------
# Grapheme-count helper (Bluesky telt Unicode graphemes, niet bytes/chars)
# ---------------------------------------------------------------------------

def _grapheme_len(text: str) -> int:
    """Return the number of Unicode grapheme clusters in text."""
    # Voor de meeste tekst geldt: graphemes ≈ len(text).
    # NFC-normalisatie zorgt voor consistente telling.
    return len(unicodedata.normalize("NFC", text))


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _bluesky_login() -> dict:
    """Authenticate with Bluesky and return session dict.

    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are set
    Post: dict met accessJwt, did, host — raises on failure
    """
    resp = requests.post(
        f"{BLUESKY_HOST}/xrpc/com.atproto.server.createSession",
        json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "accessJwt": data["accessJwt"],
        "did":       data["did"],
        "host":      BLUESKY_HOST,
    }


# ---------------------------------------------------------------------------
# Open Graph ophalen
# ---------------------------------------------------------------------------

def fetch_og_data(url: str) -> dict:
    """Haalt Open Graph metadata op van de artikel-URL.

    Pre:  url is een geldige https URL
    Post: dict met keys title, description, image (URL), url;
          ontbrekende keys krijgen lege string als waarde
    """
    headers = {"User-Agent": "TechNieuwsVandaag-Bot/1.0"}
    try:
        resp = requests.get(url, timeout=10, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        def og(prop: str) -> str:
            tag = soup.find("meta", property=f"og:{prop}") or \
                  soup.find("meta", attrs={"name": f"og:{prop}"})
            return (tag["content"] if tag and tag.get("content") else "") or ""

        title_tag = soup.find("title")
        return {
            "title":       og("title") or (title_tag.string if title_tag else "") or "",
            "description": og("description"),
            "image":       og("image"),
            "url":         og("url") or url,
        }
    except Exception as exc:
        logger.warning("OG-data ophalen mislukt voor %s: %s", url, exc)
        return {"title": "", "description": "", "image": "", "url": url}


# ---------------------------------------------------------------------------
# Thumbnail uploaden
# ---------------------------------------------------------------------------

def _upload_thumbnail(image_url: str, session: dict) -> dict | None:
    """Upload een thumbnail naar Bluesky als blob.

    Pre:  image_url is bereikbaar; session bevat accessJwt en host
    Post: Bluesky blob object, of None bij fout
    """
    try:
        img_resp = requests.get(image_url, timeout=15)
        img_resp.raise_for_status()
        content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()

        upload_resp = requests.post(
            f"{session['host']}/xrpc/com.atproto.repo.uploadBlob",
            headers={
                "Authorization": f"Bearer {session['accessJwt']}",
                "Content-Type":  content_type,
            },
            data=img_resp.content,
            timeout=30,
        )
        upload_resp.raise_for_status()
        blob = upload_resp.json().get("blob")
        logger.info("Thumbnail geüpload naar Bluesky (%s, %d bytes)", content_type, len(img_resp.content))
        return blob
    except Exception as exc:
        logger.warning("Thumbnail upload mislukt: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Hashtag facets bouwen
# ---------------------------------------------------------------------------

def _build_hashtag_facets(text: str) -> list[dict]:
    """Bouwt AT Protocol facets voor hashtags in de tekst.

    Pre:  text is een geldige UTF-8 string
    Post: lijst van facet objecten met correcte byte-offsets
    """
    facets = []
    for match in re.finditer(r"#(\w+)", text):
        tag = match.group(1)
        start_byte = len(text[: match.start()].encode("utf-8"))
        end_byte   = len(text[: match.end()].encode("utf-8"))
        facets.append({
            "index": {"byteStart": start_byte, "byteEnd": end_byte},
            "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}],
        })
    return facets


# ---------------------------------------------------------------------------
# Post tekst bouwen (URL NIET in tekst)
# ---------------------------------------------------------------------------

def _format_hashtags(keywords: str) -> str:
    """Convert comma-separated keywords to space-separated hashtags (max 3)."""
    return " ".join(
        f"#{kw.strip().replace(' ', '')}"
        for kw in keywords.split(",")[:3]
        if kw.strip()
    )


def _build_post_text(title: str, summary: str, hashtags: str) -> str:
    """Bouw post tekst zonder URL; kap af op zingrens als > 300 graphemes.

    Pre:  title, summary, hashtags zijn strings
    Post: tekst <= BLUESKY_MAX_GRAPHEMES graphemes; URL staat er NIET in
    """
    # Eerste 2 zinnen als intro
    sentences = summary.split(". ")
    intro = ". ".join(sentences[:2]).strip()
    if intro and not intro.endswith("."):
        intro += "."

    text = f"{title}\n\n{intro}\n\n{hashtags}"

    if _grapheme_len(text) <= BLUESKY_MAX_GRAPHEMES:
        return text

    # Te lang: bereken budget voor intro
    base = f"{title}\n\n\n\n{hashtags}"
    budget = BLUESKY_MAX_GRAPHEMES - _grapheme_len(base) - 1  # -1 voor "…"
    if budget <= 0:
        # Zelfs zonder intro te lang: kap titel af
        return (title + "\n\n" + hashtags)[: BLUESKY_MAX_GRAPHEMES]

    # Kap intro af op woordgrens
    words = intro.split()
    trimmed = ""
    for word in words:
        candidate = (trimmed + " " + word).strip()
        if _grapheme_len(candidate) > budget:
            break
        trimmed = candidate

    intro = (trimmed + "…").strip()
    return f"{title}\n\n{intro}\n\n{hashtags}"


# ---------------------------------------------------------------------------
# Post aanmaken met embed
# ---------------------------------------------------------------------------

def _create_post(
    session: dict,
    text: str,
    article_url: str,
    og_data: dict,
    thumbnail_blob: dict | None,
) -> dict:
    """Maakt een Bluesky post aan met rich link embed.

    Pre:  session is actief; text is max 300 graphemes; URL staat NIET in text
    Post: API response dict met 'uri' key bij succes; raises op HTTP-fout
    """
    embed: dict = {
        "$type": "app.bsky.embed.external",
        "external": {
            "uri":         article_url,
            "title":       (og_data.get("title") or "")[:300],
            "description": (og_data.get("description") or "")[:300],
        },
    }
    if thumbnail_blob:
        embed["external"]["thumb"] = thumbnail_blob

    record: dict = {
        "$type":     "app.bsky.feed.post",
        "text":      text,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "facets":    _build_hashtag_facets(text),
        "embed":     embed,
        "langs":     ["nl"],
    }

    resp = requests.post(
        f"{session['host']}/xrpc/com.atproto.repo.createRecord",
        headers={
            "Authorization": f"Bearer {session['accessJwt']}",
            "Content-Type":  "application/json",
        },
        json={
            "repo":       session["did"],
            "collection": "app.bsky.feed.post",
            "record":     record,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Publieke interface
# ---------------------------------------------------------------------------

def post_to_bluesky(
    title: str,
    summary: str,
    keywords: str,
    post_url: str,
    dry_run: bool = False,
) -> bool:
    """Post one article announcement to Bluesky with rich link card.

    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are set when ENABLE_SOCIAL_POSTING=true
          post_url is the WordPress article URL (used for OG fetch + embed)
    Post: returns True on success; False on failure or when disabled
    """
    if not ENABLE_SOCIAL_POSTING:
        return False

    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        logger.warning("Bluesky credentials niet geconfigureerd (BLUESKY_HANDLE / BLUESKY_APP_PASSWORD)")
        return False

    hashtags = _format_hashtags(keywords)
    text = _build_post_text(title, summary, hashtags)

    # OG data ophalen (mislukt → lege card, geen crashl)
    og_data = fetch_og_data(post_url)

    if dry_run:
        logger.info(
            "[DRY RUN] Bluesky post (%d graphemes):\n%s\n\n"
            "[DRY RUN] Embed URL : %s\n"
            "[DRY RUN] OG title  : %s\n"
            "[DRY RUN] OG image  : %s",
            _grapheme_len(text),
            text,
            post_url,
            og_data.get("title", "(geen)"),
            og_data.get("image", "(geen)"),
        )
        return True

    try:
        session = _bluesky_login()

        thumbnail_blob = None
        if og_data.get("image"):
            thumbnail_blob = _upload_thumbnail(og_data["image"], session)

        result = _create_post(session, text, post_url, og_data, thumbnail_blob)
        post_uri = result.get("uri", "")
        logger.info("Bluesky post verstuurd: %s → %s", title, post_uri)
        return bool(post_uri)

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
        post    = item["post"]
        post_url = post.get("link") or post.get("preview_url", "")
        post_to_bluesky(
            title=article.titel1,
            summary=article.samenvatting,
            keywords=article.trefwoorden,
            post_url=post_url,
            dry_run=dry_run,
        )
