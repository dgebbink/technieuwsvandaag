"""
Social media publicatie: post nieuwe artikelen naar Bluesky (AT Protocol).
Wordt alleen uitgevoerd wanneer ENABLE_SOCIAL_POSTING=true.

Embed-strategie:
- app.bsky.embed.external met thumb blob
- Afbeelding wordt groot getoond bovenin de link card
- URL staat NIET in de posttekst (loopt via embed uri)
"""
import logging
import os
import re
import secrets
import subprocess
import time
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from PIL import Image

from pathlib import Path

import json

from config import (
    BASE_DIR,
    BLUESKY_APP_PASSWORD,
    BLUESKY_HANDLE,
    BLUESKY_POST_DELAY_SECONDS,
    ENABLE_INSTAGRAM_POSTING,
    ENABLE_SOCIAL_POSTING,
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_API_VERSION,
    INSTAGRAM_MEDIA_BASE_URL,
    INSTAGRAM_MEDIA_REMOTE_DIR,
    INSTAGRAM_MEDIA_RETENTION_DAYS,
    INSTAGRAM_MEDIA_SSH_HOST,
    INSTAGRAM_QUEUE_FILE,
)

logger = logging.getLogger(__name__)

_TMP_DIR            = BASE_DIR / "tmp"
_TMP_DIR.mkdir(exist_ok=True)

BLUESKY_HOST        = "https://bsky.social"
BLUESKY_MAX_GRAPHEMES = 300
_IMAGE_TMP          = str(_TMP_DIR / "tnv_bluesky_image.jpg")
_IMAGE_READY        = str(_TMP_DIR / "tnv_bluesky_ready.jpg")


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
    """Bouwt het Bluesky embed object: external link card met optionele thumb.

    Pre:  article_url is geldig; og_data heeft title/description
    Post: app.bsky.embed.external; thumb ingevuld als image_blob aanwezig
    """
    embed: dict = {
        "$type": "app.bsky.embed.external",
        "external": {
            "uri":         article_url,
            "title":       (og_data.get("title") or "")[:300],
            "description": (og_data.get("description") or "")[:300],
        },
    }
    if image_blob:
        embed["external"]["thumb"] = image_blob
    return embed


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


