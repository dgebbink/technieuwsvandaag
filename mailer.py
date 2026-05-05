"""
Notificatiemail: bouwt een HTML-mail en verstuurt deze via SMTP.
Fallback: slaat de mail op als bestand en print naar stdout.
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from ai_processor import ProcessedArticle
from approval_store import create_tokens
from config import (
    LOGS_DIR,
    NOTIFICATION_EMAIL,
    SMTP_DISPLAY_NAME,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Actie knoppen
# ---------------------------------------------------------------------------

def build_action_buttons(
    post_id:    int,
    post_title: str,
    wp_url:     str,
    meta:       dict | None = None,
) -> tuple[str, str, str]:
    """Builds Decline and New Image HTML buttons for the notification email.
    Pre:  post_id is a valid WordPress post ID
    Post: returns (buttons_html, decline_token, new_image_token)
          meta dict stored in tokens for reimage use (e.g. article_text)
    """
    import os
    base = os.getenv("APPROVAL_BASE_URL", "http://localhost:5055")
    decline_token, new_image_token = create_tokens(
        post_id, post_title, wp_url, meta
    )
    new_image_url = f"{base}/new-image/{new_image_token}"
    decline_url   = f"{base}/decline/{decline_token}"

    buttons_html = f"""
    <div style="margin:24px 0;text-align:center">
      <a href="{new_image_url}"
         style="display:inline-block;background:#1a73e8;color:#fff;
                padding:12px 24px;border-radius:4px;font-size:15px;
                font-weight:700;text-decoration:none;margin:4px">
        Nieuwe afbeelding
      </a>
      <a href="{decline_url}"
         style="display:inline-block;background:#dc3545;color:#fff;
                padding:12px 24px;border-radius:4px;font-size:15px;
                font-weight:700;text-decoration:none;margin:4px">
        Decline
      </a>
    </div>
    <p style="font-size:11px;color:#999;text-align:center">
      Decline verwijdert het artikel en de Bluesky post.
      Geldig voor 4 uur na publicatie.
    </p>"""

    return buttons_html, decline_token, new_image_token


# ---------------------------------------------------------------------------
# HTML-mail bouwen
# ---------------------------------------------------------------------------

def _article_section(index: int, data: dict, buttons_html: str | None = None) -> str:
    """Render one article as an HTML email card.
    Pre:  data contains 'article' (ProcessedArticle) and 'post' (dict with preview_url)
          buttons_html: pre-built button HTML (if None, build_action_buttons is called)
    Post: returns a valid HTML string
    """
    article: ProcessedArticle = data["article"]
    post: dict = data["post"]

    words = article.samenvatting.split()
    preview = " ".join(words[:150])
    if len(words) > 150:
        preview += " …"

    image_html = ""
    if post.get("image_url"):
        image_html = (
            f'<img src="{post["image_url"]}" alt="{article.titel}" '
            f'style="width:100%;border-radius:4px;margin-bottom:16px;'
            f'display:block;">'
        )

    if buttons_html is None:
        meta = {
            "article_text": article.samenvatting,
            "categorieen":  article.categorieen,
            "trefwoorden":  article.trefwoorden,
            "source_url":   article.original.url,
            "image_url":    post.get("image_url", ""),
        }
        buttons_html, _, _ = build_action_buttons(
            post["id"], article.titel, post.get("link", post["preview_url"]), meta
        )

    post_link = post.get("link", post["preview_url"])

    return f"""
    <div style="border:1px solid #e0e0e0; border-radius:8px; padding:24px;
                margin:20px 0; background:#ffffff;">
      <h2 style="color:#1a73e8; margin:0 0 16px;">Artikel {index}</h2>

      <p style="margin:0 0 12px;"><strong>Titel:</strong> {article.titel}</p>

      {image_html}

      <h3 style="color:#333; margin:0 0 8px; font-size:15px;">Samenvatting preview</h3>
      <p style="color:#555; line-height:1.7; margin:0 0 16px;">{preview}</p>

      <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr>
          <td style="padding:6px 0; color:#888; width:110px;">Categorieen</td>
          <td style="padding:6px 0;">{", ".join(article.categorieen)}</td>
        </tr>
        <tr>
          <td style="padding:6px 0; color:#888;">Trefwoorden</td>
          <td style="padding:6px 0;">{article.trefwoorden}</td>
        </tr>
        <tr>
          <td style="padding:6px 0; color:#888;">Bron</td>
          <td style="padding:6px 0;">
            <a href="{article.original.url}" style="color:#1a73e8; word-break:break-all;">
              {article.original.url}
            </a>
          </td>
        </tr>
      </table>

      <p style="margin:16px 0 4px;">
        <a href="{post_link}"
           style="color:#1a73e8;font-size:13px;">
          Bekijk artikel →
        </a>
      </p>
      {buttons_html}
    </div>
    """


def build_html_email(
    articles_data: list[dict],
    date_str: str,
    warning_message: str = "",
) -> str:
    """Assemble the full HTML email body from article sections."""
    # post: always returns a complete HTML document string
    sections = "".join(
        _article_section(i, data, data.get("buttons_html"))
        for i, data in enumerate(articles_data, start=1)
    )

    warning_html = ""
    if warning_message:
        warning_html = f"""
        <div style="background:#fff3cd; border:1px solid #ffc107; padding:14px 18px;
                    border-radius:4px; margin:20px 0; font-size:14px;">
          <strong>Waarschuwing:</strong> {warning_message}
        </div>
        """

    count_label = f"{len(articles_data)} nieuw(e) artikel(en)" if articles_data else "Geen nieuwe artikelen"

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TechNieuwsVandaag — {date_str}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; max-width:780px; margin:0 auto;
             padding:20px; background:#f0f2f5;">

  <div style="background:#1a73e8; color:#ffffff; padding:24px 28px;
              border-radius:8px 8px 0 0;">
    <h1 style="margin:0; font-size:22px;">TechNieuwsVandaag</h1>
    <p style="margin:6px 0 0; opacity:0.85;">{count_label} — {date_str}</p>
  </div>

  <div style="background:#ffffff; padding:24px 28px; border-radius:0 0 8px 8px;
              box-shadow:0 1px 3px rgba(0,0,0,.08);">
    {warning_html}
    {sections if sections else
     '<p style="color:#888;">Er zijn geen artikelen verwerkt in deze run.</p>'}
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:28px 0 16px;">
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0 0 8px;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot op {date_str}
    </p>
    <p style="text-align:center; margin:0;">
      <a href="{os.getenv('APPROVAL_BASE_URL', 'http://localhost:5055')}/analytics"
         style="color:#1a73e8; font-size:12px; text-decoration:none;">📊 Analyse</a>
    </p>
  </div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Fallback: opslaan als bestand
# ---------------------------------------------------------------------------

def _save_email_to_file(subject: str, html_body: str, date_str: str) -> None:
    """Save email HTML to a fallback file in LOGS_DIR."""
    # post: file written to LOGS_DIR/email_fallback_{date_str}.html
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    safe_date = date_str.replace(" ", "_").replace(":", "-").replace("/", "-")
    filepath = LOGS_DIR / f"email_fallback_{safe_date}.html"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"<!-- Subject: {subject} -->\n\n{html_body}")
    logger.info("Mail-fallback opgeslagen als: %s", filepath)


# ---------------------------------------------------------------------------
# Reimage-notificatiemail
# ---------------------------------------------------------------------------

def send_reimage_email(
    post_id:    int,
    post_title: str,
    wp_url:     str,
    image_url:  str,
    meta:       dict,
) -> None:
    """Send a notification email with a freshly generated image and new action buttons.
    Pre:  post_id is a valid WP post ID
    Post: email sent (or saved to LOGS_DIR as fallback)
          fresh decline/new_image tokens created and embedded in buttons
    """
    date_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    subject  = (
        f"🖼 [TechNieuwsVandaag] Nieuwe afbeelding klaar: "
        f"{post_title[:50]} — {date_str}"
    )

    img_html = (
        f'<img src="{image_url}" alt="{post_title}" '
        f'style="width:100%;border-radius:4px;margin-bottom:16px;display:block;">'
        if image_url else
        '<p style="color:#888;font-size:13px;">Afbeelding kon niet worden gegenereerd.</p>'
    )

    updated_meta = {**meta, "image_url": image_url}
    buttons_html, _, _ = build_action_buttons(post_id, post_title, wp_url, updated_meta)

    preview_url_html = (
        f'<p style="margin:12px 0 4px;">'
        f'<a href="{wp_url}" style="color:#1a73e8;font-size:13px;">'
        f'Bekijk artikel →</a></p>'
    )

    categorieen = ", ".join(meta.get("categorieen", []))
    trefwoorden = meta.get("trefwoorden", "")
    source_url  = meta.get("source_url", "")

    html_body = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Nieuwe afbeelding — {post_title}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; max-width:780px; margin:0 auto;
             padding:20px; background:#f0f2f5;">

  <div style="background:#e67e22; color:#ffffff; padding:24px 28px;
              border-radius:8px 8px 0 0;">
    <h1 style="margin:0; font-size:22px;">🖼 Nieuwe afbeelding gegenereerd</h1>
    <p style="margin:6px 0 0; opacity:0.85;">{post_title} — {date_str}</p>
  </div>

  <div style="background:#ffffff; padding:24px 28px; border-radius:0 0 8px 8px;
              box-shadow:0 1px 3px rgba(0,0,0,.08);">

    <div style="border:1px solid #e0e0e0; border-radius:8px; padding:24px; margin:20px 0;">
      <h2 style="color:#1a73e8; margin:0 0 16px;">{post_title}</h2>

      {img_html}

      <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr>
          <td style="padding:6px 0; color:#888; width:110px;">Categorieën</td>
          <td style="padding:6px 0;">{categorieen}</td>
        </tr>
        <tr>
          <td style="padding:6px 0; color:#888;">Trefwoorden</td>
          <td style="padding:6px 0;">{trefwoorden}</td>
        </tr>
        <tr>
          <td style="padding:6px 0; color:#888;">Bron</td>
          <td style="padding:6px 0;">
            <a href="{source_url}" style="color:#1a73e8; word-break:break-all;">
              {source_url}
            </a>
          </td>
        </tr>
      </table>

      {preview_url_html}
      {buttons_html}
    </div>

    <hr style="border:none; border-top:1px solid #e8e8e8; margin:28px 0 16px;">
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot op {date_str}
    </p>
  </div>

</body>
</html>"""

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
                server.sendmail(SMTP_FROM, [NOTIFICATION_EMAIL], msg.as_string())

            logger.info("Reimage-mail verstuurd naar %s", NOTIFICATION_EMAIL)
            return

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authenticatie mislukt: %s", exc)
        except smtplib.SMTPException as exc:
            logger.error("SMTP-fout: %s", exc)
        except Exception as exc:
            logger.error("Onverwachte mail-fout: %s", exc)
    else:
        logger.warning("SMTP_HOST niet geconfigureerd — fallback naar bestand")

    _save_email_to_file(subject, html_body, date_str)


