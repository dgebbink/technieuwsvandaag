# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ Kernregels

1. **`scheduler.py` overschrijft de VOLLEDIGE user-crontab** elke nacht om 00:00
   (`crontab -` met een template in `build_crontab()`). Handmatig toegevoegde
   cron-regels verdwijnen dus binnen 24 uur — nieuwe vaste jobs moeten in de
   template in `scheduler.py`.
2. **De snake-`@reboot`-regel leeft in die template** (`scheduler.py:87`) — het
   snake-project draait alleen doordat dít project z'n crontab genereert. Niet
   verwijderen zonder snake elders te regelen.
3. **Gebruik altijd `venv/bin/python3`** (let op: `venv/`, niet `.venv/` zoals de
   andere projecten) — system Python is pyenv-managed.

## Commands

```bash
# Installeer dependencies
venv/bin/pip install -r requirements.txt

# Volledige uitvoering (scant bronnen, AI-verwerking, WordPress draft, mail)
venv/bin/python3 main.py

# Simulatie zonder externe writes
venv/bin/python3 main.py --dry-run

# Test één bron
venv/bin/python3 main.py --test-source techcrunch.com --dry-run

# Approval server handmatig starten (normaal via supervisord)
venv/bin/python3 approval_server.py

# Individuele modules testen in een Python REPL
venv/bin/python3 -c "from scraper import scrape_all_sources; arts = scrape_all_sources('theverge.com'); print(len(arts))"
```

## Scheduling & services (workstation)

- **Cron (user)**: 00:00 `scheduler.py` regenereert het schema → 5× `main.py` op
  random tijden (07:00–19:00 CET, min. 90 min tussenruimte), 00:00 `log_cleaner.py`,
  18:00 UTC `daily_digest.py` (gecombineerd dagoverzicht: artikelen + Bluesky +
  FAL.ai-tegoed via `bluesky_monitor.py` + `budget_monitor.py`).
- **Approval server**: system-supervisord service `tnv-approval-server`
  (`/etc/supervisor/conf.d/user/tnv-approval-server.conf`); `supervisorctl` vereist
  sudo. `APPROVAL_BASE_URL` staat op een LAN-adres — de knoppen in de mail werken
  alleen op LAN/VPN.
- **Op oracle-web**: cron 06:00+18:00 draait `nginx_stats.py` → JSON-cache voor de
  analytics-pagina.

## Hulpscripts (handmatig)

- `backfill.py` — vult de site geantidateerd met historische artikelen
- `publish_pages.py` — publiceert/updatet WP-pagina's vanuit `assets/`
- `adhoc_processor.py` — één URL → WP-draft (gebruikt door approval-server dashboard)
- `update_bluesky_profile.py` — eenmalig Bluesky-profiel bijwerken

## Legacy / niet actief (niet op vertrouwen)

- `daily_report.py` — vervangen door `daily_digest.py` (docstring noemt een
  19:00-slot dat niet meer bestaat)
- `tnv-telegram-bot.service` — verwijst naar `telegram_bot.py` dat **niet bestaat**;
  nergens geïnstalleerd (workstation-container heeft geen systemd)
- `service_watchdog.sh` / `ssl_watchdog.sh` — staan in geen enkele crontab
  (workstation noch oracle-web); draaien dus niet

## Architecture

### Dagelijkse pijplijn (`main.py`)

Artikelen worden als **draft** aangemaakt en pas gepubliceerd na handmatige goedkeuring via e-mail.

```
scraper.py → ai_processor.py → image_generator.py → wordpress_client.py → mailer.py
                                                                               ↓
                                                              (Accept/Decline/Nieuwe afbeelding knoppen)
                                                                               ↓
                                                              approval_server.py (Flask, port 5055)
                                                                    ↓              ↓
                                                             publish_post()    delete_post()
                                                             social_poster.py
```

