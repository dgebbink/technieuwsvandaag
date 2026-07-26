"""
WordPress REST API client: beheert categorieën, tags, media-uploads en draft-posts.
Gebruikt Application Password authenticatie.
"""
import base64
import logging
import mimetypes
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

from ai_processor import ProcessedArticle
from config import (
    EDITORIAL_CATEGORY,
    IMAGE_STRATEGY,
    USER_AGENT,
    WP_APP_PASSWORD,
    WP_URL,
    WP_USERNAME,
)

logger = logging.getLogger(__name__)


class WordPressClient:
    """Client voor de WordPress REST API (wp/v2)."""

    def __init__(self) -> None:
        """Initialise WordPress REST client with Basic-Auth session."""
        # pre: WP_URL, WP_USERNAME, WP_APP_PASSWORD are configured
        # post: self.session carries Authorization header
        self.base_url = WP_URL.rstrip("/") + "/wp-json/wp/v2"

        credentials = f"{WP_USERNAME}:{WP_APP_PASSWORD}"
        token = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

        self._auth_header = f"Basic {token}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": self._auth_header,
                "User-Agent": USER_AGENT,
            }
        )

    # ------------------------------------------------------------------
    # Categorieën
    # ------------------------------------------------------------------

    def get_or_create_category(self, name: str) -> Optional[int]:
        """Return category ID by name, creating it if absent."""
        # pre: name is non-empty
        # post: returns None only on unrecoverable API error
        try:
            # Zoek op naam (werkt mogelijk niet voor namen met & of speciale tekens)
            resp = self.session.get(
                f"{self.base_url}/categories",
                params={"search": name, "per_page": 50},
                timeout=15,
            )
            resp.raise_for_status()
            for cat in resp.json():
                if cat["name"].lower() == name.lower():
                    logger.info("Categorie gevonden: '%s' (ID %d)", name, cat["id"])
                    return cat["id"]

            # Niet gevonden via search → probeer aan te maken
            logger.info("Categorie aanmaken: '%s'", name)
            resp = self.session.post(
                f"{self.base_url}/categories",
                json={"name": name},
                timeout=15,
            )

            # 400 betekent vaak "term bestaat al" — WordPress geeft het ID terug in de foutmelding
            if resp.status_code == 400:
                try:
                    error_data = resp.json()
                    term_id = (
                        error_data.get("data", {}).get("term_id")
                        or (error_data.get("additional_data") or [None])[0]
                    )
                    if term_id:
                        logger.info("Categorie '%s' bestaat al (ID %d)", name, term_id)
                        return int(term_id)
                except Exception:
                    pass
                logger.error("Categorie aanmaken gaf onverwachte 400 voor '%s': %s", name, resp.text)
                return None

            resp.raise_for_status()
            cat_id: int = resp.json()["id"]
            logger.info("Categorie aangemaakt: '%s' (ID %d)", name, cat_id)
            return cat_id

        except Exception as exc:
            logger.error("Categorie ophalen/aanmaken mislukt voor '%s': %s", name, exc)
            return None

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def get_or_create_tags(self, keywords: str) -> list[int]:
        """Return tag IDs for comma-separated keywords, creating missing ones."""
        # pre: keywords is a comma-separated string
        # post: may return fewer IDs than keywords on partial failure
        tag_ids: list[int] = []

        for keyword in keywords.split(","):
            keyword = keyword.strip()
            if not keyword:
                continue
            try:
                resp = self.session.get(
                    f"{self.base_url}/tags",
                    params={"search": keyword, "per_page": 10},
                    timeout=15,
                )
                resp.raise_for_status()
                tags = resp.json()

                existing = next(
                    (t for t in tags if t["name"].lower() == keyword.lower()), None
                )
                if existing:
                    tag_ids.append(existing["id"])
                else:
                    resp = self.session.post(
                        f"{self.base_url}/tags",
                        json={"name": keyword},
                        timeout=15,
                    )
                    resp.raise_for_status()
                    tag_ids.append(resp.json()["id"])

            except Exception as exc:
                logger.warning("Tag ophalen/aanmaken mislukt voor '%s': %s", keyword, exc)

        return tag_ids

    # ------------------------------------------------------------------
    # Media-upload
    # ------------------------------------------------------------------

    def upload_image(
        self,
        image_path: str,
        filename: str = "featured-image.jpg",
        alt_text: str = "",
    ) -> Optional[dict]:
        """Upload a local image to the WordPress media library; return {id, url} or None.

        Pre:  image_path exists on disk
        Post: alt_text set via follow-up PATCH if provided; None on any failure
        """
        path = Path(image_path)
        if not path.exists():
            logger.warning("Afbeeldingsbestand niet gevonden: %s", image_path)
            return None

        mime_type, _ = mimetypes.guess_type(str(path))
        mime_type = mime_type or "image/jpeg"

        try:
            with open(path, "rb") as img_file:
                image_data = img_file.read()

            resp = self.session.post(
                f"{self.base_url}/media",
                headers={
                    "Authorization": self._auth_header,
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type": mime_type,
                },
                data=image_data,
                timeout=30,
            )
            resp.raise_for_status()
            media = resp.json()
            media_id: int = media["id"]
            logger.info("Afbeelding geüpload (media ID %d)", media_id)

            # Alt-tekst instellen via follow-up PATCH
            if alt_text:
                try:
                    self.session.patch(
                        f"{self.base_url}/media/{media_id}",
                        json={"alt_text": alt_text[:125]},
                        timeout=15,
                    )
                except Exception as exc:
                    logger.warning("Alt-tekst instellen mislukt voor media %d: %s", media_id, exc)

            return {"id": media_id, "url": media.get("source_url", "")}

        except Exception as exc:
            logger.error("Afbeelding uploaden mislukt voor '%s': %s", image_path, exc)
            return None

    # ------------------------------------------------------------------
    # Post aanmaken
    # ------------------------------------------------------------------

    def create_draft(
        self,
        article: ProcessedArticle,
        dry_run: bool = False,
    ) -> Optional[dict]:
        """Publish article to WordPress; return {id, preview_url, link, title, image_url} or None."""
        # pre: article.original.url and pub_date are valid
        # post: returns None on any WordPress API failure
        if dry_run:
            logger.info("[DRY RUN] Zou artikel publiceren: %s", article.titel)
            return {
                "id": 0,
                "preview_url": f"{WP_URL.rstrip('/')}/?p=0&preview=true",
                "link":        f"{WP_URL.rstrip('/')}/?p=0",
                "title":       article.titel,
                "image_url":   "",
            }

        try:
            # Categorieën (max 3)
            categories = []
            for cat_name in article.categorieen:
                cat_id = self.get_or_create_category(cat_name)
                if cat_id:
                    categories.append(cat_id)

            # Tags
            tag_ids = self.get_or_create_tags(article.trefwoorden)

            # Afbeelding uploaden
            alt_text = f"{article.focus_keyword} {article.titel}".strip()
            media: Optional[dict] = None
            if article.image_path:
                media = self.upload_image(article.image_path, alt_text=alt_text)
                if not media:
                    logger.warning(
                        "Afbeelding upload mislukt, artikel '%s' wordt zonder afbeelding geplaatst",
                        article.titel,
                    )

            # Inhoud als HTML: afbeelding bovenaan, tekst, bron-knop onderaan
            paragraphs = article.samenvatting.split("\n\n")
            content_parts = []

            # Afbeelding alleen in content plaatsen bij scrape-modus met caption
            # (featured_media zorgt al voor de afbeelding bovenaan bij generate-modus)
            if media and media.get("url") and IMAGE_STRATEGY == "scrape" and article.image_caption:
                content_parts.append(
                    f'<figure>'
                    f'<img src="{media["url"]}" alt="{alt_text}" '
                    f'style="width:100%;height:auto;margin-bottom:0.4em;">'
                    f'<figcaption style="font-size:0.8em;color:#888;">'
                    f'{article.image_caption}'
                    f'</figcaption>'
                    f'</figure>'
                )

            content_parts += [f"<p>{p.strip()}</p>" for p in paragraphs if p.strip()]

            content_html = "\n".join(content_parts)

            # Publicatiedatum: gebruik originele artikel-datum (antidateren)
            pub_date_gmt = article.original.pub_date.strftime("%Y-%m-%dT%H:%M:%S")

            post_data: dict = {
                "title": article.titel,
                "content": content_html,
                "status": "publish",
                "date_gmt": pub_date_gmt,
                "categories": categories,
                "tags": tag_ids,
            }

            if media:
                post_data["featured_media"] = media["id"]

            if article.slug:
                post_data["slug"] = article.slug

            # Meta-velden: bron, SEO (Yoast + RankMath), schema markup
            # Zie README voor WordPress register_post_meta vereisten
            try:
                meta: dict = {
                    "bron_url": article.original.url,
                    "schema_article_type": "NewsArticle",
                }
                if article.meta_description:
                    meta["_yoast_wpseo_metadesc"] = article.meta_description
                    meta["rank_math_description"] = article.meta_description
                if article.focus_keyword:
                    meta["_yoast_wpseo_focuskw"] = article.focus_keyword
                    meta["rank_math_focus_keyword"] = article.focus_keyword
                if IMAGE_STRATEGY == "scrape" and article.bron_image_url:
                    meta["bron_image_url"] = article.bron_image_url
                post_data["meta"] = meta
                resp = self.session.post(
                    f"{self.base_url}/posts",
                    json=post_data,
                    timeout=30,
                )
                resp.raise_for_status()
            except requests.HTTPError as http_err:
                if resp.status_code == 400 and "bron_url" in resp.text:
                    # Custom field niet geregistreerd → opnieuw zonder meta
                    logger.warning(
                        "Meta-veld 'bron_url' niet geregistreerd in WordPress, "
                        "artikel zonder meta geplaatst"
                    )
                    del post_data["meta"]
                    resp = self.session.post(
                        f"{self.base_url}/posts",
                        json=post_data,
                        timeout=30,
                    )
                    resp.raise_for_status()
                else:
                    raise http_err

            post = resp.json()
            post_id: int = post["id"]
            preview_url = f"{WP_URL.rstrip('/')}/?p={post_id}&preview=true"
            post_link: str = post.get("link", preview_url)

            logger.info("Artikel gepubliceerd: '%s' (ID %d)", article.titel, post_id)
            return {
                "id": post_id,
                "preview_url": preview_url,
                "link": post_link,
                "title": article.titel,
                "image_url": media["url"] if media else "",
            }

        except Exception as exc:
            logger.error("WordPress draft aanmaken mislukt voor '%s': %s", article.titel, exc)
            return None