# ---------------------------------------------------------------------------
# Urgente balans-waarschuwing
# ---------------------------------------------------------------------------

def send_balance_warning() -> None:
    """Send an urgent email warning that the Anthropic credit balance is depleted."""
    # pre: SMTP_HOST and NOTIFICATION_EMAIL are configured
    # post: falls back to file save if SMTP fails
    date_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    subject = f"⚠ [TechNieuwsVandaag] Anthropic tegoed op — actie vereist! ({date_str})"

    html_body = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <title>Anthropic tegoed op</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; max-width:600px; margin:0 auto;
             padding:20px; background:#f0f2f5;">
  <div style="background:#d32f2f; color:#ffffff; padding:24px 28px;
              border-radius:8px 8px 0 0;">
    <h1 style="margin:0; font-size:22px;">⚠ Anthropic tegoed op</h1>
    <p style="margin:6px 0 0; opacity:0.9;">{date_str}</p>
  </div>
  <div style="background:#ffffff; padding:24px 28px; border-radius:0 0 8px 8px;
              box-shadow:0 1px 3px rgba(0,0,0,.08);">
    <p style="font-size:16px; color:#333;">
      Het Anthropic API-tegoed is <strong>te laag</strong> om nieuwe artikelen te verwerken.
      De TechNieuwsVandaag Bot is gestopt en er zijn <strong>geen artikelen gepubliceerd</strong>.
    </p>
    <p style="color:#555;">
      Herlaad het tegoed via
      <a href="https://console.anthropic.com/settings/billing" style="color:#1a73e8;">
        console.anthropic.com → Plans &amp; Billing
      </a>
      en start de bot daarna handmatig opnieuw als je de gemiste dag wil inhalen:
    </p>
    <pre style="background:#f5f5f5; padding:12px; border-radius:4px; font-size:13px;">