**Dataflow:**
- `scraper.py` produceert `list[Article]` (dataclass met title, url, pub_date, excerpt, image_url)
- `ai_processor.py` consumeert `list[Article]`, vraagt Claude om selectie en per artikel JSON met titel/samenvatting/trefwoorden/categorie; produceert `list[ProcessedArticle]`
- `image_generator.py` vraagt Claude om een FAL.ai prompt + brand_domain (JSON), genereert afbeelding via FAL.ai, haalt echt logo op via Google favicon service en composit het over de afbeelding (PIL, bottom-right)
- `wordpress_client.py` maakt categorieën/tags aan, uploadt afbeelding, maakt **draft** post (status: draft); produceert `list[dict]` met `{'article': ProcessedArticle, 'post': {'id', 'preview_url', 'image_url', 'title'}}`
- `mailer.py` verstuurt HTML-notificatiemail met de afbeelding en drie knoppen per artikel

### Approval flow

- `approval_store.py` — JSON token store (`approval_tokens.json`), 24u expiry, replay-beveiliging; `create_tokens()` geeft `(accept_token, decline_token, reimage_token)` terug
- `approval_server.py` — Flask server op `APPROVAL_HOST:APPROVAL_PORT` (standaard `0.0.0.0:5055`):
  - `GET /approve/<token>` — publiceert WP draft direct → retourneert succespagina → post naar Bluesky na `BLUESKY_POST_DELAY_SECONDS` in achtergrondthread
  - `GET /decline/<token>` — verwijdert WP draft permanent (`force=True`)
  - `GET /reimage/<token>` — retourneert bevestigingspagina direct → genereert FAL.ai afbeelding, uploadt naar WP en verstuurt nieuwe mail in achtergrondthread
  - `GET /health` — health check + cleanup verlopen tokens
- Draait als **supervisord** service (`/etc/supervisor/conf.d/user/tnv-approval-server.conf`)

## Sleutelbestanden

| Bestand | Doel |
|---|---|
| `config.py` | Alle settings uit `.env`, gedeelde paden |
| `approval_store.py` | Token store voor Accept/Decline/Reimage |
| `approval_server.py` | Flask approval server (incl. `/submit` dashboard endpoint) |
| `adhoc_processor.py` | Verwerkt één URL direct naar WordPress post (dashboard flow) |
| `approval_tokens.json` | Pending tokens — niet verwijderen |
| `image_distribution.json` | Persistente teller voor beeld-persoonsvariatie; stuurt de verdeling naar `IMAGE_DISTRIBUTION_TARGETS` in `config.py` |
| `posted_urls.txt` | Één URL per regel, voorkomt dubbele posts |
| `sources.txt` | Één domein/URL per regel |

## Afbeeldingsgeneratie (`IMAGE_STRATEGY=generate`)

Claude genereert een JSON met:
- `prompt` — fotorealistische beschrijving zonder logo's/tekst (FAL.ai hallucineerde anders logo's)
- `brand_domain` — bijv. `"nvidia.com"` of `null`

FAL.ai krijgt ook een `negative_prompt` mee: `"logo, text, letters, words, brand name, watermark, ..."`.

Daarna: echt logo ophalen via `https://www.google.com/s2/favicons?domain={brand_domain}&sz=128` (fallback: DuckDuckGo icons), dan PIL-compositing bottom-right met witte pill-achtergrond.

## Server (WordPress)

- **SSH:** `ssh -i ~/.ssh/ssh-key-oracle-web.key ubuntu@141.144.195.65`
- WordPress installatiepad: `/var/www/technieuwsvandaag/wordpress/`
- Actief theme: `tnv-news` (`wp-content/themes/tnv-news/`)
- Theme-bestanden zijn eigendom van `www-data` — gebruik `sudo` om te schrijven
- DB: `wpdbtech` op `localhost`, user `dgebbink`
- WP-CLI beschikbaar: `sudo -u www-data wp ...` (vanuit de WordPress root)
- Thema templates: `index.php` (homepage), `archive.php` (categorie/tag), `template-nieuws.php` (alle nieuws, pagina ID 778)

## WordPress auth

HTTP Basic met base64-encoded `WP_USERNAME:WP_APP_PASSWORD`. Custom field `bron_url` vereist `register_post_meta()` in WordPress (zie README).

## Logging

- Dagelijkse runs: `logs/run_YYYY-MM-DD_HH-MM-SS.log`
- Approval server: `logs/approval_server.log`
