#!/usr/bin/env python3
"""
Telegram bot voor TechNieuwsVandaag adhoc artikel aanvragen.
Verwerkt URLs die als bericht worden gestuurd naar de bot.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    filters,
    ContextTypes,
)
from adhoc_processor import process_single_url

load_dotenv()

BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_UID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "0"))

Path("logs").mkdir(exist_ok=True)

logging.basicConfig(
    format="%(asctime)s — %(levelname)s — %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("logs/telegram_bot.log"),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger(__name__)


async def check_auth(update: Update) -> bool:
    """Controleert of de afzender geautoriseerd is.
    Pre:  update bevat een geldig message object
    Post: True als user ID overeenkomt met ALLOWED_UID
          stuurt afwijzingsbericht bij ongeautoriseerde toegang
    """
    uid = update.effective_user.id
    if uid != ALLOWED_UID:
        log.warning("Ongeautoriseerde toegang van user ID: %s", uid)
        await update.message.reply_text("⛔ Geen toegang. Deze bot is privé.")
        return False
    return True


async def handle_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verwerkt het /start commando.
    Pre:  update is een geldig command bericht
    Post: stuurt welkomstbericht naar geautoriseerde gebruiker
    """
    if not await check_auth(update):
        return
    await update.message.reply_text(
        "👋 TechNieuwsVandaag Bot actief.\n\n"
        "Stuur een artikel-URL en ik maak er een WordPress draft van.\n\n"
        "Commando's:\n"
        "/start — dit bericht\n"
        "/status — check bot status"
    )


async def handle_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verwerkt het /status commando.
    Pre:  update is een geldig command bericht
    Post: stuurt statusbericht met recente log info
    """
    if not await check_auth(update):
        return
    await update.message.reply_text(
        "✅ Bot draait\n"
        f"🤖 Toegestane user: {ALLOWED_UID}\n"
        "📋 Stuur een URL om te verwerken"
    )


async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verwerkt een URL bericht en maakt een WordPress draft.
    Pre:  update.message.text bevat een URL
          process_single_url() is beschikbaar
    Post: WordPress draft aangemaakt en link teruggestuurd
          of foutmelding bij ongeldige/ontoegankelijke URL
    """
    if not await check_auth(update):
        return

    url = update.message.text.strip()

    if not url.startswith(("http://", "https://")):
        await update.message.reply_text(
            "⚠️ Ongeldige URL. Stuur een link die begint met https://"
        )
        return

    processing_msg = await update.message.reply_text(
        f"⏳ Bezig met verwerken...\n{url}"
    )

    log.info("Verwerken URL via Telegram: %s", url)

    try:
        result = process_single_url(url)

        if result and result.get("wp_url"):
            wp_url   = result["wp_url"]
            titel    = result.get("title", "Geen titel")
            draft_id = result.get("post_id", "?")

            await processing_msg.edit_text(
                f"✅ Artikel aangemaakt!\n\n"
                f"📰 *{titel}*\n\n"
                f"🔗 Preview:\n{wp_url}\n\n"
                f"📝 Draft ID: {draft_id}\n"
                f"_(Publiceer via WordPress admin)_",
                parse_mode="Markdown",
            )
            log.info("Succesvol verwerkt: %s → %s", url, wp_url)

        else:
            await processing_msg.edit_text(
                f"❌ Verwerking mislukt\n\n"
                f"URL: {url}\n\n"
                f"Mogelijke oorzaken:\n"
                f"• Pagina niet toegankelijk\n"
                f"• Geen leesbare content gevonden\n"
                f"• AI verwerking mislukt\n\n"
                f"Controleer logs/telegram_bot.log voor details."
            )

    except Exception as e:
        log.error("Fout bij verwerken %s: %s", url, e, exc_info=True)
        await processing_msg.edit_text(
            f"💥 Onverwachte fout:\n{str(e)[:200]}\n\nURL: {url}"
        )


async def handle_non_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Verwerkt berichten die geen URL zijn.
    Pre:  update.message.text bevat geen http URL
    Post: stuurt instructiebericht terug
    """
    if not await check_auth(update):
        return
    await update.message.reply_text(
        "💡 Stuur een artikel-URL (beginnend met https://)\n"
        "Ik maak er dan een WordPress draft van."
    )


def main() -> None:
    """Start de Telegram bot polling loop.
    Pre:  TELEGRAM_BOT_TOKEN en TELEGRAM_ALLOWED_USER_ID in .env
    Post: bot draait en verwerkt berichten totdat gestopt
    """
    if not BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN niet ingesteld in .env")
    if not ALLOWED_UID:
        raise ValueError("TELEGRAM_ALLOWED_USER_ID niet ingesteld in .env")

    log.info("Bot start — toegestane user ID: %s", ALLOWED_UID)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  handle_start))
    app.add_handler(CommandHandler("status", handle_status))

    app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r'https?://\S+'),
        handle_url,
    ))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_non_url,
    ))

    log.info("Bot polling gestart...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