def _build_post_text(title: str, summary: str, hashtags: str) -> str:
    """Bouw post tekst zonder URL; kap af op zingrens als > 300 graphemes.

    Pre:  title, summary, hashtags zijn strings
    Post: tekst <= BLUESKY_MAX_GRAPHEMES graphemes; URL staat NIET in tekst
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
    base   = f"{title}\n\n\n\n{hashtags}"
    budget = BLUESKY_MAX_GRAPHEMES - _grapheme_len(base) - 1  # -1 voor "…"
    if budget <= 0:
        return (title + "\n\n" + hashtags)[: BLUESKY_MAX_GRAPHEMES]

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
) -> str:
    """Post one article announcement to Bluesky with inline image.

    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are set when ENABLE_SOCIAL_POSTING=true
          post_url is the WordPress article URL
    Post: returns AT Protocol URI string on success; "" on failure or when disabled
    """
    if not ENABLE_SOCIAL_POSTING:
        return ""

    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        logger.warning("Bluesky credentials niet geconfigureerd (BLUESKY_HANDLE / BLUESKY_APP_PASSWORD)")
        return ""

    hashtags = _format_hashtags(keywords)
    text     = _build_post_text(title, summary, hashtags)
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
        return "at://did:plc:dryrun/app.bsky.feed.post/dryrun"

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

        # Facets: alleen hashtags (URL loopt via embed uri)
        facets = _build_hashtag_facets(text)

        result   = _create_post(session, text, facets, embed)
        post_uri = result.get("uri", "")
        logger.info("Bluesky post verstuurd: %s → %s", title, post_uri)
        return post_uri

    except Exception as exc:
        logger.error("Bluesky post mislukt voor '%s': %s", title, exc)
        return ""


def delete_bluesky_post(post_uri: str) -> bool:
    """Deletes a Bluesky post by its AT Protocol URI.

    Pre:  post_uri is a valid AT URI: at://did:plc:xxx/app.bsky.feed.post/rkey
          BLUESKY_HANDLE and BLUESKY_APP_PASSWORD are configured
    Post: returns True on success; False on failure or when disabled/unconfigured
    """
    if not ENABLE_SOCIAL_POSTING:
        return False

    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        logger.warning("Bluesky credentials niet geconfigureerd — verwijderen overgeslagen")
        return False

    if not post_uri:
        logger.warning("Lege post_uri meegegeven aan delete_bluesky_post")
        return False

    # Parse: at://did:plc:xxx/app.bsky.feed.post/rkey
    try:
        parts = post_uri.split("/")
        # at:// → parts[0]='at:', parts[1]='', parts[2]=repo, parts[3]=collection, parts[4]=rkey
        repo       = parts[2]
        collection = parts[3]
        rkey       = parts[4]
    except IndexError:
        logger.error("Ongeldige AT URI voor delete: %s", post_uri)
        return False

    try:
        session = _bluesky_login()
        resp = requests.post(
            f"{session['host']}/xrpc/com.atproto.repo.deleteRecord",
            headers={
                "Authorization": f"Bearer {session['accessJwt']}",
                "Content-Type":  "application/json",
            },
            json={
                "repo":       repo,
                "collection": collection,
                "rkey":       rkey,
            },
            timeout=20,
        )
        resp.raise_for_status()
        logger.info("Bluesky post verwijderd: %s", post_uri)
        return True

    except Exception as exc:
        logger.error("Bluesky post verwijderen mislukt (%s): %s", post_uri, exc)
        return False


# ---------------------------------------------------------------------------
# Instagram (Meta Graph API)
# ---------------------------------------------------------------------------
# Flow (zie INSTAGRAM_PLAN.md fase 6): per artikel beeld componeren
# (instagram_image.py) → scp naar de ig-media host op meterkast (publieke
# image_url — de WP media library kon dit niet aan, zie de "bekende bug" in
# INSTAGRAM_PLAN.md fase 5: WP/Imagick crasht op het XMP-blok dat Meta's
# AI-label triggert) → entry in INSTAGRAM_QUEUE_FILE (queue_instagram_post).
# Aan het eind van de dag bundelt instagram_digest.py de wachtrij tot één
# post: 1 beeld → los, 2+ → carousel (container aanmaken → pollen tot
# FINISHED → publiceren, zie post_instagram_digest).
# Let op: Instagram-posts kunnen NIET via de API verwijderd worden.

_IG_POLL_ATTEMPTS = 30
_IG_POLL_DELAY = 2.0


def _publish_image_publicly(local_path: str) -> str | None:
    """Scp een gecomponeerd beeld naar de ig-media host en geef de publieke URL terug.

    Ruimt bij elke aanroep ook bestanden op ouder dan INSTAGRAM_MEDIA_RETENTION_DAYS
    — Meta haalt het beeld maar één keer op (bij het aanmaken van de media
    container), dus een korte bewaartermijn is voldoende.

    Pre:  local_path bestaat; SSH-alias INSTAGRAM_MEDIA_SSH_HOST is bereikbaar
          zonder wachtwoord (bestaande key-auth, zie ~/.ssh/config)
    Post: retourneert de publieke https-URL, of None bij elke fout (nooit raisen)
    """
    remote_name = f"ig-{secrets.token_hex(8)}.jpg"
    remote_path = f"{INSTAGRAM_MEDIA_REMOTE_DIR}/{remote_name}"

    try:
        subprocess.run(
            ["scp", "-q", local_path, f"{INSTAGRAM_MEDIA_SSH_HOST}:{remote_path}"],
            check=True, timeout=30, capture_output=True, text=True,
        )
    except Exception as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        logger.error("Instagram-beeld uploaden naar ig-media mislukt: %s", detail)
        return None

    try:
        subprocess.run(
            ["ssh", INSTAGRAM_MEDIA_SSH_HOST,
             f"find {INSTAGRAM_MEDIA_REMOTE_DIR} -name 'ig-*.jpg' "
             f"-mtime +{INSTAGRAM_MEDIA_RETENTION_DAYS} -delete"],
            timeout=20, capture_output=True, text=True,
        )
    except Exception as exc:
        logger.warning("Opruimen oude ig-media bestanden mislukt (niet fataal): %s", exc)

    return f"{INSTAGRAM_MEDIA_BASE_URL}/{remote_name}"


def publish_video_publicly(local_path: str) -> str | None:
    """Scp een gecomponeerde Reel-video naar de ig-media host, publieke variant
    van _publish_image_publicly (zelfde host, eigen bestandsprefix 'reel-' zodat
    de cleanup-glob's elkaar niet raken).

    Pre:  local_path bestaat (zie instagram_reel.build_reel_video)
    Post: retourneert de publieke https-URL, of None bij elke fout (nooit raisen)
    """
    remote_name = f"reel-{secrets.token_hex(8)}.mp4"
    remote_path = f"{INSTAGRAM_MEDIA_REMOTE_DIR}/{remote_name}"

    try:
        subprocess.run(
            ["scp", "-q", local_path, f"{INSTAGRAM_MEDIA_SSH_HOST}:{remote_path}"],
            check=True, timeout=60, capture_output=True, text=True,
        )
    except Exception as exc:
        detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        logger.error("Reel-video uploaden naar ig-media mislukt: %s", detail)
        return None

    try:
        subprocess.run(
            ["ssh", INSTAGRAM_MEDIA_SSH_HOST,
             f"find {INSTAGRAM_MEDIA_REMOTE_DIR} -name 'reel-*.mp4' "
             f"-mtime +{INSTAGRAM_MEDIA_RETENTION_DAYS} -delete"],
            timeout=20, capture_output=True, text=True,
        )
    except Exception as exc:
        logger.warning("Opruimen oude reel-bestanden mislukt (niet fataal): %s", exc)

    return f"{INSTAGRAM_MEDIA_BASE_URL}/{remote_name}"


def _ig_graph_base() -> str:
    return f"https://graph.facebook.com/{INSTAGRAM_API_VERSION}"


def _ig_create_container(image_url: str, caption: str) -> str:
    """Maak een media container aan; retourneert container-ID, raist op fout."""
    resp = requests.post(
        f"{_ig_graph_base()}/{INSTAGRAM_ACCOUNT_ID}/media",
        params={
            "image_url": image_url,
            "caption": caption,
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    if not resp.ok:
        msg = resp.json().get("error", {}).get("message", resp.text[:200])
        raise RuntimeError(f"Instagram container aanmaken mislukt: {msg}")
    return resp.json()["id"]


def _ig_wait_finished(
    container_id: str,
    attempts: int = _IG_POLL_ATTEMPTS,
    delay: float = _IG_POLL_DELAY,
) -> None:
    """Poll de containerstatus tot FINISHED; raist bij ERROR/EXPIRED/timeout.

    Reel-containers verwerken trager dan een los beeld (video-encoding aan
    Meta's kant) — post_instagram_reel geeft daarom een ruimere attempts/delay
    mee dan de standaard image/carousel-flow.
    """
    for attempt in range(attempts):
        resp = requests.get(
            f"{_ig_graph_base()}/{container_id}",
            # Ook 'status' opvragen, niet alleen 'status_code': dat laatste geeft
            # kaal "ERROR", terwijl 'status' Meta's eigen foutcode bevat. Zonder
            # die code kostte het naderhand een losse API-aanroep om te
            # achterhalen waarom een Reel geweigerd was.
            params={
                "fields": "status_code,status",
                "access_token": INSTAGRAM_ACCESS_TOKEN,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status_code", "")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            detail = data.get("status", "") or "geen toelichting van Meta"
            raise RuntimeError(
                f"Instagram container {container_id} status: {status} — {detail}"
            )
        time.sleep(delay)
    raise RuntimeError(f"Timeout op Instagram container {container_id}")


def _ig_publish(container_id: str) -> str:
    """Publiceer een FINISHED container; retourneert de media-ID."""
    resp = requests.post(
        f"{_ig_graph_base()}/{INSTAGRAM_ACCOUNT_ID}/media_publish",
        params={"creation_id": container_id, "access_token": INSTAGRAM_ACCESS_TOKEN},
        timeout=30,
    )
    if not resp.ok:
        msg = resp.json().get("error", {}).get("message", resp.text[:200])
        raise RuntimeError(f"Instagram publiceren mislukt: {msg}")
    return resp.json()["id"]


def _ig_permalink(media_id: str) -> str:
    """Haal de permalink van een gepubliceerde post op (best effort)."""
    try:
        resp = requests.get(
            f"{_ig_graph_base()}/{media_id}",
            params={"fields": "permalink", "access_token": INSTAGRAM_ACCESS_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("permalink", "")
    except Exception as exc:
        logger.warning("Instagram permalink ophalen mislukt: %s", exc)
        return ""


_IG_REEL_POLL_ATTEMPTS = 60  # video-encoding duurt langer dan een los beeld
_IG_REEL_POLL_DELAY = 5.0    # max ~5 min wachten


def _ig_create_reel_container(video_url: str, caption: str) -> str:
    """Maak een Reel media container aan; retourneert container-ID, raist op fout."""
    resp = requests.post(
        f"{_ig_graph_base()}/{INSTAGRAM_ACCOUNT_ID}/media",
        params={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    if not resp.ok:
        msg = resp.json().get("error", {}).get("message", resp.text[:200])
        raise RuntimeError(f"Instagram Reel aanmaken mislukt: {msg}")
    return resp.json()["id"]


def post_instagram_reel(video_url: str, caption: str, dry_run: bool = False) -> str:
    """Post een silent 9:16-slideshow als Instagram Reel (weekly_reel.py).

    Pre:  video_url is een al publiek gehoste .mp4 (zie instagram_reel.py +
          publish_video_publicly); caption is al samengesteld
    Post: returns permalink (fallback media-ID) bij succes, "" bij fout/leeg.
          Nooit raisen — zelfde contract als de andere post_*-functies.
    """
    if not video_url:
        return ""

    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        logger.warning(
            "Instagram credentials niet geconfigureerd "
            "(INSTAGRAM_ACCOUNT_ID / INSTAGRAM_ACCESS_TOKEN)"
        )
        return ""

    if dry_run:
        logger.info("[DRY RUN] Instagram Reel:\nVideo: %s\nCaption:\n%s", video_url, caption)
        return "dryrun"

    try:
        container_id = _ig_create_reel_container(video_url, caption)
        _ig_wait_finished(container_id, attempts=_IG_REEL_POLL_ATTEMPTS, delay=_IG_REEL_POLL_DELAY)
        media_id = _ig_publish(container_id)
        permalink = _ig_permalink(media_id)

        logger.info("Instagram Reel gepubliceerd: %s", permalink or media_id)
        return permalink or media_id

    except Exception as exc:
        logger.error("Instagram Reel mislukt: %s", exc)
        return ""


def _load_instagram_queue() -> list[dict]:
    """Laad de dagelijkse Instagram-wachtrij.
    Post: lege lijst als het bestand nog niet bestaat
    """
    if not INSTAGRAM_QUEUE_FILE.exists():
        return []
    with open(INSTAGRAM_QUEUE_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_instagram_queue() -> list[dict]:
    """Publieke lezer voor instagram_digest.py."""
    return _load_instagram_queue()


def remove_from_instagram_queue(post_id: int) -> bool:
    """Haalt een artikel uit de dagwachtrij (bij Decline, vóórdat de digest post).

    Pre:  post_id is het WordPress post-ID
    Post: entry verwijderd indien aanwezig, wachtrij herschreven; returns
          True als er iets verwijderd is, anders False (ook als het bestand
          nog niet bestaat — nooit raisen)
    """
    queue = _load_instagram_queue()
    filtered = [entry for entry in queue if entry.get("post_id") != post_id]
    if len(filtered) == len(queue):
        return False
    with open(INSTAGRAM_QUEUE_FILE, "w", encoding="utf-8") as fh:
        json.dump(filtered, fh, indent=2)
    return True


def queue_instagram_post(
    ig_kop: str,
    ig_tekst: str,
    trefwoorden: str,
    kicker: str,
    src_image_path: str,
    post_id: int,
    decline_token: str,
    dry_run: bool = False,
) -> bool:
    """Componeer het artikelbeeld en zet het klaar voor de dagelijkse IG-digest.

    Bij lage volgersaantallen is 5 losse Instagram-posts/dag te veel — dit
    vervangt de vroegere directe post_to_instagram(): elk artikel wordt hier
    alleen gecomponeerd + publiek gehost; instagram_digest.py bundelt de
    wachtrij aan het eind van de dag tot één (carousel-)post.

    Pre:  ENABLE_INSTAGRAM_POSTING vereist INSTAGRAM_ACCOUNT_ID en
          INSTAGRAM_ACCESS_TOKEN; src_image_path is de lokale artikelafbeelding (16:9)
    Post: bij succes: entry toegevoegd aan INSTAGRAM_QUEUE_FILE, True.
          False bij fout/uitgeschakeld/ontbrekende velden — nooit raisen.
    """
    if not ENABLE_INSTAGRAM_POSTING:
        return False

    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        logger.warning(
            "Instagram credentials niet geconfigureerd "
            "(INSTAGRAM_ACCOUNT_ID / INSTAGRAM_ACCESS_TOKEN)"
        )
        return False

    if not src_image_path or not os.path.exists(src_image_path):
        logger.warning("Geen lokale afbeelding voor Instagram — artikel overgeslagen")
        return False

    if not ig_kop or not ig_tekst:
        logger.warning("ig_kop/ig_tekst ontbreekt — artikel overgeslagen voor Instagram")
        return False

    from instagram_image import compose_instagram_image  # noqa: PLC0415

    dest_path = str(_TMP_DIR / f"tnv_instagram_{post_id}.jpg")
    composed = compose_instagram_image(src_image_path, ig_kop, kicker, dest_path)
    if not composed:
        return False

    if dry_run:
        logger.info(
            "[DRY RUN] Instagram queue:\nKop  : %s\nBeeld: %s\nTekst:\n%s",
            ig_kop, composed, ig_tekst,
        )
        return True

    image_url = _publish_image_publicly(composed)
    if not image_url:
        logger.error("Instagram-beeld publiek hosten mislukt — artikel overgeslagen")
        return False

    queue = _load_instagram_queue()
    queue.append({
        "post_id":       post_id,
        "decline_token": decline_token,
        "ig_kop":        ig_kop,
        "ig_tekst":      ig_tekst,
        "trefwoorden":   trefwoorden,
        "image_url":     image_url,
        "queued_at":     datetime.now(timezone.utc).isoformat(),
    })
    with open(INSTAGRAM_QUEUE_FILE, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, indent=2)

    logger.info("Instagram-artikel in dagwachtrij gezet: %s", ig_kop)
    return True


_IG_CAROUSEL_MAX = 10  # Graph API-limiet voor carousel-items


def _ig_create_carousel_child(image_url: str) -> str:
    """Maak een carousel-item aan (geen eigen caption); retourneert container-ID."""
    resp = requests.post(
        f"{_ig_graph_base()}/{INSTAGRAM_ACCOUNT_ID}/media",
        params={
            "image_url": image_url,
            "is_carousel_item": "true",
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    if not resp.ok:
        msg = resp.json().get("error", {}).get("message", resp.text[:200])
        raise RuntimeError(f"Instagram carousel-item aanmaken mislukt: {msg}")
    return resp.json()["id"]


def _ig_create_carousel_container(children_ids: list[str], caption: str) -> str:
    """Maak de carousel-ouder aan; retourneert container-ID."""
    resp = requests.post(
        f"{_ig_graph_base()}/{INSTAGRAM_ACCOUNT_ID}/media",
        params={
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
            "caption": caption,
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=30,
    )
    if not resp.ok:
        msg = resp.json().get("error", {}).get("message", resp.text[:200])
        raise RuntimeError(f"Instagram carousel aanmaken mislukt: {msg}")
    return resp.json()["id"]


def post_instagram_digest(
    image_urls: list[str],
    caption: str,
    dry_run: bool = False,
) -> str:
    """Post de gequeuede artikelen van de dag als één Instagram-post.

    Pre:  image_urls bevat 1..INSTAGRAM_CAROUSEL_MAX al publiek gehoste
          beeld-URL's (zie queue_instagram_post); caption is al samengesteld
          (zie ai_processor.build_daily_ig_caption)
    Post: 1 URL → los beeld; 2+ URL's → carousel-post.
          Returns permalink (fallback media-ID) bij succes, "" bij fout/leeg.
          Nooit raisen — zelfde contract als post_to_bluesky.
    """
    if not image_urls:
        return ""

    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        logger.warning(
            "Instagram credentials niet geconfigureerd "
            "(INSTAGRAM_ACCOUNT_ID / INSTAGRAM_ACCESS_TOKEN)"
        )
        return ""

    if dry_run:
        logger.info(
            "[DRY RUN] Instagram digest: %d beeld(en)\nCaption:\n%s",
            len(image_urls), caption,
        )
        return "dryrun"

    try:
        if len(image_urls) == 1:
            container_id = _ig_create_container(image_urls[0], caption)
        else:
            children = [_ig_create_carousel_child(url) for url in image_urls]
            for child_id in children:
                _ig_wait_finished(child_id)
            container_id = _ig_create_carousel_container(children, caption)

        _ig_wait_finished(container_id)
        media_id = _ig_publish(container_id)
        permalink = _ig_permalink(media_id)

        logger.info(
            "Instagram digest gepubliceerd (%d beeld(en)): %s",
            len(image_urls), permalink or media_id,
        )
        return permalink or media_id

    except Exception as exc:
        logger.error("Instagram digest mislukt: %s", exc)
        return ""


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
            logger.info("[DRY RUN] Zou %ds wachten voor Bluesky post van: %s", delay, article.titel)
        else:
            logger.info("Bluesky delay gestart (%ds) voor artikel: %s", delay, article.titel)
            time.sleep(delay)
            logger.info("Bluesky delay voltooid, post wordt verstuurd")

        uri = post_to_bluesky(
            title=article.titel,
            summary=article.samenvatting,
            keywords=article.trefwoorden,
            post_url=post_url,
            dry_run=dry_run,
        )
        logger.info("Bluesky post: %s", uri or "MISLUKT")
