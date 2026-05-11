"""
Configuratie: laadt environment variables en definieert gedeelde paden en instellingen.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR: Path = Path(__file__).parent

# WordPress
WP_URL: str = os.environ.get("WP_URL", "https://technieuwsvandaag.nl")
WP_USERNAME: str = os.environ.get("WP_USERNAME", "")
WP_APP_PASSWORD: str = os.environ.get("WP_APP_PASSWORD", "")

# SMTP
SMTP_HOST: str = os.environ.get("SMTP_HOST", "")
SMTP_PORT: int = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME: str = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM: str = os.environ.get("SMTP_FROM", "noreply@technieuwsvandaag.nl")
SMTP_DISPLAY_NAME: str = os.environ.get("SMTP_DISPLAY_NAME", "Redactie TechNieuwsVandaag")
NOTIFICATION_EMAIL: str = os.environ.get("NOTIFICATION_EMAIL", "info@technieuwsvandaag.nl")

# Paden
SOURCES_FILE: Path = BASE_DIR / "sources.txt"
POSTED_URLS_FILE: Path = BASE_DIR / "posted_urls.txt"
LOGS_DIR: Path = BASE_DIR / "logs"

# Scraper
USER_AGENT: str = "TechNieuwsVandaag-Bot/1.0"
REQUEST_TIMEOUT: int = 20

# Maximum aantal artikelen dat naar Claude wordt gestuurd voor selectie
MAX_ARTICLES_FOR_SELECTION: int = 50

# Afbeeldingsstrategie: 'generate' (FAL.ai) of 'scrape' (og:image van bron)
IMAGE_STRATEGY: str = os.environ.get("IMAGE_STRATEGY", "generate")
FAL_API_KEY: str = os.environ.get("FAL_API_KEY", "")
# Waarschuwingsdrempel voor FAL.ai tegoed (in dollars); 0 schakelt de check uit
FAL_CREDIT_THRESHOLD: float = float(os.environ.get("FAL_CREDIT_THRESHOLD", "2.0"))

# Social media
ENABLE_SOCIAL_POSTING: bool = os.environ.get("ENABLE_SOCIAL_POSTING", "false").lower() == "true"
BLUESKY_HANDLE: str = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD: str = os.environ.get("BLUESKY_APP_PASSWORD", "")
BLUESKY_POST_DELAY_SECONDS: int = int(os.environ.get("BLUESKY_POST_DELAY_SECONDS", "60"))
