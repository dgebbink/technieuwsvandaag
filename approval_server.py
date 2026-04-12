#!/usr/bin/env python3
"""
Flask approval server for TechNieuwsVandaag article review.
Runs on LAN, handles Decline and New Image button clicks.
Decline deletes the Bluesky post and WordPress post.
New Image regenerates the featured image without consuming the token.
"""

import logging
import os
import threading
import uuid

from flask import Flask, request, jsonify
from dotenv import load_dotenv

from approval_store import get_token, mark_used, cleanup_expired, update_bluesky_uri
from wordpress_client import delete_post, update_featured_image
from social_poster import delete_bluesky_post
from mailer import send_reimage_email

load_dotenv()
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/approval_server.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)

# In-memory job store for dashboard submit requests
_jobs: dict = {}  # job_id -> {"status": "pending"/"done"/"error", "result": dict}


@app.route("/decline/<token>")
def decline(token: str):
    """Handles Decline button click.
    Pre:  token is a URL-safe string from the email button
    Post: Bluesky post deleted (if URI present), WordPress post deleted,
          token marked used — or error page if invalid/expired
    """
    entry = get_token(token)
    if not entry or entry["action"] != "decline":
        logging.warning(f"Invalid/expired decline token: {token[:8]}…")
        return _html_response(
            "Link ongeldig of verlopen",
            "Deze Decline-link is niet meer geldig (max 4 uur na publicatie).",
            error=True,
        )

    post_id      = entry["post_id"]
    post_title   = entry["post_title"]
    bluesky_uri  = entry.get("bluesky_uri", "")

    logging.info(f"Declining post {post_id}: {post_title}")
    mark_used(token)

    bsky_msg = ""
    if bluesky_uri:
        try:
            ok = delete_bluesky_post(bluesky_uri)
            bsky_msg = (
                "Bluesky post verwijderd.<br>"
                if ok else
                "Bluesky post verwijderen mislukt (handmatig verwijderen).<br>"
            )
            logging.info(f"Bluesky delete {'OK' if ok else 'FAILED'} for {bluesky_uri}")
        except Exception as e:
            bsky_msg = f"Bluesky fout: {e}<br>"
            logging.error(f"Bluesky delete failed for {post_id}: {e}")
    else:
        bsky_msg = "Geen Bluesky post gevonden (overgeslagen).<br>"
        logging.info(f"No bluesky_uri for post {post_id} — skipping Bluesky delete")

    try:
        delete_post(post_id)
        logging.info(f"Deleted WordPress post {post_id}")
        return _html_response(
            "Artikel verwijderd",
            f"<b>{post_title}</b> is verwijderd.<br><br>"
            f"{bsky_msg}",
        )

    except Exception as e:
        logging.error(f"WordPress delete failed for {post_id}: {e}")
        return _html_response(
            "Verwijderen mislukt",
            f"{bsky_msg}"
            f"WordPress-fout: {str(e)}<br>Verwijder handmatig via WordPress admin.",
            error=True,
        )


@app.route("/new-image/<token>")
def new_image(token: str):
    """Handles New Image button click.
    Pre:  token is a URL-safe string from the email button
    Post: new image generated via FAL.ai and set as featured image on WordPress post;
          token is NOT marked used so it can be clicked multiple times within 4 hours
    """
    entry = get_token(token)
    if not entry or entry["action"] != "new_image":
        logging.warning(f"Invalid/expired new_image token: {token[:8]}…")
        return _html_response(
            "Link ongeldig of verlopen",
            "Deze Nieuwe-afbeelding-link is niet meer geldig (max 4 uur na publicatie).",
            error=True,
        )

    post_id      = entry["post_id"]
    post_title   = entry["post_title"]
    meta         = entry.get("meta", {})
    article_text = meta.get("article_text", post_title)

    logging.info(f"New image request for post {post_id}: {post_title}")
    # Token intentionally NOT marked used — stays clickable until expiry

    def _do_new_image():
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
                return

            new_image_url = update_featured_image(
                post_id, new_image_path, alt_text=post_title
            )
            logging.info(f"New image set for post {post_id}: {new_image_url}")

        except Exception as e:
            logging.error(f"New image failed for {post_id}: {e}")

    threading.Thread(target=_do_new_image, daemon=True).start()

    return _html_response(
        "Nieuwe afbeelding wordt gegenereerd",
        f"Een nieuwe afbeelding wordt op de achtergrond aangemaakt voor <b>{post_title}</b>.<br><br>"
        f"De afbeelding verschijnt binnen enkele minuten op de website.",
    )


@app.route("/submit", methods=["POST"])
def submit():
    """Dashboard endpoint: submit a URL for article generation.
    Pre:  JSON body with {"url": "https://..."}
    Post: returns {"job_id": "..."} immediately; processing runs in background
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return jsonify({"error": "Ongeldige URL"}), 400

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "pending"}
    logging.info(f"Dashboard submit job {job_id}: {url}")

    def _run():
        try:
            from adhoc_processor import process_single_url
            result = process_single_url(url)
            if result and result.get("wp_url"):
                _jobs[job_id] = {"status": "done", "result": result}
                logging.info(f"Job {job_id} done: {result['wp_url']}")
            else:
                _jobs[job_id] = {"status": "error", "result": {"error": "Verwerking mislukt — controleer de logs."}}
                logging.error(f"Job {job_id} failed for {url}")
        except Exception as e:
            _jobs[job_id] = {"status": "error", "result": {"error": str(e)}}
            logging.error(f"Job {job_id} exception: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/status/<job_id>")
def job_status(job_id: str):
    """Returns current status of a submit job.
    Pre:  job_id from a prior /submit call
    Post: {"status": "pending"/"done"/"error", "result": {...}}
    """
    if job_id not in _jobs:
        return jsonify({"error": "Onbekend job ID"}), 404
    return jsonify(_jobs[job_id])


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