cd /home/dgebbink/projects/technieuwsvandaag
python3 main.py --lookback-days 2</pre>
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:24px 0 16px;">
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot op {date_str}
    </p>
  </div>
</body>
</html>"""

    if SMTP_HOST:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = formataddr((SMTP_DISPLAY_NAME, SMTP_FROM))
            msg["To"] = NOTIFICATION_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [NOTIFICATION_EMAIL], msg.as_string())

            logger.info("Balans-waarschuwingsmail verstuurd naar %s", NOTIFICATION_EMAIL)
            return

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authenticatie mislukt: %s", exc)
        except smtplib.SMTPException as exc:
            logger.error("SMTP-fout: %s", exc)
        except Exception as exc:
            logger.error("Onverwachte mail-fout: %s", exc)
    else:
        logger.warning("SMTP_HOST niet geconfigureerd — fallback naar bestand")

    _save_email_to_file(subject, html_body, date_str)


# ---------------------------------------------------------------------------
# FAL.ai balans-waarschuwing
# ---------------------------------------------------------------------------

def send_fal_balance_warning(balance: float) -> None:
    """Send an urgent email warning that the FAL.ai credit balance is low."""
    # pre: SMTP_HOST and NOTIFICATION_EMAIL are configured
    # post: falls back to file save if SMTP fails
    from config import FAL_CREDIT_THRESHOLD  # noqa: PLC0415 — late import vermijdt circulaire dep.

    date_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    subject = (
        f"⚠ [TechNieuwsVandaag] FAL.ai tegoed laag (${balance:.4f}) — actie vereist! ({date_str})"
    )

    html_body = f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <title>FAL.ai tegoed laag</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; max-width:600px; margin:0 auto;
             padding:20px; background:#f0f2f5;">
  <div style="background:#e65100; color:#ffffff; padding:24px 28px;
              border-radius:8px 8px 0 0;">
    <h1 style="margin:0; font-size:22px;">⚠ FAL.ai tegoed laag</h1>
    <p style="margin:6px 0 0; opacity:0.9;">{date_str}</p>
  </div>
  <div style="background:#ffffff; padding:24px 28px; border-radius:0 0 8px 8px;
              box-shadow:0 1px 3px rgba(0,0,0,.08);">
    <p style="font-size:16px; color:#333;">
      Het FAL.ai tegoed is <strong>${balance:.4f}</strong> — onder de ingestelde drempel van
      <strong>${FAL_CREDIT_THRESHOLD:.2f}</strong>.
      Afbeeldingen worden mogelijk niet gegenereerd.
    </p>
    <p style="color:#555;">
      Herlaad het tegoed via
      <a href="https://fal.ai/dashboard/usage-billing/credits" style="color:#1a73e8;">
        fal.ai → Dashboard → Billing
      </a>
      om de afbeeldingsgeneratie te hervatten.
    </p>
    <p style="color:#555;">
      Als tijdelijke maatregel kun je de afbeeldingsstrategie omzetten naar scrapen:
    </p>
    <pre style="background:#f5f5f5; padding:12px; border-radius:4px; font-size:13px;">
# .env
IMAGE_STRATEGY=scrape</pre>
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:24px 0 16px;">
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot op {date_str}
    </p>
  </div>
</body>
</html>"""

    if SMTP_HOST:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = formataddr((SMTP_DISPLAY_NAME, SMTP_FROM))
            msg["To"] = NOTIFICATION_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [NOTIFICATION_EMAIL], msg.as_string())

            logger.info("FAL.ai balans-waarschuwingsmail verstuurd naar %s", NOTIFICATION_EMAIL)
            return

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authenticatie mislukt: %s", exc)
        except smtplib.SMTPException as exc:
            logger.error("SMTP-fout: %s", exc)
        except Exception as exc:
            logger.error("Onverwachte mail-fout: %s", exc)
    else:
        logger.warning("SMTP_HOST niet geconfigureerd — fallback naar bestand")

    _save_email_to_file(subject, html_body, date_str)