# ---------------------------------------------------------------------------
# Hoofd-functie
# ---------------------------------------------------------------------------

def publish_articles(
    processed_articles: list[ProcessedArticle],
    dry_run: bool = False,
) -> list[dict]:
    """Create WordPress posts for all articles; return list of {article, post} dicts."""
    # pre: processed_articles is non-empty
    # post: failed posts are logged and excluded from result
    client = WordPressClient()
    results: list[dict] = []

    for article in processed_articles:
        post = client.create_draft(article, dry_run=dry_run)
        if post:
            results.append({"article": article, "post": post})
        else:
            logger.error("Publiceren mislukt voor: %s", article.titel)

    return results


# ---------------------------------------------------------------------------
# Recent gepubliceerde artikelen (voor duplicate-topic check)
# ---------------------------------------------------------------------------

def create_editorial_draft(
    titel: str,
    inhoud: str,
    trefwoorden: str = "",
    categorie: str = "Editorial",
    dry_run: bool = False,
) -> Optional[dict]:
    """Zet een editorial als DRAFT in WordPress; return {id, preview_url, title} of None.

    Bewust los van create_draft(): die verwacht een ProcessedArticle met een
    bronartikel, afbeelding en antidatering, en publiceert direct. Een editorial
    heeft geen bron-URL en geen beeld, en moet juist blijven staan tot een mens
    op Publiceer klikt — een expliciet standpunt hoort niet ongelezen live.

    Pre:  titel en inhoud zijn niet-leeg; inhoud heeft '\\n\\n' tussen alinea's
    Post: draft aangemaakt (status='draft'), None bij elke API-fout — nooit raisen
    """
    if dry_run:
        logger.info("[DRY RUN] Zou editorial-draft aanmaken: %s", titel)
        return {
            "id": 0,
            "preview_url": f"{WP_URL.rstrip('/')}/?p=0&preview=true",
            "title": titel,
        }

    client = WordPressClient()
    try:
        categories = []
        cat_id = client.get_or_create_category(categorie)
        if cat_id:
            categories.append(cat_id)

        tag_ids = client.get_or_create_tags(trefwoorden) if trefwoorden else []

        content_html = "\n".join(
            f"<p>{p.strip()}</p>" for p in inhoud.split("\n\n") if p.strip()
        )

        post_data: dict = {
            "title": titel,
            "content": content_html,
            "status": "draft",
            "categories": categories,
            "tags": tag_ids,
        }

        resp = client.session.post(
            f"{client.base_url}/posts", json=post_data, timeout=30,
        )
        resp.raise_for_status()

        post = resp.json()
        post_id: int = post["id"]
        logger.info("Editorial-draft aangemaakt: '%s' (ID %d)", titel, post_id)
        return {
            "id": post_id,
            "preview_url": f"{WP_URL.rstrip('/')}/?p={post_id}&preview=true",
            "title": titel,
        }

    except Exception as exc:
        logger.error("Editorial-draft aanmaken mislukt voor '%s': %s", titel, exc)
        return None


