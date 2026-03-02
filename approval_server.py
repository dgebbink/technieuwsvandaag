#!/usr/bin/env python3
"""
Flask approval server for TechNieuwsVandaag article review.
Runs on LAN, handles Accept and Decline button clicks.
Publishes accepted articles to WordPress and Bluesky.
Deletes declined articles from WordPress.
"""

import logging
import os
import time

from flask import Flask
from dotenv import load_dotenv

from approval_store import get_token, mark_used, cleanup_expired
from wordpress_client import publish_post, delete_post, update_featured_image
from social_poster import post_to_bluesky
from mailer import send_reimage_email

load_dotenv()
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/approval_server.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)

BLUESKY_DELAY = int(os.getenv("BLUESKY_POST_DELAY_SECONDS", "60"))


@app.route("/approve/<token>")
def approve(token: str):
    """Handles Accept button click.
    Pre:  token is a URL-safe string from the email button
    Post: WordPress draft published, Bluesky post created,
          token marked used — or error page if invalid/expired
    """
    entry = get_token(token)
    if not entry or entry["action"] != "accept":
        logging.warning(f"Invalid/expired accept token: {token[:8]}…")
        return _html_response(
            "❌ Link ongeldig of verlopen",
            "Deze Accept-link is niet meer geldig (max 24 uur).",
            error=True,
        )

    post_id    = entry["post_id"]
    post_title = entry["post_title"]
    wp_url     = entry["wp_url"]

    logging.info(f"Accepting post {post_id}: {post_title}")
    mark_used(token)

    try:
        result     = publish_post(post_id)
        public_url = result.get("link", wp_url)
        logging.info(f"Published: {public_url}")

        logging.info(f"Waiting {BLUESKY_DELAY}s before Bluesky post…")
        time.sleep(BLUESKY_DELAY)

        bsky_result = post_to_bluesky(
            title=post_title,
            summary="",
            keywords="",
            post_url=public_url,
        )
        bsky_ok = bool(bsky_result)
        logging.info(f"Bluesky post: {'OK' if bsky_ok else 'FAILED'}")

        return _html_response(
            "✅ Artikel gepubliceerd",
            f"<b>{post_title}</b> is live.<br><br>"
            f"<a href='{public_url}' target='_blank'>"
            f"Bekijk artikel →</a><br><br>"
            f"Bluesky: {'✅ gepost' if bsky_ok else '⚠️ mislukt'}",
        )

    except Exception as e:
        logging.error(f"Publish failed for {post_id}: {e}")
        return _html_response(
            "⚠️ Publicatie mislukt",
            f"Fout: {str(e)}<br>Controleer WordPress admin handmatig.",
            error=True,
        )


@app.route("/decline/<token>")
def decline(token: str):
    """Handles Decline button click.
    Pre:  token is a URL-safe string from the email button
    Post: WordPress draft deleted, token marked used
          or error page if invalid/expired
    """
    entry = get_token(token)
    if not entry or entry["action"] != "decline":
        logging.warning(f"Invalid/expired decline token: {token[:8]}…")
        return _html_response(
            "❌ Link ongeldig of verlopen",
            "Deze Decline-link is niet meer geldig (max 24 uur).",
            error=True,
        )

    post_id    = entry["post_id"]
    post_title = entry["post_title"]

    logging.info(f"Declining post {post_id}: {post_title}")
    mark_used(token)

    try:
        delete_post(post_id)
        logging.info(f"Deleted post {post_id}")
        return _html_response(
            "🗑️ Artikel verwijderd",
            f"<b>{post_title}</b> is verwijderd uit WordPress.",
        )

    except Exception as e:
        logging.error(f"Delete failed for {post_id}: {e}")
        return _html_response(
            "⚠️ Verwijderen mislukt",
            f"Fout: {str(e)}<br>Verwijder handmatig via WordPress admin.",
            error=True,
        )


@app.route("/reimage/<token>")
def reimage(token: str):
    """Handles New Image button click.
    Pre:  token is a URL-safe string from the email button
    Post: new image generated via FAL.ai, uploaded to WordPress,
          new notification email sent with fresh tokens
    """
    entry = get_token(token)
    if not entry or entry["action"] != "reimage":
        logging.warning(f"Invalid/expired reimage token: {token[:8]}…")
        return _html_response(
            "❌ Link ongeldig of verlopen",
            "Deze Nieuwe-afbeelding-link is niet meer geldig (max 24 uur).",
            error=True,
        )

    post_id    = entry["post_id"]
    post_title = entry["post_title"]
    wp_url     = entry["wp_url"]
    meta       = entry.get("meta", {})
    article_text = meta.get("article_text", post_title)

    logging.info(f"Reimage request for post {post_id}: {post_title}")
    mark_used(token)

    try:
        from image_generator import generate_image_for_article
        dest = f"/tmp/tnv_reimage_{post_id}.jpg"
        new_image_path = generate_image_for_article(
            title=post_title,
            article_text=article_text,
            dest_path=dest,
            dry_run=False,
        )

        if not new_image_path:
            logging.error(f"FAL.ai image generation failed for post {post_id}")
            return _html_response(
                "⚠️ Afbeelding genereren mislukt",
                "FAL.ai kon geen nieuwe afbeelding maken. Probeer opnieuw.",
                error=True,
            )

        new_image_url = update_featured_image(
            post_id, new_image_path, alt_text=post_title
        )
        logging.info(f"New image set for post {post_id}: {new_image_url}")

        send_reimage_email(
            post_id=post_id,
            post_title=post_title,
            wp_url=wp_url,
            image_url=new_image_url,
            meta=meta,
        )
        logging.info(f"Reimage email sent for post {post_id}")

        return _html_response(
            "🖼 Nieuwe afbeelding gegenereerd",
            f"Een nieuwe afbeelding is aangemaakt voor <b>{post_title}</b>.<br><br>"
            f"Controleer je e-mail voor de nieuwe Accept/Decline knoppen.",
        )

    except Exception as e:
        logging.error(f"Reimage failed for {post_id}: {e}")
        return _html_response(
            "⚠️ Reimage mislukt",
            f"Fout: {str(e)}<br>Controleer de logs voor details.",
            error=True,
        )


@app.route("/health")
def health():
    """Health check endpoint; also cleans up expired tokens."""
    cleanup_expired()
    return "OK", 200


def _html_response(title: str, body: str, error: bool = False) -> str:
    """Renders a minimal HTML response page."""
    color = "#dc3545" if error else "#28a745"
    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width">
  <title>{title} — TechNieuwsVandaag</title>
  <style>
    body {{font-family:sans-serif;max-width:480px;
           margin:60px auto;padding:0 16px;text-align:center}}
    h1   {{color:{color};font-size:24px}}
    p    {{color:#444;line-height:1.6}}
    a    {{color:#CC0000}}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{body}</p>
  <hr style="margin:32px 0;border-color:#eee">
  <small style="color:#aaa">TechNieuwsVandaag.nl</small>
</body>
</html>"""


if __name__ == "__main__":
    host = os.getenv("APPROVAL_HOST", "0.0.0.0")
    port = int(os.getenv("APPROVAL_PORT", "5055"))
    logging.info(f"Approval server starting on {host}:{port}")
    print(f"Approval server running on http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