# ---------------------------------------------------------------------------
# Hoofd-functie
# ---------------------------------------------------------------------------

def send_notification(
    articles_data: list[dict],
    warning_message: str = "",
    dry_run: bool = False,
    subject_prefix: str = "",
) -> None:
    """Send HTML notification email; fall back to file on SMTP failure."""
    # pre: NOTIFICATION_EMAIL is set
    # post: email sent or saved to LOGS_DIR as fallback
    date_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    count = len(articles_data)
    prefix = f"{subject_prefix} " if subject_prefix else ""

    if count == 0:
        subject = f"[TechNieuwsVandaag] Geen nieuwe artikelen — {date_str}"
    else:
        subject = f"[TechNieuwsVandaag] {count} artikel(en) gepubliceerd — {date_str}"

    html_body = build_html_email(articles_data, date_str, warning_message)

    if dry_run:
        logger.info("[DRY RUN] E-mail niet verstuurd. Onderwerp: %s", subject)
        print(f"\n{'=' * 60}")
        print(f"[DRY RUN] Mail onderwerp : {subject}")
        print(f"[DRY RUN] Ontvanger     : {NOTIFICATION_EMAIL}")
        print(f"[DRY RUN] Inhoud opgeslagen in logs/")
        print("=" * 60)
        _save_email_to_file(subject, html_body, date_str)
        return

    # Probeer via SMTP
    if SMTP_HOST:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = formataddr((SMTP_DISPLAY_NAME, SMTP_FROM))
            msg["To"] = NOTIFICATION_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [NOTIFICATION_EMAIL], msg.as_string())

            logger.info("Notificatiemail verstuurd naar %s", NOTIFICATION_EMAIL)
            return

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authenticatie mislukt: %s", exc)
        except smtplib.SMTPException as exc:
            logger.error("SMTP-fout: %s", exc)
        except Exception as exc:
            logger.error("Onverwachte mail-fout: %s", exc)
    else:
        logger.warning("SMTP_HOST niet geconfigureerd — fallback naar bestand")

    # Fallback
    print(f"\nMail kon niet verstuurd worden. Onderwerp: {subject}")
    _save_email_to_file(subject, html_body, date_str)