def find_category_id(name: str) -> Optional[int]:
    """Zoekt een categorie-ID op naam, zónder hem aan te maken.

    Bewust naast get_or_create_category(): in een leespad (zoals de Reel-selectie)
    mag een lookup geen categorie aanmaken als bijwerking.

    Pre:  name is niet-leeg
    Post: ID of None als de categorie niet bestaat of de API faalt
    """
    client = WordPressClient()
    try:
        resp = client.session.get(
            f"{client.base_url}/categories",
            params={"search": name, "per_page": 50, "_fields": "id,name"},
            timeout=15,
        )
        resp.raise_for_status()
        for cat in resp.json():
            if cat["name"].lower() == name.lower():
                return int(cat["id"])
        return None
    except Exception as exc:
        logger.warning("Categorie-ID opzoeken mislukt voor '%s': %s", name, exc)
        return None


def update_editorial_draft(
    post_id: int,
    titel: str,
    inhoud: str,
    trefwoorden: str = "",
) -> bool:
    """Werkt een bestaande editorial-draft bij met een herschreven versie.

    Bewust bijwerken i.p.v. een nieuwe post: zo blijft het bij één draft hoe
    vaak je ook laat herschrijven, en blijven eerder verstuurde publish-tokens
    naar hetzelfde (bijgewerkte) stuk wijzen.

    Pre:  post_id verwijst naar een bestaande draft
    Post: titel/inhoud/tags bijgewerkt, status blijft 'draft'; False bij elke
          API-fout — nooit raisen
    """
    client = WordPressClient()
    try:
        content_html = "\n".join(
            f"<p>{p.strip()}</p>" for p in inhoud.split("\n\n") if p.strip()
        )
        post_data: dict = {"title": titel, "content": content_html}
        if trefwoorden:
            tag_ids = client.get_or_create_tags(trefwoorden)
            if tag_ids:
                post_data["tags"] = tag_ids

        resp = client.session.post(
            f"{client.base_url}/posts/{post_id}", json=post_data, timeout=30,
        )
        resp.raise_for_status()
        logger.info("Editorial-draft %d bijgewerkt: '%s'", post_id, titel)
        return True

    except Exception as exc:
        logger.error("Editorial-draft %d bijwerken mislukt: %s", post_id, exc)
        return False


