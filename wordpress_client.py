"""
WordPress REST API client: beheert categorieën, tags, media-uploads en draft-posts.
Gebruikt Application Password authenticatie.
"""
import base64
import logging
import mimetypes
from pathlib import Path
from typing import Optional

import requests

from ai_processor import ProcessedArticle
from config import USER_AGENT, WP_APP_PASSWORD, WP_URL, WP_USERNAME

logger = logging.getLogger(__name__)


class WordPressClient:
    """Client voor de WordPress REST API (wp/v2)."""

    def __init__(self) -> None:
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
        """Geef de ID van de categorie. Maakt een nieuwe aan als die niet bestaat."""
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
        """Geef tag-IDs voor komma-gescheiden trefwoorden. Maakt ontbrekende tags aan."""
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
    ) -> Optional[dict]:
        """
        Upload een lokale afbeelding naar de WordPress media-bibliotheek.
        Geeft {'id': int, 'url': str} terug of None bij mislukking.
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
            logger.info("Afbeelding geüpload (media ID %d)", media["id"])
            return {"id": media["id"], "url": media.get("source_url", "")}

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
        """
        Maak een WordPress draft-post aan.
        Geeft een dict terug met 'id', 'preview_url' en 'title'.
        """
        if dry_run:
            logger.info("[DRY RUN] Zou draft aanmaken: %s", article.titel1)
            return {
                "id": 0,
                "preview_url": f"{WP_URL.rstrip('/')}/?p=0&preview=true",
                "title": article.titel1,
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
            media: Optional[dict] = None
            if article.image_path:
                media = self.upload_image(article.image_path)
                if not media:
                    logger.warning(
                        "Afbeelding upload mislukt, artikel '%s' wordt zonder afbeelding geplaatst",
                        article.titel1,
                    )

            # Inhoud als HTML: afbeelding bovenaan, tekst, bron-knop onderaan
            paragraphs = article.samenvatting.split("\n\n")
            content_parts = []

            if media and media.get("url"):
                content_parts.append(
                    f'<img src="{media["url"]}" alt="{article.titel1}" '
                    f'style="width:100%;height:auto;margin-bottom:1.2em;">'
                )

            content_parts += [f"<p>{p.strip()}</p>" for p in paragraphs if p.strip()]
            content_parts.append(
                f'<p><a href="{article.original.url}">Bron</a></p>'
            )

            content_html = "\n".join(content_parts)

            # Publicatiedatum: gebruik originele artikel-datum (antidateren)
            pub_date_gmt = article.original.pub_date.strftime("%Y-%m-%dT%H:%M:%S")

            post_data: dict = {
                "title": article.titel1,
                "content": content_html,
                "status": "publish",
                "date_gmt": pub_date_gmt,
                "categories": categories,
                "tags": tag_ids,
            }

            if media:
                post_data["featured_media"] = media["id"]

            # Bron-URL als custom field opslaan (vereist registratie via register_post_meta)
            # Zie README voor WordPress-configuratie
            try:
                post_data["meta"] = {"bron_url": article.original.url}
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

            logger.info("Artikel gepubliceerd: '%s' (ID %d)", article.titel1, post_id)
            return {
                "id": post_id,
                "preview_url": preview_url,
                "title": article.titel1,
            }

        except Exception as exc:
            logger.error("WordPress draft aanmaken mislukt voor '%s': %s", article.titel1, exc)
            return None


# ---------------------------------------------------------------------------
# Hoofd-functie
# ---------------------------------------------------------------------------

def publish_articles(
    processed_articles: list[ProcessedArticle],
    dry_run: bool = False,
) -> list[dict]:
    """
    Verwerk alle ProcessedArticle-objecten en maak WordPress drafts aan.
    Geeft een lijst van dicts terug: {'article': ProcessedArticle, 'post': dict}.
    """
    client = WordPressClient()
    results: list[dict] = []

    for article in processed_articles:
        post = client.create_draft(article, dry_run=dry_run)
        if post:
            results.append({"article": article, "post": post})
        else:
            logger.error("Draft aanmaken mislukt voor: %s", article.titel1)

    return results
