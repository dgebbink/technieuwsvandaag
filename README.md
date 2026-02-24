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

Voor bronnen met een niet-standaard RSS-pad kun je de feed-URL direct opgeven met een pipe:

```
tweakers.net|https://tweakers.net/feeds/mixed.xml
dutchcowboys.nl|https://www.dutchcowboys.nl/sitemap/news.xml
```

---

## Nieuwe .env variabelen

| Variabele | Omschrijving | Standaard |
|---|---|---|
| `IMAGE_STRATEGY` | `generate` (FAL.ai) of `scrape` (og:image van bron) | `generate` |
| `FAL_API_KEY` | API-sleutel van fal.ai (vereist bij `generate`) | — |
| `ENABLE_SOCIAL_POSTING` | `true` om automatisch naar Bluesky te posten | `false` |
| `BLUESKY_HANDLE` | Je Bluesky handle (bijv. `technieuwsvandaag.bsky.social`) | — |
| `BLUESKY_APP_PASSWORD` | Bluesky App Password (Instellingen → App Passwords) | — |

---

## Troubleshooting

| Probleem | Oplossing |
|---|---|
| `401 Unauthorized` van WordPress | Controleer `WP_APP_PASSWORD` — gebruik Application Password |
| Geen artikelen gevonden | Controleer internetverbinding, probeer `--test-source` |
| SMTP-authenticatie mislukt | Gmail vereist een App Password (2FA aan) |
| `ANTHROPIC_API_KEY` fout | Controleer sleutel op console.anthropic.com |
| Meta-veld waarschuwing | Voeg `register_post_meta` toe aan WordPress (zie hierboven) |
| FAL.ai timeout | FAL.ai kan 60-90 sec nodig hebben; verhoog timeout in `image_generator.py` |
| `atproto` niet gevonden | Voer `pip install atproto` uit |

---

## WordPress — Professionele Uitstraling

### Theme & Typografie

**Aanbevolen theme:** Kadence (gratis versie volstaat — snel, FSE-ready, nieuwssite-vriendelijk)
- Installeren via: WordPress-admin → Weergave → Thema's → Nieuw toevoegen → zoek "Kadence"
- Alternatief: GeneratePress (lichtgewicht, uitstekende PageSpeed) of Newspack (Google-backed, nieuwssites)

**Font-stack:**
- Koppen: `Inter` of `DM Sans` via Google Fonts (of systeemfont-stack voor snelheid: `system-ui, -apple-system, sans-serif`)
- Bodytekst: 17-18px, regelafstand 1.7
- In Kadence: Weergave → Aanpassen → Typografie

**Witruimte en leesbaarheid:**
- Max. inhoudskolombreedte: 720px (instellen in Kadence → Layout → Content Width)
- Alinea-marge onderaan: 1.4em
- Plugin: **Easy Google Fonts** voor directe fontcontrole zonder child-theme

### Structuur

**Sticky header met zoekveld:**
- Kadence Blocks → Header Builder → schakel "Sticky Header" in
- Voeg een zoekwidget toe via Weergave → Widgets → Header-zijbalk

**Breadcrumbs:**
- Installeer **Yoast SEO** of **RankMath** — beide genereren automatisch breadcrumbs
- Activeren via: Yoast → Weergave → Broodkruimels → Inschakelen
- Toevoegen aan single.php van je child-theme: `<?php yoast_breadcrumb('<nav class="breadcrumbs">','</nav>'); ?>`

**Related articles:**
- Plugin: **Related Posts for WordPress** (gratis) of **Yet Another Related Posts Plugin (YARPP)**
- Weergeven onderaan elk artikel via de plugin-instellingen

**Reading time indicator:**
- Plugin: **Reading Time WP** — voegt leestijd toe aan de byline
- Of via Kadence Blocks: voeg een shortcode-blok toe met `[rt_reading_time]`

### Visueel

**Consistente aspect ratio 16:9 voor featured images:**
Voeg toe aan `functions.php` van je child-theme:
```php
add_image_size('featured-16x9', 1200, 675, true); // hard crop
```
Gebruik **Regenerate Thumbnails** plugin na toevoeging om bestaande afbeeldingen bij te werken.

**Placeholder afbeelding als fallback:**
Sla een neutrale placeholder op als `assets/placeholder.jpg` in dit repository.
Upload naar WordPress Mediabibliotheek en stel in als standaard Featured Image via:
- Plugin: **Default Featured Image** — wijst automatisch een fallback toe als er geen afbeelding is

**Category kleurcodering:**
- Plugin: **Category Colors** of via Kadence Custom CSS:
```css
.cat-ai .entry-category { background: #6366f1; }
.cat-cybersecurity .entry-category { background: #ef4444; }
.cat-hardware .entry-category { background: #f59e0b; }
```

**Favicon:**
Genereer een favicon via FAL.ai (prompt: *"Minimalist tech news logo, blue circuit board pattern, square icon, flat design, no text"*) en implementeer via:
- WordPress-admin → Weergave → Aanpassen → Site-identiteit → Siteicoon
- Aanbevolen formaat: 512×512px PNG

---

## SEO — Technische Checklist

### Plugin installatie

1. **Yoast SEO** (of RankMath als alternatief) installeren via Plugins → Nieuw toevoegen
   - Yoast: voltooi de Configuratiewizard voor basisconfiguratie
   - RankMath: gebruik de Setup Wizard en schakel "NewsArticle schema" in

### Sitemap & Search Console

