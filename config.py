"""
Configuratie: laadt environment variables en definieert gedeelde paden en instellingen.
"""
import os
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Alles in dit project rekent in Amsterdam-tijd, dus pin die vast voordat er
# ook maar één datum wordt berekend. Cron geeft TZ niet door aan zijn jobs —
# die vallen terug op /etc/localtime, en dat staat hier op Etc/UTC. Zonder
# deze pin denkt elk cron-proces dat het twee uur vroeger is: `scheduler.py`
# bouwde om 00:00 het schema van *gisteren* (waardoor de Reel-regel een dag te
# laat in de crontab kwam en `weekly_reel.py` hem dan terecht weigerde), en
# logregels stonden in UTC. Interactief verandert dit niets: daar staat TZ al
# op Europe/Amsterdam.
os.environ["TZ"] = "Europe/Amsterdam"
time.tzset()

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
# Kopie van elk gegenereerd beeld zónder het AI-label. Het label wordt in-place
# op het bestand gebrand, dus zonder deze kopie is het origineel weg — en juist
# dat origineel is nodig voor de bewegende Reel: Veo animeert een ingebrand
# label mee, waardoor het onder de stilstaande overlay gaat wiebelen.
# Bestandsnaam = de basisnaam van de WordPress-afbeelding, zodat weekly_reel het
# terugvindt vanaf de image_url van een post.
UNLABELED_IMAGE_DIR: Path = BASE_DIR / "unlabeled"
# Opruimen na deze termijn; de Reel kijkt maximaal REEL_DAYS terug.
UNLABELED_RETENTION_DAYS: int = int(os.environ.get("UNLABELED_RETENTION_DAYS", "21"))

