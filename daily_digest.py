#!/usr/bin/env python3
"""
Dagelijks overzicht van alle geposte artikelen.

Haalt alle WordPress posts van vandaag op via de REST API
en verstuurt een HTML-overzichtsmail.

Gebruik:
  python daily_digest.py
  python daily_digest.py --dry-run
"""
import argparse
import base64
import logging
import smtplib
import sys
from datetime import datetime, date, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from bluesky_monitor import collect_daily_bluesky_report
from budget_monitor import collect_funds_report
from config import (
    LOGS_DIR,
    NOTIFICATION_EMAIL,
    SMTP_DISPLAY_NAME,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
    WP_APP_PASSWORD,
    WP_URL,
    WP_USERNAME,
)
from mailer import render_bluesky_section, render_funds_section

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

CET = ZoneInfo("Europe/Amsterdam")


# ---------------------------------------------------------------------------
# WordPress data ophalen
# ---------------------------------------------------------------------------

def _wp_auth_header() -> dict:
    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def fetch_todays_posts() -> list[dict]:
    """Haalt alle WordPress posts van vandaag op (draft + publish).
    Pre:  WP_URL, WP_USERNAME, WP_APP_PASSWORD zijn geconfigureerd
    Post: gesorteerde lijst van post-dicts, nieuwste eerst
    """
    today = date.today()
    after  = f"{today.isoformat()}T00:00:00"
    before = f"{today.isoformat()}T23:59:59"

    headers = _wp_auth_header()
    posts: list[dict] = []

    for status in ("draft", "publish"):
        try:
            resp = requests.get(
                f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts",
                params={
                    "after":      after,
                    "before":     before,
                    "status":     status,
                    "per_page":   50,
                    "orderby":    "date",
                    "order":      "desc",
                    "_fields":    "id,title,link,status,date,categories,tags,meta",
                },
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            posts.extend(resp.json())
        except Exception as exc:
            logger.warning("Posts ophalen mislukt (status=%s): %s", status, exc)

    # Verwijder dubbelen (draft + publish kan overlap hebben)
    seen: set[int] = set()
    unique = []
    for p in sorted(posts, key=lambda x: x.get("date", ""), reverse=True):
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    return unique


def _resolve_terms(ids: list[int], endpoint: str, headers: dict) -> list[str]:
    """Haalt namen op voor categorie- of tag-IDs."""
    names = []
    for tid in ids[:5]:
        try:
            r = requests.get(
                f"{WP_URL.rstrip('/')}/wp-json/wp/v2/{endpoint}/{tid}",
                headers=headers,
                timeout=8,
            )
            r.raise_for_status()
            names.append(r.json().get("name", ""))
        except Exception:
            pass
    return [n for n in names if n]


def fetch_old_drafts() -> list[dict]:
    """Haalt alle ongelezen WordPress drafts op van vóór vandaag.
    Pre:  WP_URL, WP_USERNAME, WP_APP_PASSWORD zijn geconfigureerd
    Post: gesorteerde lijst van draft post-dicts, nieuwste eerst
    """
    today = date.today()
    before = f"{today.isoformat()}T00:00:00"

    headers = _wp_auth_header()
    try:
        resp = requests.get(
            f"{WP_URL.rstrip('/')}/wp-json/wp/v2/posts",
            params={
                "before":   before,
                "status":   "draft",
                "per_page": 50,
                "orderby":  "date",
                "order":    "desc",
                "_fields":  "id,title,link,status,date,categories,tags,meta",
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Oude drafts ophalen mislukt: %s", exc)
        return []


def enrich_posts(posts: list[dict]) -> list[dict]:
    """Voegt categorie- en tagnamen toe aan elke post.
    Pre:  posts is een lijst van WP post-dicts met categories/tags als ID-lijsten
    Post: elk post-dict heeft extra sleutels category_names en tag_names
    """
    headers = _wp_auth_header()
    for post in posts:
        post["category_names"] = _resolve_terms(
            post.get("categories", []), "categories", headers
        )
        post["tag_names"] = _resolve_terms(
            post.get("tags", []), "tags", headers
        )
        # Bron-URL uit custom meta
        meta = post.get("meta", {})
        post["bron_url"] = meta.get("bron_url", "") if isinstance(meta, dict) else ""
    return posts


# ---------------------------------------------------------------------------
# HTML-mail bouwen
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    color = "#2e7d32" if status == "publish" else "#f57c00"
    label = "Gepubliceerd" if status == "publish" else "Concept"
    return (
        f'<span style="background:{color}; color:#fff; font-size:11px; '
        f'font-weight:bold; padding:2px 8px; border-radius:10px; '
        f'vertical-align:middle;">{label}</span>'
    )


def _post_card(index: int, post: dict, show_date: bool = False) -> str:
    title = post["title"].get("rendered", "(geen titel)")
    link  = post.get("link", "#")
    status = post.get("status", "draft")
    dt_raw = post.get("date", "")
    bron   = post.get("bron_url", "")
    cats   = ", ".join(post.get("category_names", [])) or "—"
    tags   = ", ".join(post.get("tag_names", [])) or "—"

    # Datum/tijdstip in CET
    time_str = ""
    if dt_raw:
        try:
            dt_utc = datetime.fromisoformat(dt_raw).replace(tzinfo=timezone.utc)
            dt_cet = dt_utc.astimezone(CET)
            time_str = dt_cet.strftime("%-d %b %Y  %H:%M") if show_date else dt_cet.strftime("%H:%M")
        except Exception:
            time_str = dt_raw[:16]

    bron_row = ""
    if bron:
        bron_domain = bron.split("/")[2] if bron.startswith("http") else bron
        bron_row = f"""
        <tr>
          <td style="padding:5px 0; color:#888; width:110px; vertical-align:top;">Bron</td>
          <td style="padding:5px 0;">
            <a href="{bron}" style="color:#1a73e8;">{bron_domain}</a>
          </td>
        </tr>"""

    return f"""
    <div style="border:1px solid #e0e0e0; border-radius:8px; padding:20px 24px;
                margin:16px 0; background:#ffffff;">
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px;">
        <span style="color:#999; font-size:13px;">#{index} &nbsp;·&nbsp; {time_str} CET</span>
        {_status_badge(status)}
      </div>
      <h2 style="margin:0 0 14px; font-size:17px; color:#1a1a1a; line-height:1.4;">
        <a href="{link}" style="color:#1a73e8; text-decoration:none;">{title}</a>
      </h2>
      <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr>
          <td style="padding:5px 0; color:#888; width:110px;">Categorieën</td>
          <td style="padding:5px 0;">{cats}</td>
        </tr>
        <tr>
          <td style="padding:5px 0; color:#888;">Tags</td>
          <td style="padding:5px 0; color:#555;">{tags}</td>
        </tr>
        {bron_row}
      </table>
      <p style="margin:14px 0 0;">
        <a href="{link}"
           style="background:#1a73e8; color:#fff; padding:8px 18px;
                  text-decoration:none; border-radius:4px; font-size:13px;
                  font-weight:bold; display:inline-block;">
          Bekijk artikel →
        </a>
      </p>
    </div>"""


def build_digest_html(
    today_posts: list[dict],
    old_drafts: list[dict],
    date_str: str,
    bluesky_data: dict | None = None,
    funds_data: dict | None = None,
) -> str:
    """Bouwt de volledige HTML-digest op basis van de post-lijst."""
    today_nl = datetime.now(CET).strftime("%-d %B %Y")

    published = sum(1 for p in today_posts if p.get("status") == "publish")
    concepts  = len(today_posts) - published

    stats = (
        f"{len(today_posts)} artikel{'en' if len(today_posts) != 1 else ''} vandaag"
        f" &nbsp;·&nbsp; {published} gepubliceerd"
        f" &nbsp;·&nbsp; {concepts} concept{'en' if concepts != 1 else ''}"
    )

    today_cards = "".join(_post_card(i, p, show_date=False) for i, p in enumerate(today_posts, 1))
    empty = '<p style="color:#888; padding:20px 0;">Geen artikelen gepost vandaag.</p>'

    old_drafts_section = ""
    if old_drafts:
        old_cards = "".join(_post_card(i, p, show_date=True) for i, p in enumerate(old_drafts, 1))
        old_drafts_section = f"""
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:28px 0 20px;">
    <h2 style="font-size:16px; color:#555; margin:0 0 4px;">
      Openstaande concepten
    </h2>
    <p style="font-size:13px; color:#888; margin:0 0 8px;">
      {len(old_drafts)} concept{'en' if len(old_drafts) != 1 else ''} van vóór vandaag
    </p>
    {old_cards}"""

    report_section = ""
    if bluesky_data is not None or funds_data is not None:
        bluesky_html = render_bluesky_section(bluesky_data) if bluesky_data is not None else ""
        funds_html   = render_funds_section(funds_data)     if funds_data   is not None else ""
        report_section = f"""
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:28px 0 20px;">
    <h2 style="font-size:16px; color:#333; margin:0 0 16px;">
      📊 Dagrapport
    </h2>
    {bluesky_html}
    {funds_html}"""

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TechNieuwsVandaag — Dagelijks overzicht {today_nl}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; max-width:720px; margin:0 auto;
             padding:20px; background:#f0f2f5;">

  <div style="background:#1a73e8; color:#ffffff; padding:24px 28px;
              border-radius:8px 8px 0 0;">
    <h1 style="margin:0; font-size:22px;">TechNieuwsVandaag</h1>
    <p style="margin:6px 0 0; opacity:0.85; font-size:14px;">
      Dagelijks overzicht — {today_nl}
    </p>
  </div>

  <div style="background:#f8f9fa; border-left:4px solid #1a73e8;
              padding:12px 20px; font-size:13px; color:#555;">
    {stats}
  </div>

  <div style="background:#ffffff; padding:24px 28px; border-radius:0 0 8px 8px;
              box-shadow:0 1px 3px rgba(0,0,0,.08);">
    <h2 style="font-size:16px; color:#555; margin:0 0 4px;">Vandaag gepost</h2>
    {today_cards if today_posts else empty}
    {old_drafts_section}
    {report_section}
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:28px 0 16px;">
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot · {date_str}
    </p>
  </div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Versturen
# ---------------------------------------------------------------------------

def send_digest(dry_run: bool = False) -> None:
    """Haalt posts op, bouwt digest en verstuurt de mail.
    Pre:  WordPress en SMTP credentials geconfigureerd in .env
    Post: mail verstuurd of opgeslagen als fallback in logs/
    """
    date_str = datetime.now(CET).strftime("%d-%m-%Y %H:%M")
    today_nl = datetime.now(CET).strftime("%-d %B %Y")
    subject  = f"📋 [TechNieuwsVandaag] Dagoverzicht — {today_nl}"

    logger.info("Dagelijkse digest ophalen voor %s", date.today().isoformat())
    today_posts = fetch_todays_posts()
    logger.info("%d post(s) vandaag gevonden", len(today_posts))

    old_drafts = fetch_old_drafts()
    logger.info("%d openstaand(e) concept(en) van vóór vandaag gevonden", len(old_drafts))

    if today_posts:
        logger.info("Categorieën en tags ophalen voor vandaag...")
        today_posts = enrich_posts(today_posts)

    if old_drafts:
        logger.info("Categorieën en tags ophalen voor oude drafts...")
        old_drafts = enrich_posts(old_drafts)

    logger.info("Bluesky activiteit ophalen...")
    bluesky_data = collect_daily_bluesky_report()

    logger.info("Tegoed ophalen...")
    funds_data = collect_funds_report()

    html_body = build_digest_html(today_posts, old_drafts, date_str, bluesky_data, funds_data)

    if dry_run:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = LOGS_DIR / f"digest_dryrun_{date.today().isoformat()}.html"
        path.write_text(html_body, encoding="utf-8")
        logger.info("[DRY RUN] Digest niet verstuurd — opgeslagen als: %s", path)
        print(f"\n[DRY RUN] Onderwerp : {subject}")
        print(f"[DRY RUN] Ontvanger : {NOTIFICATION_EMAIL}")
        print(f"[DRY RUN] Posts vandaag : {len(today_posts)}")
        print(f"[DRY RUN] Oude drafts   : {len(old_drafts)}")
        return

    if SMTP_HOST:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = formataddr((SMTP_DISPLAY_NAME, SMTP_FROM))
            msg["To"]      = NOTIFICATION_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, [NOTIFICATION_EMAIL], msg.as_string())

            logger.info("Digest verstuurd naar %s (%d posts vandaag, %d oude drafts)",
                        NOTIFICATION_EMAIL, len(today_posts), len(old_drafts))
            return

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authenticatie mislukt: %s", exc)
        except smtplib.SMTPException as exc:
            logger.error("SMTP-fout: %s", exc)
        except Exception as exc:
            logger.error("Onverwachte fout: %s", exc)
    else:
        logger.warning("SMTP_HOST niet geconfigureerd — fallback naar bestand")

    # Fallback
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOGS_DIR / f"digest_fallback_{date.today().isoformat()}.html"
    path.write_text(html_body, encoding="utf-8")
    logger.info("Digest opgeslagen als fallback: %s", path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dagelijks overzicht mailen")
    parser.add_argument("--dry-run", action="store_true",
                        help="Sla op als bestand, verstuur geen mail")
    args = parser.parse_args()
    send_digest(dry_run=args.dry_run)
