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
        "Middle Eastern": 1,
        "white": 1,
        "mixed-race": 1,
    },
    "age_bucket": {
        "18-22": 1,
        "23-26": 1,
        "27-30": 1,
    },
    "scene_population": {
        "group": 60,
        "solo": 40,
    },
}
# Onafhankelijke random toggle (geen convergentie-tracking): kans dat de
# ethniciteit expliciet in de solo person-instructie wordt benoemd.
IMAGE_MENTION_ETHNICITY_PROBABILITY: float = 0.30
FAL_API_KEY: str = os.environ.get("FAL_API_KEY", "")
# Admin-scoped FAL.ai key voor de billing- en usage-endpoints (api.fal.ai/v1/...).
# De gewone FAL_API_KEY (API-scope) mag deze endpoints niet (403). Aanmaken via
# https://fal.ai/dashboard/keys met scope "ADMIN". Leeg = tegoed/kosten onbekend.
FAL_ADMIN_API_KEY: str = os.environ.get("FAL_ADMIN_API_KEY", "")
# Waarschuwingsdrempel voor FAL.ai tegoed (in dollars); 0 schakelt de check uit
FAL_CREDIT_THRESHOLD: float = float(os.environ.get("FAL_CREDIT_THRESHOLD", "2.0"))

# Social media
ENABLE_SOCIAL_POSTING: bool = os.environ.get("ENABLE_SOCIAL_POSTING", "false").lower() == "true"
BLUESKY_HANDLE: str = os.environ.get("BLUESKY_HANDLE", "")
BLUESKY_APP_PASSWORD: str = os.environ.get("BLUESKY_APP_PASSWORD", "")
BLUESKY_POST_DELAY_SECONDS: int = int(os.environ.get("BLUESKY_POST_DELAY_SECONDS", "60"))

# Instagram (Meta Graph API) — losse toggle naast Bluesky; zie INSTAGRAM_PLAN.md.
# INSTAGRAM_ACCESS_TOKEN moet een never-expiring Page token zijn
# (genereren met instagram_token.py).
ENABLE_INSTAGRAM_POSTING: bool = os.environ.get("ENABLE_INSTAGRAM_POSTING", "false").lower() == "true"
INSTAGRAM_ACCOUNT_ID: str = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
INSTAGRAM_ACCESS_TOKEN: str = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_API_VERSION: str = os.environ.get("INSTAGRAM_API_VERSION", "v19.0")
# Extra wachttijd ná de Bluesky-post voordat het artikel naar Instagram gaat
INSTAGRAM_POST_DELAY_SECONDS: int = int(os.environ.get("INSTAGRAM_POST_DELAY_SECONDS", "120"))
FACEBOOK_APP_ID: str = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET: str = os.environ.get("FACEBOOK_APP_SECRET", "")