IMAGE_DISTRIBUTION_FILE: Path = BASE_DIR / "image_distribution.json"
IMAGE_DISTRIBUTION_TARGETS: dict = {
    "gender": {
        "a woman": 85,
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
        "20-22": 1,
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

# Welke dienst de beelden maakt bij IMAGE_STRATEGY=generate: 'fal' (FAL.ai
# flux/dev) of 'nanobanana' (Nano Banana 2 / Gemini 3.1 Flash Image). Wordt bij
# de eerste beeldaanvraag één keer uitgelezen door image_providers.
# get_image_provider(); ontbreekt de key van de gekozen provider, dan volgt een
# ImageProviderError — er is bewust geen terugval op de andere provider.
IMAGE_PROVIDER: str = os.environ.get("IMAGE_PROVIDER", "fal")
# Keten die het overneemt als IMAGE_PROVIDER géén beeld oplevert (quota op,
# time-out, verlopen sessie). Komma-gescheiden en op volgorde geprobeerd; leeg
# = geen terugval. Geldt alleen voor runtime-fouten: een ontbrekende key van de
# primaire provider blijft luid stuklopen, anders draait de site ongemerkt
# maanden op de verkeerde dienst.
# Standaard 'nanobanana,fal': gratis web-Gemini vooraan (zie IMAGE_PROVIDER),
# daarna de betaalde API, en FAL.ai als laatste redmiddel.
IMAGE_FALLBACK_PROVIDER: str = os.environ.get("IMAGE_FALLBACK_PROVIDER", "nanobanana,fal")

# Web-Gemini (gratis, via de browser). Geen API-key maar een Selenium-grid plus
# een Firefox-profiel waarin met de hand is ingelogd op Google — zie
# image_providers/webgemini.py voor waarom dat zo moet.
SELENIUM_GRID_URL: str = os.environ.get("SELENIUM_GRID_URL", "http://192.168.2.44:4444")
WEBGEMINI_SSH_HOST: str = os.environ.get("WEBGEMINI_SSH_HOST", "meterkast")
WEBGEMINI_FIREFOX_CONTAINER: str = os.environ.get("WEBGEMINI_FIREFOX_CONTAINER", "firefox")
WEBGEMINI_SELENIUM_CONTAINER: str = os.environ.get("WEBGEMINI_SELENIUM_CONTAINER", "selenium-firefox")
WEBGEMINI_PROFILE: str = os.environ.get("WEBGEMINI_PROFILE", "h272lyqm.default-release")
# Ruim genomen: genereren duurde 12-24 s, maar de app kan traag laden.
WEBGEMINI_TIMEOUT: int = int(os.environ.get("WEBGEMINI_TIMEOUT", "180"))

FAL_API_KEY: str = os.environ.get("FAL_API_KEY", "")
# Admin-scoped FAL.ai key voor de billing- en usage-endpoints (api.fal.ai/v1/...).
# De gewone FAL_API_KEY (API-scope) mag deze endpoints niet (403). Aanmaken via
# https://fal.ai/dashboard/keys met scope "ADMIN". Leeg = tegoed/kosten onbekend.
FAL_ADMIN_API_KEY: str = os.environ.get("FAL_ADMIN_API_KEY", "")
# Waarschuwingsdrempel voor FAL.ai tegoed (in dollars); 0 schakelt de check uit
FAL_CREDIT_THRESHOLD: float = float(os.environ.get("FAL_CREDIT_THRESHOLD", "2.0"))

# Gemini (Nano Banana 2) — alleen nodig bij IMAGE_PROVIDER=nanobanana.
# Key aanmaken via https://aistudio.google.com/apikey.
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
# Model-id staat in .env zodat een opvolger geen codewijziging vergt; Google
# rouleert deze namen (nano banana → gemini-2.5-flash-image → 3.1).
GEMINI_IMAGE_MODEL: str = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
# Eigen grootboek van Gemini-verbruik. Nodig omdat een Gemini API-key géén
# billing-endpoint heeft (zie budget_monitor.py); zonder dit bestand is er geen
# enkel zicht op de kosten in het dagoverzicht.
GEMINI_USAGE_FILE: Path = BASE_DIR / "gemini_usage.json"
# Beeldformaat bij Gemini: 0.5K, 1K, 2K of 4K (let op de hoofdletter K).
# **1K (1376x768) is bewust de standaard.** Het thema registreert als grootste
# formaat `tnv-hero` op 780x439 en `tnv-card` op 480x270; Instagram krijgt
# 1080x1350. Alles boven ~1400px breed wordt dus door WordPress weggeschaald en
# door niemand bekeken, terwijl 2K wél de helft duurder is ($0.101 tegen
# $0.067) en 4K ruim het dubbele. Verhoog dit alleen als het thema grotere
# afbeeldingsformaten gaat registreren.
GEMINI_IMAGE_SIZE: str = os.environ.get("GEMINI_IMAGE_SIZE", "1K")
# Optioneel maandplafond, puur voor de weergave. Staat dit op 0, dan toont het
# dagoverzicht alleen het verbruik en geen "nog over".
GEMINI_MONTHLY_BUDGET: float = float(os.environ.get("GEMINI_MONTHLY_BUDGET", "0"))

# Vooruitbetaald tegoed. Anders dan een maandplafond loopt dit dóór over
# maandgrenzen heen: het wordt afgetrokken van het totale verbruik sinds we
# begonnen met bijhouden, niet van het verbruik deze maand. 0 = geen tegoed.
GEMINI_PREPAID_CREDIT: float = float(os.environ.get("GEMINI_PREPAID_CREDIT", "0"))
GEMINI_CREDIT_CURRENCY: str = os.environ.get("GEMINI_CREDIT_CURRENCY", "USD").upper()
# Google's gepubliceerde prijslijst is in dollars, dus verbruik wordt in USD
# berekend. Staat het tegoed in euro's, dan is deze koers nodig om de twee te
# kunnen vergelijken — het resultaat is dan een benadering, en het dagoverzicht
# zegt dat er ook bij. Bijwerken als de koers ver wegloopt.
GEMINI_USD_TO_EUR: float = float(os.environ.get("GEMINI_USD_TO_EUR", "0.92"))

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
FACEBOOK_APP_ID: str = os.environ.get("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET: str = os.environ.get("FACEBOOK_APP_SECRET", "")

# Artikelen worden per main.py-run niet meer los gepost, maar opgestapeld in
# deze wachtrij; instagram_digest.py bundelt ze aan het eind van de dag tot
# één (carousel-)post — lage volgers rechtvaardigen geen 5 posts/dag.
INSTAGRAM_QUEUE_FILE: Path = BASE_DIR / "instagram_queue.json"

# Dagelijkse volger-/posttelling voor het dagrapport. De Graph API geeft geen
# volgerslijst en geen follower-insights zonder instagram_manage_insights, dus
# de groei "sinds gisteren" bestaat alleen doordat we hem zelf wegschrijven.
INSTAGRAM_STATS_FILE: Path = BASE_DIR / "instagram_stats.json"

# Publieke image-host voor Instagram-postbeelden (los van WordPress — zie
# INSTAGRAM_PLAN.md fase 5 "bekende bug": WP/Imagick crasht op het XMP-blok
# dat het AI-label triggert). Static nginx-container op meterkast
# (ig-media.gebbink.nl, 192.168.2.56), bereikt via scp over het bestaande
# SSH-alias in ~/.ssh/config.
INSTAGRAM_MEDIA_SSH_HOST: str = os.environ.get("INSTAGRAM_MEDIA_SSH_HOST", "meterkast")
INSTAGRAM_MEDIA_REMOTE_DIR: str = os.environ.get(
    "INSTAGRAM_MEDIA_REMOTE_DIR", "/mnt/data/containers/ig-media/html"
)
INSTAGRAM_MEDIA_BASE_URL: str = os.environ.get(
    "INSTAGRAM_MEDIA_BASE_URL", "https://ig-media.gebbink.nl"
)
# Bestanden ouder dan dit worden opgeruimd bij elke nieuwe upload (Meta haalt
# het beeld maar één keer op, bij het aanmaken van de media container)
INSTAGRAM_MEDIA_RETENTION_DAYS: int = int(os.environ.get("INSTAGRAM_MEDIA_RETENTION_DAYS", "2"))

# Reel-cadans: elke 6 dagen i.p.v. wekelijks, zodat het moment door de week
# rouleert. Bij 7 dagen sta je altijd op dezelfde weekdag vast — en dat was
# uitgerekend zondag, volgens meerdere analyses de zwakste dag voor Reels. Een
# 6-daagse cyclus schuift de weekdag elke keer één terug (zo → za → vr → …) en
# doorloopt de hele week in 42 dagen. Dat is meteen een meting: na een paar
# cycli laat Instagram-inzichten zien welk moment voor dít publiek werkt.
#
# Bewust berekend vanaf een vast ijkpunt i.p.v. een "laatst gepost"-bestand:
# geen state die kan bederven, en een gemiste run zet de cyclus niet uit de pas.
REEL_CYCLE_DAYS: int = int(os.environ.get("REEL_CYCLE_DAYS", "6"))
REEL_CYCLE_EPOCH: date = date.fromisoformat(
    os.environ.get("REEL_CYCLE_EPOCH", "2026-08-02")
)


def is_reel_day(day: date | None = None) -> bool:
    """True als er op `day` (standaard vandaag) een Reel hoort te verschijnen.

    Pre:  REEL_CYCLE_DAYS >= 1
    Post: True op het ijkpunt en elke REEL_CYCLE_DAYS dagen daarna; vóór het
          ijkpunt altijd False
    """
    day = day or date.today()
    if day < REEL_CYCLE_EPOCH:
        return False
    return (day - REEL_CYCLE_EPOCH).days % REEL_CYCLE_DAYS == 0


def next_reel_day(after: date | None = None) -> date:
    """Eerstvolgende Reel-dag op of ná `after` (standaard vandaag)."""
    day = after or date.today()
    if day < REEL_CYCLE_EPOCH:
        return REEL_CYCLE_EPOCH
    rest = (day - REEL_CYCLE_EPOCH).days % REEL_CYCLE_DAYS
    return day if rest == 0 else day + timedelta(days=REEL_CYCLE_DAYS - rest)


# Wekelijkse Reel: muziekspoor. Leeg = stil spoor (Instagram eist een
# audiostream, zie instagram_reel.py). Vul dit alleen met muziek waarvan je de
# rechten aantoonbaar hebt — een commercieel nummer onder een zakelijk account
# wordt door Meta gedempt of verwijderd. Pad relatief aan BASE_DIR of absoluut.
REEL_AUDIO_FILE: str = os.environ.get("REEL_AUDIO_FILE", "Beauty Flow.mp3")

# Bewegende Reel: laat de artikelbeelden animeren via de gratis
# Gemini-webinterface (zie reel_animator.py). De stilstaande versie blijft de
# terugval: mislukt de animatie voor een artikel — quota op, sessie verlopen,
# UI gewijzigd — dan gaat dát artikel als gewone slide mee en draait de Reel
# gewoon door. Zet dit op false om volledig terug te vallen op de oude,
# stilstaande Reel.
REEL_ANIMATE: bool = os.environ.get("REEL_ANIMATE", "true").lower() == "true"
# Lengte van een geanimeerde clip; Veo levert ~10 s, we tonen het begin.
REEL_ANIMATE_SECONDS: float = float(os.environ.get("REEL_ANIMATE_SECONDS", "3.0"))
# Per beeld; animeren duurde in de proef ~65 s, maar Veo kan uitschieten.
REEL_ANIMATE_TIMEOUT: int = int(os.environ.get("REEL_ANIMATE_TIMEOUT", "300"))

# De standaardtrack staat onder CC BY 4.0: commercieel gebruik mag, maar
# naamsvermelding is een licentievoorwaarde. Die staat bewust op één centrale
# plek — de colofonpagina (assets/pagina-colofon.html, sectie "Muziek") — en
# niet in elke Reel-caption. Vervang je de track, pas dan die sectie mee aan.

# Editorial — opiniërend redactiestuk, ma/wo/vr (zie CLAUDE.md). Anders dan
# nieuwsartikelen gaat dit als DRAFT naar WordPress: een stuk met een expliciet
# standpunt hoort niet ongelezen live te gaan. Publiceren gebeurt via de
# Publiceer-knop in de mail (approval_server /publish/<token>).
ENABLE_EDITORIAL: bool = os.environ.get("ENABLE_EDITORIAL", "false").lower() == "true"
EDITORIAL_CATEGORY: str = os.environ.get("EDITORIAL_CATEGORY", "Editorial")
# Aantal recent gepubliceerde artikelen dat als kandidatenlijst wordt meegegeven
EDITORIAL_CANDIDATES: int = int(os.environ.get("EDITORIAL_CANDIDATES", "5"))
# Ruimere TTL dan de 4u van approval_store: een editorial die 's ochtends
# gegenereerd wordt moet 's avonds nog te publiceren zijn.
EDITORIAL_TOKEN_TTL_HOURS: int = int(os.environ.get("EDITORIAL_TOKEN_TTL_HOURS", "48"))
