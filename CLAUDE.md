# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Installeer dependencies
pip install -r requirements.txt

# Volledige uitvoering (scant bronnen, AI-verwerking, WordPress, mail)
python main.py

# Simulatie zonder externe writes
python main.py --dry-run

# Test één bron
python main.py --test-source techcrunch.com --dry-run

# Individuele modules testen in een Python REPL
python -c "from scraper import scrape_all_sources; arts = scrape_all_sources('theverge.com'); print(len(arts))"
```

## Architecture

De pijplijn loopt lineair door vijf stappen, gecoördineerd vanuit `main.py`:

```
scraper.py → ai_processor.py → [download_image] → wordpress_client.py → mailer.py
```

**Dataflow:**
- `scraper.py` produceert `list[Article]` (dataclass met title, url, pub_date, excerpt, image_url)
- `ai_processor.py` consumeert `list[Article]`, vraagt Claude om selectie (JSON array van indices) en per artikel een JSON-blok met titel1/titel2/samenvatting/trefwoorden/categorie; produceert `list[ProcessedArticle]`
- `main.py` download afbeeldingen en zet `processed.image_path` op het locale pad
- `wordpress_client.py` consumeert `list[ProcessedArticle]`, maakt categorieën/tags aan, uploadt afbeelding, maakt draft post; produceert `list[dict]` met `{'article': ProcessedArticle, 'post': {'id', 'preview_url', 'title'}}`
- `mailer.py` consumeert die `list[dict]` en verstuurt een HTML-notificatiemail

**Sleutelbestanden:**
- `config.py` — alle settings uit `.env`, gedeelde paden (`LOGS_DIR`, `POSTED_URLS_FILE`, `SOURCES_FILE`)
- `posted_urls.txt` — één URL per regel, voorkomt dubbele posts
- `sources.txt` — één domein/URL per regel (http-prefix wordt automatisch toegevoegd)

**AI-model:** `claude-sonnet-4-6` (gedefinieerd als `MODEL` in `ai_processor.py`)

**WordPress auth:** HTTP Basic met base64-encoded `WP_USERNAME:WP_APP_PASSWORD`. Custom field `bron_url` vereist `register_post_meta()` in WordPress (zie README).

**Logging:** Elke run schrijft naar `logs/run_YYYY-MM-DD_HH-MM-SS.log` én stdout.
