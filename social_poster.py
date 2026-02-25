"""
Social media publicatie: post nieuwe artikelen naar Bluesky (AT Protocol).
Wordt alleen uitgevoerd wanneer ENABLE_SOCIAL_POSTING=true.

Embed-strategie:
- Afbeelding wordt groot getoond via app.bsky.embed.images
- Artikel URL staat als klikbare link facet in de posttekst
- Fallback naar app.bsky.embed.external als geen afbeelding beschikbaar
"""
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from PIL import Image

from config import BLUESKY_APP_PASSWORD, BLUESKY_HANDLE, BLUESKY_POST_DELAY_SECONDS, ENABLE_SOCIAL_POSTING

logger = logging.getLogger(__name__)

BLUESKY_HOST        = "https://bsky.social"
BLUESKY_MAX_GRAPHEMES = 300
_IMAGE_TMP          = "/tmp/tnv_bluesky_image.jpg"
_IMAGE_READY        = "/tmp/tnv_bluesky_ready.jpg"


# ---------------------------------------------------------------------------
# Grapheme-count helper
# ---------------------------------------------------------------------------

def _grapheme_len(text: str) -> int:
    """Return the number of Unicode grapheme clusters in text."""
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
# Afbeelding downloaden, voorbereiden en uploaden
# ---------------------------------------------------------------------------