def fetch_recent_published(limit: int = 10) -> list[dict]:
    """Fetch the most recently published posts (title + excerpt) for dedup checks.
    Pre:  WP REST API reachable; limit >= 1
    Post: returns list[dict] with plain-text keys 'title', 'excerpt' and 'link',
          newest first; returns [] on any API error
    """
    import re as _re
    from html import unescape

    def _strip(html: str) -> str:
        return unescape(_re.sub(r"<[^>]+>", "", html or "")).strip()

    client = WordPressClient()
    try:
        resp = client.session.get(
            f"{client.base_url}/posts",
            params={
                "per_page": limit,
                "status":   "publish",
                "orderby":  "date",
                "order":    "desc",
                "_fields":  "title,excerpt,link",
            },
            timeout=15,
        )
        resp.raise_for_status()
        posts = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Laatste gepubliceerde artikelen ophalen mislukt: %s", exc)
        return []

    return [
        {
            "title":   _strip(p.get("title", {}).get("rendered", "")),
            "excerpt": _strip(p.get("excerpt", {}).get("rendered", "")),
            "link":    p.get("link", ""),
        }
        for p in posts
    ]


def fetch_posts_for_reel(days: int = 7) -> list[dict]:
    """Fetch één gepubliceerd artikel per dag over de laatste `days` dagen
    (titel + featured-image URL), voor de wekelijkse Instagram-Reel-recap.

    Pre:  WP REST API bereikbaar; days >= 1
    Post: lijst van dicts met 'title', 'link', 'date', 'image_url' — het
          laatst-gepubliceerde artikel van elke dag dat een featured image
          had, oudste dag eerst; [] bij elke API-fout
    """
    import re as _re
    from html import unescape

    def _strip(html: str) -> str:
        return unescape(_re.sub(r"<[^>]+>", "", html or "")).strip()

    client = WordPressClient()
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    params: dict = {
        "after":     since,
        "status":    "publish",
        "per_page":  100,
        "orderby":   "date",
        "order":     "desc",
        "_embed":    "wp:featuredmedia",
    }

    # Editorials horen niet in de weekrecap: het zijn opiniestukken, geen
    # nieuws, en op Instagram is de link niet klikbaar — een standpunt zonder
    # onderbouwing eronder. Expliciet uitsluiten, want ze vielen tot nu toe
    # alleen buiten de Reel doordat ze geen featured image hadden; zodra ze die
    # wél kregen zouden ze er vanzelf in glippen.
    editorial_id = find_category_id(EDITORIAL_CATEGORY)
    if editorial_id:
        params["categories_exclude"] = editorial_id

    try:
        resp = client.session.get(
            f"{client.base_url}/posts",
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        posts = resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Posts voor weekly reel ophalen mislukt: %s", exc)
        return []

    per_day: dict[str, dict] = {}
    for p in posts:
        day = p.get("date", "")[:10]
        if day in per_day:
            continue  # al een (nieuwere) post van die dag
        media = p.get("_embedded", {}).get("wp:featuredmedia", [{}])
        image_url = media[0].get("source_url", "") if media else ""
        if not image_url:
            continue
        per_day[day] = {
            "title":     _strip(p.get("title", {}).get("rendered", "")),
            "link":      p.get("link", ""),
            "date":      day,
            "image_url": image_url,
        }

    return [per_day[d] for d in sorted(per_day)]


# ---------------------------------------------------------------------------
# Publiceren / verwijderen van individuele posts
# ---------------------------------------------------------------------------

def publish_post(post_id: int) -> dict:
    """Publishes a WordPress draft post.
    Pre:  post_id is a valid draft post ID
    Post: post status changed to publish; returns updated post dict with link
    """
    client = WordPressClient()
    resp = client.session.post(
        f"{client.base_url}/posts/{post_id}",
        json={"status": "publish"},
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("Post %d gepubliceerd", post_id)
    return resp.json()


def update_featured_image(post_id: int, image_path: str, alt_text: str = "") -> str:
    """Uploads a new image and sets it as the featured image for an existing post.
    Pre:  post_id is a valid WP post ID, image_path exists on disk
    Post: new image uploaded and set as featured_media; returns new image URL or ""
    """
    client = WordPressClient()
    media  = client.upload_image(image_path, alt_text=alt_text)
    if not media:
        return ""
    resp = client.session.post(
        f"{client.base_url}/posts/{post_id}",
        json={"featured_media": media["id"]},
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("Featured image bijgewerkt voor post %d (media %d)", post_id, media["id"])
    return media["url"]


def delete_post(post_id: int) -> None:
    """Permanently deletes a WordPress post (bypasses trash).
    Pre:  post_id is a valid post ID
    Post: post deleted from WordPress
    """
    client = WordPressClient()
    resp = client.session.delete(
        f"{client.base_url}/posts/{post_id}",
        params={"force": True},
        timeout=30,
    )
    resp.raise_for_status()
    logger.info("Post %d verwijderd", post_id)
