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

# Persistente teller voor de persoonsvariatie in beeldprompts.
# Per run wordt per dimensie de categorie gekozen die het meest achterloopt op
# zijn doelaandeel, zodat de werkelijke verdeling naar deze gewichten convergeert
# (i.p.v. puur toeval, dat bij ~1 beeld/dag flink kan afdwalen).
# Gewichten zijn relatief; alle gelijke gewichten = uniforme verdeling.
IMAGE_DISTRIBUTION_FILE: Path = BASE_DIR / "image_distribution.json"
IMAGE_DISTRIBUTION_TARGETS: dict = {
    "gender": {
        "a woman": 70,
        "a non-binary person": 15,
        "a man": 15,
    },
    "ethnicity": {
        "Black": 1,
        "East Asian": 1,
        "South Asian": 1,
        "Latina": 1,
        "Middle Eastern": 1,
        "white": 1,
        "mixed-race": 1,
    },
    "body_type": {
        "athletic build": 1,
        "curvy build": 1,
        "slender build": 1,
        "average build": 1,
        "tall and lean build": 1,
        "petite build": 1,
    },
    "age_bucket": {
        "18-22": 1,
        "23-26": 1,
        "27-30": 1,
    },
    "hair": {
        "short pixie cut": 1,
        "long curly hair": 1,
        "braided hair": 1,
        "a sleek bob": 1,
        "natural afro hair": 1,
        "wavy shoulder-length hair": 1,
        "hair in an updo": 1,
    },
}
FAL_API_KEY: str = os.environ.get("FAL_API_KEY", "")
# Waarschuwingsdrempel voor FAL.ai tegoed (in dollars); 0 schakelt de check uit
FAL_CREDIT_THRESHOLD: float = float(os.environ.get("FAL_CREDIT_THRESHOLD", "2.0"))

# Social media
ENABLE_SOCIAL_POSTING: bool = os.environ.get("ENABLE_SOCIAL_POSTING", "false").lower() == "true"
BLUESKY_HANDLE: str = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD: str = os.environ.get("BLUESKY_APP_PASSWORD", "")
BLUESKY_POST_DELAY_SECONDS: int = int(os.environ.get("BLUESKY_POST_DELAY_SECONDS", "60"))
