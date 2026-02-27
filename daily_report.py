#!/usr/bin/env python3
"""
End-of-day report for TechNieuwsVandaag.
Sends Bluesky activity + fund balances to notification email.
Run daily at 19:00 CET via scheduler.py.

Gebruik:
  python3 daily_report.py
"""

import logging
import sys
from datetime import date

from bluesky_monitor import collect_daily_bluesky_report
from budget_monitor import collect_funds_report
from config import NOTIFICATION_EMAIL, LOGS_DIR
from mailer import render_bluesky_section, render_funds_section, send_email

LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "daily_report.log"),
    ],
)
logger = logging.getLogger(__name__)


def build_report_html() -> str:
    """Builds the full daily report HTML email body.
    Pre:  bluesky and budget modules importable
    Post: complete HTML string ready to send
    """
    today        = date.today().strftime("%-d %B %Y")
    bluesky_data = collect_daily_bluesky_report()
    funds_data   = collect_funds_report()
    bluesky_html = render_bluesky_section(bluesky_data)
    funds_html   = render_funds_section(funds_data)

    return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TechNieuwsVandaag — Dagrapport {today}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif; max-width:640px;
             margin:0 auto; padding:20px; background:#f0f2f5;">

  <div style="background:#1A1A1A; padding:20px 24px;
              border-bottom:3px solid #CC0000; border-radius:8px 8px 0 0;">
    <h1 style="color:#fff; margin:0; font-size:20px;">
      📊 TechNieuwsVandaag — Dagrapport {today}
    </h1>
  </div>

  <div style="background:#ffffff; padding:20px 24px;
              border-radius:0 0 8px 8px;
              box-shadow:0 1px 3px rgba(0,0,0,.08);">
    {bluesky_html}
    {funds_html}
    <hr style="border:none; border-top:1px solid #e8e8e8; margin:24px 0 12px;">
    <p style="color:#aaa; font-size:12px; text-align:center; margin:0;">
      Automatisch gegenereerd door TechNieuwsVandaag-Bot
    </p>
  </div>

</body>
</html>"""


def main() -> None:
    """Builds and sends the daily report email.
    Pre:  NOTIFICATION_EMAIL and SMTP settings in .env
    Post: email sent to NOTIFICATION_EMAIL
    """
    today   = date.today().strftime("%-d %B %Y")
    subject = f"📊 [TechNieuwsVandaag] Dagrapport — {today}"

    logger.info("Dagrapport samenstellen voor %s", date.today().isoformat())
    html = build_report_html()

    send_email(to=NOTIFICATION_EMAIL, subject=subject, html_body=html)
    logger.info("Dagrapport verstuurd naar %s", NOTIFICATION_EMAIL)


if __name__ == "__main__":
    main()