def _fetch_article_image(wp_url: str) -> str | None:
    """Downloadt de featured image van het WordPress artikel.

    Pre:  wp_url is een geldige gepubliceerde WordPress URL
    Post: geeft lokaal bestandspad terug of None bij fout
    """
    headers = {"User-Agent": "TechNieuwsVandaag-Bot/1.0"}
    try:
        resp = requests.get(wp_url, timeout=10, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Probeer og:image eerst
        og_tag = soup.find("meta", property="og:image")
        if og_tag and og_tag.get("content"):
            image_url = og_tag["content"]
        else:
            img_tag = soup.select_one("article img")
            if not img_tag:
                return None
            image_url = img_tag.get("src", "")

        if not image_url:
            return None

        img_resp = requests.get(image_url, timeout=15, headers=headers)
        img_resp.raise_for_status()
        with open(_IMAGE_TMP, "wb") as f:
            f.write(img_resp.content)

        logger.info("Artikelafbeelding gedownload: %s (%d bytes)", image_url, len(img_resp.content))
        return _IMAGE_TMP

    except Exception as exc:
        logger.warning("Artikelafbeelding downloaden mislukt voor %s: %s", wp_url, exc)
        return None


def _prepare_image_for_bluesky(filepath: str) -> str:
    """Resizet en comprimeert afbeelding voor Bluesky upload (max 1MB, max 2000px breed).

    Pre:  filepath is een geldig afbeeldingsbestand
    Post: geeft pad naar geoptimaliseerd JPEG terug
    """
    img = Image.open(filepath).convert("RGB")

    if img.width > 2000:
        ratio = 2000 / img.width
        img = img.resize((2000, int(img.height * ratio)), Image.LANCZOS)

    quality = 88
    while quality > 40:
        img.save(_IMAGE_READY, "JPEG", quality=quality)
        if os.path.getsize(_IMAGE_READY) < 950_000:
            break
        quality -= 10

    size_kb = os.path.getsize(_IMAGE_READY) // 1024
    logger.info("Afbeelding klaar voor Bluesky: %d KB (quality=%d)", size_kb, quality)
    return _IMAGE_READY


def _upload_image_blob(filepath: str, session: dict) -> dict | None:
    """Upload afbeelding als blob naar Bluesky.

    Pre:  filepath bestaat en is onder 1MB; session is actief
    Post: geeft blob dict terug of None bij fout
    """
    try:
        with open(filepath, "rb") as f:
            data = f.read()

        resp = requests.post(
            f"{session['host']}/xrpc/com.atproto.repo.uploadBlob",
            headers={
                "Authorization": f"Bearer {session['accessJwt']}",
                "Content-Type":  "image/jpeg",
            },
            data=data,
            timeout=30,
        )
        resp.raise_for_status()
        blob = resp.json().get("blob")
        logger.info("Afbeelding geüpload als blob naar Bluesky (%d bytes)", len(data))
        return blob
    except Exception as exc:
        logger.warning("Blob upload mislukt: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Embed bouwen
# ---------------------------------------------------------------------------

def _build_embed(article_url: str, og_data: dict, image_blob: dict | None) -> dict:
    """Bouwt het Bluesky embed object.

    Pre:  article_url is geldig; og_data heeft title/description
    Post: app.bsky.embed.images als image_blob aanwezig,
          anders app.bsky.embed.external als fallback
    """
    if image_blob:
        # Afbeelding groot in de post; URL-link loopt via facet in tekst
        return {
            "$type": "app.bsky.embed.images",
            "images": [{
                "image":       image_blob,
                "alt":         (og_data.get("title") or "Afbeelding bij artikel")[:1000],
                "aspectRatio": {"width": 16, "height": 9},
            }],
        }
    else:
        # Fallback: link card
        return {
            "$type": "app.bsky.embed.external",
            "external": {
                "uri":         article_url,
                "title":       (og_data.get("title") or "")[:300],
                "description": (og_data.get("description") or "")[:300],
            },
        }


# ---------------------------------------------------------------------------
# Facets bouwen (hashtags + URL-link)
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


def _build_url_facet(text: str, url: str) -> dict | None:
    """Bouwt een link facet voor de URL in de tekst.

    Pre:  url komt exact voor in text
    Post: geeft facet dict terug met correcte byte-offsets, of None als URL niet gevonden
    """
    text_bytes = text.encode("utf-8")
    url_bytes  = url.encode("utf-8")
    start = text_bytes.find(url_bytes)
    if start == -1:
        return None
    return {
        "index": {"byteStart": start, "byteEnd": start + len(url_bytes)},
        "features": [{"$type": "app.bsky.richtext.facet#link", "uri": url}],
    }


# ---------------------------------------------------------------------------
# Post tekst bouwen (URL IN tekst als klikbare link)
# ---------------------------------------------------------------------------

def _format_hashtags(keywords: str) -> str:
    """Convert comma-separated keywords to space-separated hashtags (max 3)."""
    return " ".join(
        f"#{kw.strip().replace(' ', '')}"
        for kw in keywords.split(",")[:3]
        if kw.strip()
    )


def _build_post_text(title: str, summary: str, url: str, hashtags: str) -> str:
    """Bouw post tekst met URL als klikbare link op aparte regel.

    Pre:  title, summary, url, hashtags zijn strings
    Post: tekst <= BLUESKY_MAX_GRAPHEMES graphemes; URL staat op aparte regel
    """
    # Eerste 2 zinnen als intro
    sentences = summary.split(". ")
    intro = ". ".join(sentences[:2]).strip()
    if intro and not intro.endswith("."):
        intro += "."

    url_line  = f"\n\n{url}"
    hash_line = f"\n\n{hashtags}" if hashtags else ""
    reserved  = _grapheme_len(url_line) + _grapheme_len(hash_line)
    max_base  = BLUESKY_MAX_GRAPHEMES - reserved

    base = f"{title}\n\n{intro}"
    if _grapheme_len(base) <= max_base:
        return f"{base}{url_line}{hash_line}"

    # Te lang: kap intro af op woordgrens
    title_part = f"{title}\n\n"
    budget = max_base - _grapheme_len(title_part) - 1  # -1 voor "…"
    if budget <= 0:
        return f"{title}{url_line}{hash_line}"

    words = intro.split()
    trimmed = ""
    for word in words:
        candidate = (trimmed + " " + word).strip()
        if _grapheme_len(candidate) > budget:
            break
        trimmed = candidate

    intro = (trimmed + "…").strip()
    return f"{title}\n\n{intro}{url_line}{hash_line}"


# ---------------------------------------------------------------------------
# Post aanmaken
# ---------------------------------------------------------------------------

def _create_post(
    session:     dict,
    text:        str,
    facets:      list[dict],
    embed:       dict,
) -> dict:
    """Maakt een Bluesky post aan.

    Pre:  session is actief; text is max 300 graphemes
    Post: API response dict met 'uri' key bij succes; raises op HTTP-fout
    """
    record: dict = {
        "$type":     "app.bsky.feed.post",
        "text":      text,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "facets":    facets,
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
    """Post one article announcement to Bluesky with inline image.

    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are set when ENABLE_SOCIAL_POSTING=true
          post_url is the WordPress article URL
    Post: returns True on success; False on failure or when disabled
    """
    if not ENABLE_SOCIAL_POSTING:
        return False

    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        logger.warning("Bluesky credentials niet geconfigureerd (BLUESKY_HANDLE / BLUESKY_APP_PASSWORD)")
        return False

    hashtags = _format_hashtags(keywords)
    text     = _build_post_text(title, summary, post_url, hashtags)
    og_data  = fetch_og_data(post_url)

    if dry_run:
        logger.info(
            "[DRY RUN] Bluesky post (%d graphemes):\n%s\n\n"
            "[DRY RUN] OG title : %s\n"
            "[DRY RUN] OG image : %s",
            _grapheme_len(text),
            text,
            og_data.get("title", "(geen)"),
            og_data.get("image", "(geen)"),
        )
        return True

    try:
        session = _bluesky_login()

        # Afbeelding downloaden, voorbereiden en uploaden
        image_blob = None
        img_path   = _fetch_article_image(post_url)
        if img_path:
            ready_path = _prepare_image_for_bluesky(img_path)
            image_blob = _upload_image_blob(ready_path, session)

        if not image_blob:
            logger.info("Geen afbeelding beschikbaar — fallback naar link card embed")

        # Embed opbouwen
        embed = _build_embed(post_url, og_data, image_blob)

        # Facets: hashtags + URL-link
        facets = _build_hashtag_facets(text)
        url_facet = _build_url_facet(text, post_url)
        if url_facet:
            facets.append(url_facet)

        result   = _create_post(session, text, facets, embed)
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

    delay = BLUESKY_POST_DELAY_SECONDS

    for item in results:
        article  = item["article"]
        post     = item["post"]
        post_url = post.get("link") or post.get("preview_url", "")

        if dry_run:
            logger.info("[DRY RUN] Zou %ds wachten voor Bluesky post van: %s", delay, article.titel1)
        else:
            logger.info("Bluesky delay gestart (%ds) voor artikel: %s", delay, article.titel1)
            time.sleep(delay)
            logger.info("Bluesky delay voltooid, post wordt verstuurd")

        post_to_bluesky(
            title=article.titel1,
            summary=article.samenvatting,
            keywords=article.trefwoorden,
            post_url=post_url,
            dry_run=dry_run,
        )