# ---------------------------------------------------------------------------
# Generieke e-mailhelper (voor dagrapport en andere modules)
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, html_body: str) -> None:
    """Send an HTML email to an arbitrary recipient.
    Pre:  SMTP settings configured in .env
    Post: email sent; falls back to file on SMTP failure
    """
    date_str = datetime.now().strftime("%d-%m-%Y %H:%M")

    if SMTP_HOST:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = formataddr((SMTP_DISPLAY_NAME, SMTP_FROM))
            msg["To"]      = to
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [to], msg.as_string())

            logger.info("Mail verstuurd naar %s — %s", to, subject)
            return

        except smtplib.SMTPAuthenticationError as exc:
            logger.error("SMTP authenticatie mislukt: %s", exc)
        except smtplib.SMTPException as exc:
            logger.error("SMTP-fout: %s", exc)
        except Exception as exc:
            logger.error("Onverwachte mail-fout: %s", exc)
    else:
        logger.warning("SMTP_HOST niet geconfigureerd — fallback naar bestand")

    _save_email_to_file(subject, html_body, date_str)


# ---------------------------------------------------------------------------
# HTML-secties voor het dagrapport
# ---------------------------------------------------------------------------

def render_bluesky_section(data: dict) -> str:
    """Renders Bluesky daily activity as HTML email block.
    Pre:  data is output of collect_daily_bluesky_report()
    Post: self-contained HTML div string
    """
    if not data.get("success"):
        return (
            "<div style='background:#fff3cd;padding:12px;"
            "border-radius:4px;margin:16px 0'>"
            "<b>⚠️ Bluesky ophalen mislukt:</b> "
            f"{data.get('error', 'onbekend')}</div>"
        )

    new_f = data["new_followers"]
    if new_f:
        items = "".join(
            f"<li><b>{f['displayName']}</b> (@{f['handle']})</li>"
            for f in new_f
        )
        followers_html = (
            f"<h3 style='color:#0085ff;margin:12px 0 4px'>"
            f"Nieuwe volgers vandaag ({len(new_f)})</h3>"
            f"<ul style='margin:4px 0'>{items}</ul>"
        )
    else:
        followers_html = (
            "<p style='color:#888;margin:4px 0'>Geen nieuwe volgers vandaag.</p>"
        )

    recent_f = data.get("recent_followers", [])
    if recent_f:
        rows = "".join(
            "<tr style='border-bottom:1px solid #dde8ff'>"
            f"<td style='padding:8px 10px;font-weight:700'>{f['displayName']}</td>"
            f"<td style='padding:8px 10px;color:#555;font-size:12px'>@{f['handle']}</td>"
            f"<td style='padding:8px 10px;color:#666;font-size:12px'>"
            f"{f['description'][:80] + '…' if len(f['description']) > 80 else f['description'] or '—'}"
            "</td></tr>"
            for f in recent_f
        )
        recent_html = (
            f"<h3 style='color:#0085ff;margin:16px 0 6px'>"
            f"Nieuwste volgers (laatste {len(recent_f)})</h3>"
            "<table style='width:100%;border-collapse:collapse;font-size:13px;"
            "background:#fff;border-radius:4px;overflow:hidden'>"
            "<tr style='background:#0085ff'>"
            "<th style='text-align:left;padding:6px 10px;color:#fff;font-size:11px;"
            "text-transform:uppercase;letter-spacing:0.6px'>Naam</th>"
            "<th style='text-align:left;padding:6px 10px;color:#fff;font-size:11px;"
            "text-transform:uppercase;letter-spacing:0.6px'>Handle</th>"
            "<th style='text-align:left;padding:6px 10px;color:#fff;font-size:11px;"
            "text-transform:uppercase;letter-spacing:0.6px'>Bio</th></tr>"
            f"{rows}</table>"
        )
    else:
        recent_html = ""

    posts_html = ""
    for p in data["posts"]:
        replies_html = ""
        if p["replies"]:
            reply_items = "".join(
                f"<li><b>{r['displayName']}</b>: {r['text']}</li>"
                for r in p["replies"]
            )
            replies_html = (
                f"<ul style='margin:6px 0 0 16px;color:#333;font-size:13px'>"
                f"{reply_items}</ul>"
            )
        posts_html += (
            "<div style='border-left:3px solid #0085ff;padding:8px 12px;"
            "margin:8px 0;background:#fff'>"
            f"<p style='margin:0 0 4px;font-size:13px'>{p['text']}…</p>"
            "<span style='font-size:11px;color:#888'>"
            f"❤️ {p['likeCount']} &nbsp;"
            f"🔁 {p['repostCount']} &nbsp;"
            f"💬 {p['replyCount']}</span>"
            f"{replies_html}</div>"
        )

    if not posts_html:
        posts_html = "<p style='color:#888'>Geen posts vandaag.</p>"

    handle = data.get("handle", "technieuwsvandaag.bsky.social")
    return (
        "<div style='background:#f0f7ff;padding:16px;border-radius:6px;"
        "margin:20px 0;border:1px solid #cce0ff'>"
        f"<h2 style='margin-top:0;color:#0085ff'>🦋 Bluesky — @{handle}</h2>"
        f"<p><b>Totaal volgers:</b> {data['total_followers']}</p>"
        f"{followers_html}"
        f"{recent_html}"
        "<h3 style='color:#0085ff;margin:16px 0 4px'>Posts vandaag</h3>"
        f"{posts_html}</div>"
    )


