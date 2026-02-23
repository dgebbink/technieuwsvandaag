# TechNieuwsVandaag — Automatiseringsscript

Dagelijks script dat tech-nieuwsbronnen scant, de 2 meest relevante artikelen selecteert,
Nederlandse samenvattingen genereert via Claude AI, en deze als draft klaarzet op WordPress.

---

## Vereisten

- Python 3.10 of hoger
- Een Anthropic API-sleutel (Claude)
- WordPress met REST API ingeschakeld + Application Password
- SMTP-toegang voor notificatiemails

---

## Installatie

```bash
# Installeer afhankelijkheden
pip install -r requirements.txt

# Kopieer en vul .env in
cp .env .env.local   # optioneel; .env wordt direct ingelezen
nano .env
```

### .env invullen

| Variabele | Omschrijving |
|---|---|
| `ANTHROPIC_API_KEY` | Sleutel van console.anthropic.com |
| `WP_URL` | Basis-URL van de WordPress-site |
| `WP_USERNAME` | WordPress gebruikersnaam of e-mailadres |
| `WP_APP_PASSWORD` | WordPress **Application Password** (Profiel → Application Passwords) |
| `SMTP_HOST` | SMTP-server (bijv. `smtp.gmail.com`) |
| `SMTP_PORT` | Standaard `587` (STARTTLS) |
| `SMTP_USERNAME` | SMTP-gebruikersnaam |
| `SMTP_PASSWORD` | SMTP-wachtwoord of app-wachtwoord |
| `SMTP_FROM` | Afzenderadres |
| `NOTIFICATION_EMAIL` | Ontvanger van de notificatiemail |

> **Belangrijk:** Gebruik een WordPress Application Password, niet het hoofdwachtwoord.
> Aanmaken via: WordPress-admin → Gebruikers → Profiel → Application Passwords.

---

## Gebruik

```bash
# Normale dagelijkse uitvoering
python main.py

# Simuleer alles zonder WordPress-posts of mails te versturen
python main.py --dry-run

# Test slechts één bron
python main.py --test-source techcrunch.com
python main.py --test-source theverge --dry-run
```

Logbestanden worden opgeslagen in `logs/run_YYYY-MM-DD_HH-MM-SS.log`.

---

## Projectstructuur

```
technieuwsvandaag/
├── main.py              # Orchestratie + CLI
├── config.py            # Configuratie (laadt .env)
├── scraper.py           # RSS-scraping, HTML-fallback, afbeeldingen
├── ai_processor.py      # Claude AI: selectie + samenvatting
├── wordpress_client.py  # WordPress REST API: categorieën, media, drafts
├── mailer.py            # SMTP-notificatiemail + fallback
├── sources.txt          # Lijst van te scrapen bronnen
├── posted_urls.txt      # Bijgehouden geposte URLs (auto-aangemaakt)
├── .env                 # Credentials (nooit committen!)
├── requirements.txt
└── logs/                # Logbestanden (auto-aangemaakt)
```

---

## WordPress configuratie

### Custom field `bron_url`

De originele bron-URL wordt als custom field opgeslagen. Voeg dit fragment toe aan
`functions.php` van je thema of een site-plugin:

```php
add_action('init', function () {
    register_post_meta('post', 'bron_url', [
        'show_in_rest' => true,
        'single'       => true,
        'type'         => 'string',
    ]);
});
```

Zonder dit fragment plaatst het script het artikel nog steeds als draft,
maar slaat de bron-URL niet op als meta-veld.

### Categorieën

Categorieën worden automatisch aangemaakt als ze nog niet bestaan.
De volledige lijst staat in `ai_processor.py` onder `CATEGORIES`.

---

## Cron-installatie

Voer het script dagelijks om 06:00 UTC (07:00 CET) uit:

```bash
# Crontab bewerken
crontab -e
```

Voeg de volgende regel toe:

```
0 6 * * * cd /pad/naar/technieuwsvandaag && /usr/bin/python3 main.py >> logs/cron.log 2>&1
```

Controleer het pad naar Python met `which python3`.

---

## Modules afzonderlijk testen

```python
# Scraper testen
from scraper import scrape_all_sources
articles = scrape_all_sources(test_source="techcrunch.com")
print(f"{len(articles)} artikelen gevonden")

# AI-verwerking testen (vereist ANTHROPIC_API_KEY in .env)
from ai_processor import process_articles
processed = process_articles(articles)
print(processed[0].titel1)

# WordPress-verbinding testen
from wordpress_client import WordPressClient
client = WordPressClient()
cat_id = client.get_or_create_category("Technologie")
print(f"Categorie ID: {cat_id}")
```

---

## Bronnen toevoegen

Voeg een URL per regel toe aan `sources.txt`. Het script probeert automatisch
veelgebruikte RSS-paden (`/feed`, `/rss`, `/rss.xml` etc.).

---

## Troubleshooting

| Probleem | Oplossing |
|---|---|
| `401 Unauthorized` van WordPress | Controleer `WP_APP_PASSWORD` — gebruik Application Password |
| Geen artikelen gevonden | Controleer internetverbinding, probeer `--test-source` |
| SMTP-authenticatie mislukt | Gmail vereist een App Password (2FA aan) |
| `ANTHROPIC_API_KEY` fout | Controleer sleutel op console.anthropic.com |
| Meta-veld waarschuwing | Voeg `register_post_meta` toe aan WordPress (zie hierboven) |
