"""
Notificatiemail: bouwt een HTML-mail en verstuurt deze via SMTP.
Fallback: slaat de mail op als bestand en print naar stdout.
"""
import logging
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from ai_processor import ProcessedArticle
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
# HTML-mail bouwen
# ---------------------------------------------------------------------------

def _article_section(index: int, data: dict) -> str:
    """Bouw een HTML-sectie voor één artikel."""
    article: ProcessedArticle = data["article"]
    post: dict = data["post"]

    words = article.samenvatting.split()
    preview = " ".join(words[:150])
    if len(words) > 150:
        preview += " …"

    return f"""
    <div style="border:1px solid #e0e0e0; border-radius:8px; padding:24px;
                margin:20px 0; background:#ffffff;">
      <h2 style="color:#1a73e8; margin:0 0 16px;">Artikel {index}</h2>

      <h3 style="color:#333; margin:0 0 8px; font-size:15px;">Titel suggesties</h3>
      <p style="margin:4px 0;"><strong>Optie 1:</strong> {article.titel1}</p>
      <p style="margin:4px 0 16px;"><strong>Optie 2:</strong> {article.titel2}</p>

      <h3 style="color:#333; margin:0 0 8px; font-size:15px;">Samenvatting preview</h3>
      <p style="color:#555; line-height:1.7; margin:0 0 16px;">{preview}</p>

      <table style="width:100%; border-collapse:collapse; font-size:14px;">
        <tr>
          <td style="padding:6px 0; color:#888; width:110px;">Categorieën</td>
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

      <p style="margin:20px 0 0;">
        <a href="{post['preview_url']}"
           style="background:#1a73e8; color:#ffffff; padding:10px 22px;
                  text-decoration:none; border-radius:4px; display:inline-block;
                  font-weight:bold;">
          Bekijk WordPress Draft →
        </a>
      </p>
    </div>
    """


def build_html_email(
    articles_data: list[dict],
    date_str: str,
    warning_message: str = "",
) -> str:
    """Bouw de volledige HTML-mailbody op."""
    sections = "".join(
        _article_section(i, data) for i, data in enumerate(articles_data, start=1)
    )

    warning_html = ""
    if warning_message:
        warning_html = f"""
        <div style="background:#fff3cd; border:1px solid #ffc107; padding:14px 18px;
                    border-radius:4px; margin:20px 0; font-size:14px;">
          <strong>⚠ Waarschuwing:</strong> {warning_message}
        </div>
        """

    count_label = f"{len(articles_data)} nieuwe draft(s)" if articles_data else "Geen nieuwe drafts"

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
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot op {date_str}
    </p>
  </div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Fallback: opslaan als bestand
# ---------------------------------------------------------------------------

def _save_email_to_file(subject: str, html_body: str, date_str: str) -> None:
    """Sla de mail op als HTML-bestand in de logs-map (fallback)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    safe_date = date_str.replace(" ", "_").replace(":", "-").replace("/", "-")
    filepath = LOGS_DIR / f"email_fallback_{safe_date}.html"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"<!-- Subject: {subject} -->\n\n{html_body}")
    logger.info("Mail-fallback opgeslagen als: %s", filepath)


# ---------------------------------------------------------------------------
# Urgente balans-waarschuwing
# ---------------------------------------------------------------------------

def send_balance_warning() -> None:
    """
    Verstuur een urgente HTML-mail wanneer het Anthropic tegoed op is.
    Valt terug op bestandsopslag als SMTP niet beschikbaar of mislukt.
    """
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
            msg["From"] = formataddr((SMTP_DISPLAY_NAME, SMTP_USERNAME))
            msg["To"] = NOTIFICATION_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, [NOTIFICATION_EMAIL], msg.as_string())

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
# Hoofd-functie
# ---------------------------------------------------------------------------

def send_notification(
    articles_data: list[dict],
    warning_message: str = "",
    dry_run: bool = False,
) -> None:
    """
    Verstuur een HTML-notificatiemail naar het geconfigureerde adres.
    Valt terug op bestandsopslag als SMTP niet beschikbaar of mislukt.
    """
    date_str = datetime.now().strftime("%d-%m-%Y %H:%M")
    count = len(articles_data)

    if count == 0:
        subject = f"⚠ [TechNieuwsVandaag] Geen nieuwe artikelen — {date_str}"
    else:
        subject = f"✅ [TechNieuwsVandaag] {count} nieuwe artikel(en) gepubliceerd — {date_str}"

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
            msg["From"] = formataddr((SMTP_DISPLAY_NAME, SMTP_USERNAME))
            msg["To"] = NOTIFICATION_EMAIL
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, [NOTIFICATION_EMAIL], msg.as_string())

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