def render_funds_section(data: dict) -> str:
    """Renders available API fund balances as HTML block.
    Pre:  data is output of collect_funds_report()
    Post: self-contained HTML div with colour-coded balances;
          red warning shown when balance drops below $1.00
    """
    def row(name: str, d: dict, icon: str, link: str) -> str:
        if not d.get("success") or d.get("available") is None:
            return (
                "<tr style='border-bottom:1px solid #e0e0e0'>"
                f"<td style='padding:10px 8px'>{icon} <b>{name}</b></td>"
                "<td style='padding:10px 8px;color:#999'>"
                f"Niet beschikbaar &mdash; "
                f"<a href='{link}' style='color:#CC0000'>controleer handmatig</a>"
                "</td></tr>"
            )
        amount = d["available"]
        color  = "#28a745" if amount > 5.00 else "#ffc107" if amount > 1.00 else "#dc3545"
        warn   = "&nbsp;⚠️ <b>Tegoed bijna op!</b>" if amount < 1.00 else ""
        return (
            "<tr style='border-bottom:1px solid #e0e0e0'>"
            f"<td style='padding:10px 8px'>{icon} <b>{name}</b></td>"
            f"<td style='padding:10px 8px;color:{color};font-size:16px;font-weight:700'>"
            f"${amount:.2f} USD{warn}</td></tr>"
        )

    anthropic_row = row(
        "Claude (Anthropic)", data["anthropic"], "🤖",
        "https://console.anthropic.com/settings/billing",
    )
    fal_row = row(
        "FAL.ai (beeldgeneratie)", data["fal"], "🎨",
        "https://fal.ai/dashboard/usage-billing/credits",
    )
    return (
        "<div style='background:#f9f9f9;padding:16px;border-radius:6px;"
        "margin:20px 0;border:1px solid #e0e0e0'>"
        "<h2 style='margin-top:0;font-size:16px;text-transform:uppercase;"
        "letter-spacing:0.5px'>💳 Beschikbaar tegoed</h2>"
        "<table style='width:100%;border-collapse:collapse'>"
        "<tr style='background:#1A1A1A'>"
        "<th style='text-align:left;padding:8px;color:#fff;font-size:11px;"
        "text-transform:uppercase;letter-spacing:0.8px;width:50%'>Service</th>"
        "<th style='text-align:left;padding:8px;color:#fff;font-size:11px;"
        "text-transform:uppercase;letter-spacing:0.8px'>Tegoed</th></tr>"
        f"{anthropic_row}{fal_row}"
        "</table></div>"
    )