2. **XML Sitemap** activeren:
   - Yoast: automatisch actief na installatie → controleer via `technieuwsvandaag.nl/sitemap_index.xml`
   - Indienen bij Google: Search Console → Sitemaps → URL invoeren

3. **robots.txt** controleren:
   - Verifieer dat `wp-json` NIET geblokkeerd is (nodig voor REST API)
   - Controleer via: `technieuwsvandaag.nl/robots.txt`
   - `Disallow: /wp-json/` moet NIET aanwezig zijn

### Custom fields registreren

4. Voeg toe aan `functions.php` van je child-theme voor volledige meta-ondersteuning:
```php
add_action('init', function () {
    $fields = ['bron_url', 'bron_image_url', 'schema_article_type'];
    foreach ($fields as $field) {
        register_post_meta('post', $field, [
            'show_in_rest' => true,
            'single'       => true,
            'type'         => 'string',
        ]);
    }
});
```

### Core Web Vitals & Caching

5. **Caching plugin** installeren:
   - **WP Rocket** (betaald, aanbevolen voor PageSpeed) of **W3 Total Cache** (gratis)
   - Minimaal instellen: paginacaching, CSS/JS minificatie, lazy load afbeeldingen

6. **Lazy loading afbeeldingen:**
   - Kadence heeft dit ingebouwd; anders via plugin **a3 Lazy Load**

### Structured Data

7. **NewsArticle schema** is al ingebouwd in het script (`schema_article_type = NewsArticle` in meta)
   - Yoast Premium of RankMath Pro activeren voor automatische schema-injectie
   - Valideren via: Google Rich Results Test

### Interne Linking

8. **Related Posts plugin** (zie boven) zorgt voor automatische interne links
   - Stel in op minimaal 3 gerelateerde artikelen per post
   - Plugin: **Link Whisper** (betaald) voor slimme interne linksugesties op basis van AI

---

## Traffic & Groei Strategie

### Organisch (SEO)

**Long-tail keyword aanpak:**
- Richt je op Nederlandse zoekopdrachten die groot nieuws vertalen naar lokale context:
  bijv. *"OpenAI nieuw model nederland"*, *"AI wet europa impact bedrijven"*
- Gebruik Google Search Console (na 3 maanden) om te zien welke zoekopdrachten impressies genereren
- Voeg maandelijks 1-2 "evergreen" artikelen toe naast de dagelijkse nieuws-samenvattingen
  (bijv. "Wat is een LLM?" of "GPU's uitgelegd voor beginners")

**Contentfrequentie:**
- 2 artikelen per dag (via dit script) is een goede basis
- Google Discover beloont consistentie: mis liever geen dag dan meer posten

**Interne linkstrategie:**
- Gebruik de Related Posts plugin om automatisch te linken
- Schrijf maandelijks een "week in review" artikel dat linkt naar alle recente posts

**Realistische SEO-tijdlijn:**
| Periode | Verwachting |
|---|---|
| 0-3 maanden | Indexering, nauwelijks organisch verkeer |
| 3-6 maanden | Eerste posities op long-tail queries, 50-200 bezoekers/dag |
| 6-12 maanden | 200-1000 bezoekers/dag als contentfrequentie consistent is |
| 12+ maanden | Kans op Google News / Discover opname |

### Social Media Automatisering (Bluesky)

Het script bevat een ingebouwde Bluesky-module (`social_poster.py`).

**Activeren:**
```bash
# .env
ENABLE_SOCIAL_POSTING=true
BLUESKY_HANDLE=technieuwsvandaag.bsky.social
BLUESKY_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
```

App Password aanmaken: Bluesky → Instellingen → Privacy & Security → App Passwords

**Post-formaat:** titel + eerste 2 zinnen samenvatting + URL + 3 hashtags (automatisch, max 300 tekens)

**Uitbreidingsmogelijkheden:**
- LinkedIn via hun API (vereist Company Page)
- Twitter/X via API v2 (betaald)
- Mastodon via standaard ActivityPub API (gratis)

### Nieuwsbrief

**Aanbevolen tool:** MailPoet (gratis t/m 1.000 abonnees, WordPress-plugin)
- Installeren via: Plugins → MailPoet
- Stel een automatische "Weekly Digest" in: elke vrijdag de 10 meest gelezen artikelen van die week
- Alternatief: **Brevo** (voorheen Sendinblue) — gratis tot 300 mails/dag, meer geavanceerd

**Instellen:**
1. Maak een nieuwsbrief-formulier aan en plaats het in de sidebar of als pop-up
2. Maak een "Latest Posts" e-mail template aan in MailPoet
3. Plan als wekelijkse automatisering

### Google Discover & Google News

**Google Discover:**
- Vereisten: mobielvriendelijke site, hoge PageSpeed score (≥70), HTTPS, originele content
- Afbeeldingen minimaal 1200px breed (dit script genereert 16:9 via FAL.ai)
- Geen harde garantie — Google bepaalt zelf wat in Discover verschijnt

**Google News (Publisher Center):**
- Aanmelden via: news.google.com/publisher-center
- Vereisten:
  - Duidelijke auteurspagina of redactiepagina (maak een "Redactie"-gebruiker aan in WordPress)
  - Colofon/Over ons pagina met contactinformatie
  - Consistent publicatieschema (minimaal 3×/week)
  - NewsArticle schema markup (ingebouwd in dit script)
- Technische aanpassingen in WordPress:
  - Yoast News SEO plugin (betaald, ~€89/jaar) of RankMath Pro voor Google News sitemap
  - Zorg dat artikelen binnen 48 uur gepubliceerd worden na het origineel

---
