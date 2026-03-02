# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Installeer dependencies
pip install -r requirements.txt

# Volledige uitvoering (scant bronnen, AI-verwerking, WordPress draft, mail)
python main.py

# Simulatie zonder externe writes
python main.py --dry-run

# Test één bron
python main.py --test-source techcrunch.com --dry-run

# Verwerk één URL direct (Telegram/adhoc flow — publiceert direct, bypasses approval)
python -c "from adhoc_processor import process_single_url; print(process_single_url('https://...'))"

# Approval server handmatig starten (normaal via supervisord)
python approval_server.py

# Individuele modules testen in een Python REPL
python -c "from scraper import scrape_all_sources; arts = scrape_all_sources('theverge.com'); print(len(arts))"
```

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
  - `GET /approve/<token>` — publiceert WP draft → wacht `BLUESKY_POST_DELAY_SECONDS` → post naar Bluesky
  - `GET /decline/<token>` — verwijdert WP draft permanent (`force=True`)
  - `GET /reimage/<token>` — genereert nieuwe FAL.ai afbeelding → uploadt naar WP → verstuurt nieuwe mail met nieuwe tokens
  - `GET /health` — health check + cleanup verlopen tokens
- Draait als **supervisord** service (`/etc/supervisor/conf.d/user/tnv-approval-server.conf`)

### Telegram/adhoc flow (`telegram_bot.py` + `adhoc_processor.py`)

Bypasses de approval flow — publiceert direct:

```
telegram_bot.py → adhoc_processor.process_single_url()
                      → ai_processor.process_article()
                      → image_generator (FAL.ai + logo compositing)
                      → wordpress_client.publish_articles()  # maakt draft
                      → wordpress_client.publish_post()      # publiceert direct
                      → social_poster.post_to_bluesky()
```

## Sleutelbestanden

| Bestand | Doel |
|---|---|
| `config.py` | Alle settings uit `.env`, gedeelde paden |
| `approval_store.py` | Token store voor Accept/Decline/Reimage |
| `approval_server.py` | Flask approval server |
| `approval_tokens.json` | Pending tokens — niet verwijderen |
| `posted_urls.txt` | Één URL per regel, voorkomt dubbele posts |
| `sources.txt` | Één domein/URL per regel |

## Afbeeldingsgeneratie (`IMAGE_STRATEGY=generate`)

Claude genereert een JSON met:
- `prompt` — fotorealistische beschrijving zonder logo's/tekst (FAL.ai hallucineerde anders logo's)
- `brand_domain` — bijv. `"nvidia.com"` of `null`

FAL.ai krijgt ook een `negative_prompt` mee: `"logo, text, letters, words, brand name, watermark, ..."`.

Daarna: echt logo ophalen via `https://www.google.com/s2/favicons?domain={brand_domain}&sz=128` (fallback: DuckDuckGo icons), dan PIL-compositing bottom-right met witte pill-achtergrond.

## WordPress auth

HTTP Basic met base64-encoded `WP_USERNAME:WP_APP_PASSWORD`. Custom field `bron_url` vereist `register_post_meta()` in WordPress (zie README).

## Logging

- Dagelijkse runs: `logs/run_YYYY-MM-DD_HH-MM-SS.log`
- Approval server: `logs/approval_server.log`
- Telegram bot: `logs/telegram_bot.log`
- Adhoc verwerking: `logs/adhoc_YYYY-MM-DD.log`
