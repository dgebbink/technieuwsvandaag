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
4. **Reken cron-tijden nooit om — alles in de crontab staat in Amsterdam-tijd.**
   De daemon plant in die zone (geërfd uit zijn omgeving; `/etc/timezone` zegt
   misleidend `Etc/UTC`), maar geeft zijn `TZ` níét door aan de jobs: die zagen
   UTC. Daardoor vuurde élke job twee uur te vroeg, en bouwde `scheduler.py` om
   00:00 het schema van *gisteren* — met als stil gevolg dat de Reel-regel een
   dag te laat in de crontab kwam en `weekly_reel.py` hem dan terecht weigerde,
   dus de Reel postte helemaal niet meer. Twee eerdere "fixes" (`cet_to_utc()`,
   daarna runtime-detectie met `cet_to_cron_clock()`) maakten het erger: die
   detectie leest de tijdzone van het eigen proces, en dat is juist niet de klok
   van de daemon. Nu zet `build_crontab()` een `TZ=Europe/Amsterdam`-regel
   bovenaan de crontab en pint `config.py` diezelfde zone bij import (`tzset`),
   zodat planning, `date.today()` en logtijden gelijklopen. `CRON_TZ` is geen
   alternatief — cron 3.0pl1 negeert het. Gevonden en gefixt 2026-08-03.

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
  ma/wo/vr 09:00 CET `editorial.py` (opiniërend redactiestuk → WP-draft +
  goedkeuringsmail; zie Architecture),
  elke 6 dagen 11:00 CET `weekly_reel.py` (silent Instagram-Reel-recap — de
  cyclus rouleert de weekdag, zie hieronder), 19:45 CET `instagram_digest.py` (bundelt de dagelijkse Instagram-
  artikelen tot 1 post), 20:00 CET `daily_digest.py` (gecombineerd dagoverzicht:
  artikelen + Bluesky + Instagram + FAL.ai-tegoed via `bluesky_monitor.py` +
  `instagram_monitor.py` + `budget_monitor.py`).
  De Instagram-sectie toont volgers, posts van vandaag met likes/reacties en de
  stand van de dagwachtrij (staat daar om 20:00 nog iets in, dan is de digest van
  19:45 mislukt). **Volgersgroei komt uit `instagram_stats.json`, niet uit de
  API**: de Graph API geeft geen volgerslijst zoals Bluesky, en de insights
  (bereik, profielweergaven, dagelijkse follower-delta) vereisen de permissie
  `instagram_manage_insights` die dit token niet heeft — vandaar een eigen
  dagelijkse telling. De delta is dus pas zichtbaar vanaf de tweede dag, en één
  gemiste run betekent dat de volgende delta twee dagen omvat (het rapport noemt
  daarom de datum van de vorige meting).
- **Instagram-posting is een dagdigest + wekelijkse Reel, geen post per artikel**:
  elke `main.py`-run zet gequeuede artikelen in `instagram_queue.json`
  (`social_poster.queue_instagram_post()`) i.p.v. direct te posten — bij lage
  volgersaantallen zijn 5 losse posts/dag te veel. `instagram_digest.py` bundelt de
  wachtrij 's avonds tot één post (carousel bij 2+ artikelen); details en de bekende
  beperking (gedeclineerd artikel na de digest-post niet meer uit een gepubliceerde
  carousel te halen) staan in `INSTAGRAM_PLAN.md` fase 6. `weekly_reel.py` post
  daarnaast wekelijks een silent 9:16-slideshow (één artikel per dag, rechtstreeks uit
  WordPress — los van de dagwachtrij) — Reels zijn het enige kanaal dat niet-volgers
  bereikt, feedposts amper. Zie `INSTAGRAM_PLAN.md` fase 7.
  **De Reel is stilstaand. `REEL_ANIMATE` staat sinds 2026-08-15 standaard uit**
  (was aan sinds 11 aug). De machinerie om de beelden te laten bewegen bestaat
  nog — `reel_animator.py`, Veo via de gratis Gemini-webinterface — maar het
  gratis account kán geen video meer maken: dat zit achter Google AI Pro/Ultra,
  en Gemini antwoordt met een upsell. `build_reel_video()` mengt nog steeds
  MP4's en JPEG's (en trekt ze gelijk in maat/fps/SAR, anders weigert concat),
  dus aanzetten kost verder niets. Doe dat pas met een abonnement, en fiks dan
  éérst de bestandsveld-detectie in `reel_animator.py` — zie de toelichting bij
  `REEL_ANIMATE` in `config.py` voor beide gebreken.

  **De Reel draait op een cyclus van 6 dagen, niet wekelijks** — daardoor
  rouleert de weekdag (zo → za → vr → …, hele week in 42 dagen) i.p.v. vast te
  staan op zondag, volgens meerdere analyses de zwakste dag. Een cron-veld kan
  dat niet uitdrukken, dus `scheduler.py` zet de regel alléén op een cyclusdag
  in de crontab (`config.is_reel_day()`); `weekly_reel.py` toetst het nog eens
  zelf tegen dubbel posten. Zie `INSTAGRAM_PLAN.md` fase 10.
  **Twee valkuilen bij de Reel** (beide raakten de eerste echte post, zie
  `INSTAGRAM_PLAN.md` fase 9): de nginx-config van `ig-media` op meterkast moet
  `.mp4` serveren — hij deed lang alleen `.jpg`, waardoor Meta de video niet kon
  ophalen; en een Reel moet een audiostream hebben, dus `build_reel_video()` zet
  er altijd minstens een stil spoor in. Muziek via `REEL_AUDIO_FILE` (standaard
  `Beauty Flow.mp3`, CC BY — naamsvermelding staat op de colofonpagina).
- **IG-caption: harde limiet van 2200 tekens** (`IG_CAPTION_MAX` in
  `ai_processor.py`) — erboven weigert de Graph API de *hele* post
  ("The caption was too long"), en ~9 artikelhooks halen dat al. Vandaar
  `fit_ig_entries()`: de digest snoeit het aantal artikelen tot de caption past,
  i.p.v. achteraf een fout op te vangen. Wie een vast blok aan de caption
  toevoegt, moet die limiet meerekenen. `instagram_queue.json` is een
  **dagwachtrij, geen backlog**: na een geslaagde digest gaat alles eruit, ook
  wat niet in de caption paste — die artikelen staan op de site en een dag
  later alsnog als "nieuws" posten is slechter dan overslaan. Alleen een
  *mislukte* digest laat de wachtrij staan voor een nieuwe poging, en dan nog
  maar 2 dagen: zonder die grens groeide de wachtrij na één transient fout door
  tot élke volgende poging te lang was en de digest zich nooit meer herstelde
  (24–26 juli 2026, ~2,5 dag geen posts).
- **Approval server**: system-supervisord service `tnv-approval-server`
  (`/etc/supervisor/conf.d/user/tnv-approval-server.conf`); `supervisorctl` vereist
  sudo. `APPROVAL_BASE_URL` staat op een LAN-adres — de knoppen in de mail werken
  alleen op LAN/VPN.
- **Service watchdog**: `service_watchdog.sh` elke 5 min via cron (in de
  scheduler-template) — herstart gestopte supervisor-services, herstelt
  log-permissies, meldt via Telegram (credentials uit `.env`).
- **Op oracle-web**: cron (ubuntu) 06:00+18:00 draait `nginx_stats.py` → JSON-cache
  voor de analytics-pagina; root-cron 05:15 UTC draait
  `/usr/local/bin/ssl_watchdog.sh` (kopie van `ssl_watchdog.sh` hier) — controleert
  en vernieuwt SSL-certs, herstelt nginx-config, meldt via Telegram (env:
  `/root/ssl_watchdog.env`).

## Hulpscripts (handmatig)

- `backfill.py` — vult de site geantidateerd met historische artikelen
- `publish_pages.py` — publiceert/updatet WP-pagina's vanuit `assets/`
- `source_discovery.py` — breidt `sources.txt` uit met domeinen die door ≥2
  bestaande bronnen werden gelinkt, plus een Claude-suggestie van ontbrekende
  gerenommeerde tech-sites; kandidaten moeten een bereikbaarheids-/RSS-check én
  een Claude-reputatie-oordeel doorstaan, en werken daarna `/bronnen/` bij.
  **Draait niet meer automatisch** — de wekelijkse cron is er 2026-08-25 uit
  gehaald omdat de belangrijke bronnen inmiddels in de lijst staan en elke
  toevoeging daarna vooral ruis is. Handmatig draaien mag; toevoegingen (en
  dry-run-resultaten) staan in `source_discovery_log.txt`, zodat je iets kunt
  terugdraaien. Blijft gelden: een *algemeen* nieuwsmedium mag alleen mee met de
  RSS-feed van zijn tech-sectie (`tech_rss` in het reputatie-oordeel, eerst
  geverifieerd op echte items) — zonder die eis kwam `nytimes.com` binnen op
  zijn brede voorpaginafeed.
- `bronnen_page.py` — genereert `/bronnen/` uit `sources.txt`; `--publish` zet hem
  ook op WordPress. `source_discovery.py` roept dit zelf aan na een toevoeging,
  zodat de publieke bronnenlijst niet achterloopt (aug. 2026 stonden er 21
  vermeld tegen 35 in gebruik). Omschrijvingen staan per domein in
  `assets/bronnen_meta.json`; een onbekend domein laat het script eenmalig door
  Claude beschrijven en cachet dat. `assets/pagina-veel-gebruikte-bronnen.html`
  is dus **gegenereerd** — bewerk het bestand niet met de hand.
  > **Let op bij álle pagina's in `assets/`: `wpautop` verbouwt witruimte.** Een
  > lege regel wordt een alineagrens — óók binnen `<style>`, waar een letterlijke
  > `</p><p>` midden in de CSS belandt en de browser de rest van het blok
  > overslaat (de mobiele media-query stond zo wél in de paginabron maar werkte
  > niet). Een enkele regelovergang bínnen een alinea wordt een `<br>`, wat op
  > mobiel afgebroken regels midden in een zin geeft. Dus: geen lege regels in
  > een `<style>`-blok, en alinea-tekst op één regel. `build_html()` doet dat
  > automatisch; handgeschreven pagina's zoals `pagina-colofon.html` niet.
- `adhoc_processor.py` — één URL → WP-draft (gebruikt door approval-server dashboard)
- `update_bluesky_profile.py` — eenmalig Bluesky-profiel bijwerken
- `brand.py` — **één bron voor het merk**: het T-teken, het woordmerk en de hele
  set statische beelden in `assets/brand/` (logo's, profielbanners,
  postachtergronden). `instagram_image.py` haalt het woordmerk hier op, dus de
  Reel-kaarten en de site tonen hetzelfde logo. Pas het logo hier aan en draai
  `venv/bin/python3 brand.py` — niet in een beeldbewerker. Zie
  `assets/brand/README.md` voor welk bestand waarvoor is

## Opgeruimd (2026-07-10)

`daily_report.py` (vervangen door `daily_digest.py`) en `tnv-telegram-bot.service`
(de bijbehorende `telegram_bot.py` was al verwijderd in commit a101266) zijn
verwijderd. De watchdog-scripts zijn sindsdien wél actief — zie Scheduling &
services.

## Architecture

### Dagelijkse pijplijn (`main.py`)

Nieuwsartikelen worden **direct gepubliceerd** (status `publish`) en daarna sociaal
gedeeld; de mail biedt achteraf een Decline-knop (verwijdert artikel + Bluesky-post)
en een Nieuwe afbeelding-knop, 4 uur geldig. Alleen de **editorial** gaat als draft
en wacht op goedkeuring — zie hieronder.

```
scraper.py → ai_processor.py → image_generator.py → wordpress_client.py → mailer.py
                                                                               ↓
                                                              (Decline / Nieuwe afbeelding)
                                                                               ↓
                                                              approval_server.py (Flask, port 5055)
                                                                    ↓                ↓
                                                             delete_post()    update_featured_image()
```

**Dataflow:**
- `scraper.py` produceert `list[Article]` (dataclass met title, url, pub_date, excerpt, image_url)
- `ai_processor.py` consumeert `list[Article]`, vraagt Claude om selectie en per artikel JSON met titel/samenvatting/trefwoorden/categorie; produceert `list[ProcessedArticle]`
  - **`triage_articles()` doet tech-filter, deduplicatie, uitsluiting van wat
    we net brachten én rangschikking in één Claude-aanroep** (waren er vier,
    tot 2026-08-18 — zie Tokenverbruik). De tech-toets is er omdat
    gerenommeerde maar brede bronnen (`nytimes.com`, `theguardian.com`) ook
    muziek, sport en algemeen nieuws leveren en de selectieprompt alleen niet
    genoeg was: op 2026-08-09 won een artikel over een blink-182-album de
    selectie en stond het op de site. Zulke bronnen staan daarom óók op hun
    tech-sectiefeed in `sources.txt`; de filter is het vangnet voor wat daar
    alsnog doorheen komt.
    Drie uitkomsten, en het verschil is wezenlijk: een **lege lijst** is een
    geldig oordeel (dan publiceert de run niets), **`None`** betekent dat de
    aanroep mislukte en laat álle kandidaten staan zodat de triage de pijplijn
    nooit blokkeert, en anders krijg je maximaal 5 kandidaten op volgorde. De
    duplicaat-toets zit nu vóór de samenvatting in plaats van erna, dus een
    artikel dat op een recente publicatie lijkt kost geen dure aanroep meer.
    `similar_to_recent_titles()` blijft als gratis lokaal vangnet achteraf.
- `image_generator.py` vraagt Claude om een FAL.ai prompt + brand_domain (JSON), genereert afbeelding via FAL.ai, haalt echt logo op via Google favicon service en composit het over de afbeelding (PIL, bottom-right)
- `wordpress_client.py` maakt categorieën/tags aan, uploadt afbeelding, publiceert de post; produceert `list[dict]` met `{'article': ProcessedArticle, 'post': {'id', 'preview_url', 'link', 'image_url', 'title'}}`
- `mailer.py` verstuurt HTML-notificatiemail met de afbeelding en twee knoppen per artikel

### Approval flow

- `approval_store.py` — JSON token store (`approval_tokens.json`), `TTL_HOURS = 4`, replay-beveiliging.
  `create_tokens()` geeft `(decline_token, new_image_token)` terug;
  `create_editorial_tokens()` geeft `(publish_token, decline_token)` met een eigen,
  ruimere TTL.
- `approval_server.py` — Flask server op `APPROVAL_HOST:APPROVAL_PORT` (standaard `0.0.0.0:5055`):
  - `GET /decline/<token>` — verwijdert Bluesky-post (indien aanwezig), haalt het artikel uit de Instagram-wachtrij en verwijdert de WP-post (`force=True`)
  - `GET /new-image/<token>` — bevestigingspagina direct → genereert FAL.ai-afbeelding, uploadt naar WP en verstuurt nieuwe mail in achtergrondthread
  - `GET /publish/<token>` — publiceert een editorial-draft (geen social posting)
  - `GET|POST /revise/<token>` — herschrijf-formulier voor een editorial: toont de huidige tekst met een commentaarveld, laat Claude het stuk herschrijven, werkt de draft bij en mailt de nieuwe versie. Token wordt **niet** verbruikt — herschrijven mag zo vaak als nodig binnen de TTL
  - `GET /submit` (POST) + `GET /status/<job_id>` — dashboard-flow via `adhoc_processor.py`
  - `GET /health` — health check + cleanup verlopen tokens
  - `GET /analytics` — analytics-pagina

### Editorial (`editorial.py`)

Opiniërend redactiestuk als "wij, de redactie", los van de nieuwspijplijn en
achter `ENABLE_EDITORIAL`. Cron ma/wo/vr 09:00 CET (in de scheduler-template).

Kandidaat-onderwerpen komen uit `fetch_recent_published()` — de artikelen die de
site zelf al plaatste, dus geen aparte scrape. Claude kiest daaruit zelf het
onderwerp met de meeste duidingswaarde en levert JSON
(`titel`/`inhoud`/`standpunt_samenvatting`/`onderwerp_tags`).

**Gaat bewust als draft naar WordPress** (`create_editorial_draft()`), anders dan
nieuwsartikelen: een stuk dat per instructie altijd een expliciet standpunt inneemt
hoort niet ongelezen live te gaan. De mail toont de volledige tekst plus drie
knoppen — Publiceer, Herschrijf en Verwijder; niets doen laat het concept staan. De
prompt draagt op om bij politiek/maatschappelijk gevoelige onderwerpen één serieus
tegenargument te verwerken — scherp mag, eenzijdig niet.

**Beeld is niet optioneel.** `index.php` pakt de drie nieuwste posts als hero-grid
zonder categoriefilter, dus een gepubliceerde editorial staat meteen groot op de
homepage — en zonder featured image rendert het thema daar een grijs vak met
"Geen afbeelding" (420px hoog). `editorial.py` genereert het beeld daarom via
`generate_image_for_editorial()` (eigen promptvariant, zie Afbeeldingsgeneratie);
lukt dat niet, dan waarschuwt de mail expliciet vóór je op Publiceer drukt.

**Uitgesloten van social.** Editorials komen niet in `instagram_queue.json` (de
dagdigest is een nieuwsoverzicht; een standpunt zonder klikbare onderbouwing
eronder is precies het risico waarvoor de goedkeuringsstap bestaat) en niet in de
wekelijkse Reel — `fetch_posts_for_reel()` sluit de categorie expliciet uit via
`categories_exclude`. Dat filter is nodig: tot dan vielen editorials er alleen
buiten doordat ze géén featured image hadden, en dat is sinds bovenstaande niet
meer waar. `/publish` post ook niet naar Bluesky.

**Herschrijfronde:** Herschrijf opent `/revise/<token>` op de approval-server, met
de huidige tekst en een commentaarveld. Het commentaar gaat als redactionele
instructie mee terug naar Claude (`revise_editorial()`), waarna de **bestaande
draft wordt bijgewerkt** (`update_editorial_draft()`) — één draft, hoe vaak je ook
laat herschrijven. Elke ronde levert verse tokens en een nieuwe mail; de
ronde-teller staat in de token-meta en in het mailonderwerp. De huidige tekst zit
in die meta, zodat een revisie niets uit WordPress hoeft terug te halen.
- Draait als **supervisord** service (`/etc/supervisor/conf.d/user/tnv-approval-server.conf`)

## Sleutelbestanden

| Bestand | Doel |
|---|---|
| `config.py` | Alle settings uit `.env`, gedeelde paden |
| `approval_store.py` | Token store voor Decline/Nieuwe afbeelding + editorial Publiceer |
| `editorial.py` | Opiniërend redactiestuk → WP-draft + goedkeuringsmail (ma/wo/vr) |
| `approval_server.py` | Flask approval server (incl. `/submit` dashboard endpoint) |
| `adhoc_processor.py` | Verwerkt één URL direct naar WordPress post (dashboard flow) |
| `approval_tokens.json` | Pending tokens — niet verwijderen |
| `image_distribution.json` | Persistente teller voor beeld-persoonsvariatie; stuurt de verdeling naar `IMAGE_DISTRIBUTION_TARGETS` in `config.py` |
| `posted_urls.txt` | Één URL per regel, voorkomt dubbele posts |
| `sources.txt` | Één domein/URL per regel |

## Afbeeldingsgeneratie (`IMAGE_STRATEGY=generate`)

Claude genereert een JSON met een `prompt` — fotorealistische beschrijving zonder
logo's/tekst (FAL.ai hallucineerde anders logo's). Daarna zet `add_ai_label()`
het AI-label op het beeld.

**Wélke dienst het beeld maakt staat los van de prompt**: `IMAGE_PROVIDER` in
`.env` kiest tussen `fal` (FAL.ai flux/dev, standaard) en `nanobanana` (Nano
Banana 2 / `gemini-3.1-flash-image` via de Gemini Interactions API). De
implementaties zitten in het pakket `image_providers/` achter één interface
(`ImageProvider.generate_image(prompt, dest_path, options)`);
`image_generator.py` gaat alleen nog over de prompt en de nabewerking en roept
`generate_provider_image()` aan. Twee dingen om te weten bij wijzigingen:

- **De keten is `webgemini` → `nanobanana` → `fal`** (`IMAGE_PROVIDER` +
  komma-gescheiden `IMAGE_FALLBACK_PROVIDER`), van gratis naar duur.
  `webgemini` genereert via gemini.google.com in de Selenium-Firefox op
  meterkast en levert 1376×768 — dezelfde resolutie als de betaalde API op 1K.
  Drie dingen die hem laten falen, en alle drie zijn normaal: het gratis
  dagquotum (~3 beelden), een verlopen Google-sessie, of een gewijzigde DOM.
  Elk daarvan geeft `None` en schuift de keten door.
  > **De sessie kan niet automatisch herstellen.** Google weigert een
  > geautomatiseerde login, dus de provider leent de cookies uit het
  > Firefox-profiel (`WEBGEMINI_PROFILE`) waar met de hand is ingelogd. Staat er
  > structureel `Web-Gemini: niet ingelogd` in de log, dan is dát de reden en
  > moet je opnieuw inloggen in de Firefox-container. Tot die tijd betaalt de
  > site gewoon via `nanobanana` — stil duurder, niet stuk.
  > Twee valkuilen bij onderhoud: het beeld in de DOM is een **preview** van
  > 1024px (de volle resolutie zit achter "Download full size image", die een
  > PNG in de downloadmap van de Selenium-container zet), en de pagina draait
  > **Trusted Types**, dus de prompt moet met native WebDriver-typen ingevoerd
  > worden — `innerHTML` wordt door de CSP geblokkeerd.
- **Terugval bij een mislukt beeld, níét bij een misconfiguratie.** Levert de
  primaire provider geen beeld op (quota op, time-out), dan neemt de volgende
  schakel het over, met een `WARNING` in de log. Ontbreekt daarentegen de *key* van de primaire provider of is
  `IMAGE_PROVIDER` onbekend, dan volgt een `ImageProviderError` en stopt
  `main.py` vóór het eerste beeld (in `--dry-run` alleen een waarschuwing) —
  terugval zou die fout verbergen en de site maanden ongemerkt op de verkeerde
  dienst laten draaien. De helpers in `image_generator.py` laten die fout er
  expliciet doorheen; alle *andere* beeldfouten blijven een gelogde `None`.
  **De terugvalprovider krijgt de kale prompt**, niet de al aangevulde: de
  logo-instructie van Nano Banana mag niet meeliften naar flux/dev.
- **Gemini-kosten houden we zelf bij.** Een Gemini API-key heeft geen
  billing-endpoint (FAL.ai wél), dus `record_gemini_usage()` schrijft per beeld
  een regel in `gemini_usage.json` en `budget_monitor.py` telt die op voor het
  dagoverzicht.
  > **Reken de beeldkosten op het formaat, niet op de image-tokens.** De API
  > rapporteert **1120 image-tokens bij élk formaat** — 1K, 2K en 4K geven
  > exact hetzelfde getal (gemeten 2026-08-10), terwijl de prijs wél per
  > formaat verschilt. Op tokens rekenen gaf daardoor voor alles ~$0.068 en
  > verzweeg een derde van de kosten van een 2K-beeld. `_GEMINI_IMAGE_PRICE`
  > is de bron; de tokentarieven gelden alleen nog voor de invoer- en
  > tekst-/denktokens erbovenop.
  Er is geen "resterend tegoed" bij Google (postpaid). `GEMINI_PREPAID_CREDIT`
  telt af vanaf het ongesnoeide lifetime-verbruik; `GEMINI_MONTHLY_BUDGET` is
  het alternatief dat elke maand opnieuw begint.
- **`GEMINI_IMAGE_SIZE` staat op 1K, en dat is een bewuste ondergrens.** Het
  thema registreert `tnv-hero` op 780×439 en `tnv-card` op 480×270; Instagram
  krijgt 1080×1350. 1K levert 1376×768 — ruim boven alles wat er getoond wordt,
  terwijl 2K de helft duurder is ($0.101 tegen $0.067) en alleen pixels
  oplevert die WordPress meteen wegschaalt. 0.5K (688×384) is te klein voor
  Instagram. Verhoog dit alleen als het thema grotere formaten registreert.
- **De promptteksten zijn op flux/dev afgestemd.** De uitsluitingen staan
  bewust in de *positieve* prompt (flux kent geen `negative_prompt`, zie
  hieronder). Gemini leest die net zo goed, maar wie de prompts herschrijft voor
  één provider, verandert ze voor beide.
- **Nano Banana vereist billing, ook voor één beeld.** De gratis Gemini-tier
  geeft élk image-model quota 0 (`HTTP 429 ... limit: 0`); zonder billing op het
  Google Cloud-project werkt `nanobanana` niet. Billing staat sinds 2026-08-10
  aan en de provider is die dag end-to-end getest: ~19 s per beeld, 2752×1536
  JPEG (≈3 MB ruw, ≈1 MB nadat `add_ai_label()` het opnieuw wegschrijft).
- **Nano Banana verwerkt échte merklogo's subtiel in het beeld** (op verzoek,
  2026-08-10). Dit model rendert herkenbare, correct geproportioneerde
  merktekens in plaats van de vervormde pseudo-logo's van flux/dev, en dat mag
  hier bewust: `NanoBananaImageProvider.prompt_suffix` vraagt om logo's dáár
  waar ze in een echte foto zouden staan (apparaten, schermen, signage,
  verpakking), klein en terloops, nooit als blikvanger.
  `generate_provider_image()` plakt de suffix er op het laatste moment achter,
  dus hij geldt voor álle promptvarianten — nieuws, editorial én gevoelig
  onderwerp. **De FAL-prompts zijn hierdoor letterlijk ongewijzigd** t.o.v.
  2026-08-04: `FalImageProvider.prompt_suffix` is leeg, en flux zou er toch
  vervormde merktekens van maken.
  > **De uitzonderingszin achteraan is niet optioneel.** De gedeelde
  > nieuwsprompt eist "no text or lettering", en een woordmerk ís lettering.
  > Zonder de expliciete uitzondering staan er twee tegenstrijdige instructies
  > in één prompt en laat het model er willekeurig één vallen — hetzelfde
  > mechanisme dat de group-template eerder de sekse deed weglaten.
  > Bijwerking: er duikt soms klein, onleesbaar tekstje op in UI-panelen en op
  > winkelbordjes. Wil je dat strakker, dan is die zin de plek.
- `generate_header_image.py` en `update_bluesky_profile.py` hebben nog hun eigen
  directe FAL.ai-aanroep en volgen `IMAGE_PROVIDER` niet — losse hulpscripts.

> **Geen `negative_prompt` — dat veld bestaat niet.** `fal-ai/flux/dev` heeft
> alleen prompt, image_size, num_images, num_inference_steps, guidance_scale,
> seed, sync_mode, output_format, acceleration en enable_safety_checker; flux dev
> is guidance-distilled en heeft geen CFG-negative. Wat we meestuurden werd dus
> weggegooid. Uitsluitingen horen in de **positieve** prompt — flux volgt die
> wél, zoals "no text or lettering" al liet zien. `generate_fal_image()` heeft de
> parameter daarom niet meer; voeg hem niet opnieuw toe.

**De persoonsvariant moet letterlijk in de prompt staan.** Claude schrijft de
nieuwsprompt zelf en liet de gekozen sekse in 25 van de 53 *group*-beelden weg
("three young colleagues"); flux/dev vult zo'n genderloze groep standaard met
mannen, dus een gevraagde vrouw werd een man. De group-template sprak zichzelf
tegen — hij vroeg om "predominantly women" en verbood tegelijk "individual
demographic traits". Nu eisen de templates de sekse expliciet op, en
`_enforce_person_in_prompt()` hangt hem er deterministisch achter als hij alsnog
ontbreekt (met een `WARNING` in de log, zodat wegzakken zichtbaar blijft). Solo
was altijd al 31/31 goed.

Een *group* is bovendien altijd één sekse. Alleen de sekse eisen was niet
genoeg: de prompt noemde dan wel "women", maar Claude zette er "one male
colleague" of "and a colleague" naast — en van een genderloze "colleague" maakt
flux/dev standaard een man. `_enforce_person_in_prompt()` hangt er bij een groep
daarom altijd een expliciete afbakening achter (geen bijfiguren van een andere
sekse, ook niet op de achtergrond); de sekse-check zelf zou zo'n gemengde groep
niet zien, want het gevraagde woord stáát er.

**Styling van de vrouwelijke variant** is nadrukkelijk aantrekkelijk/erotisch en
staat **bovenop** de zakelijke beschrijving ("confident", plus expliciet "never
passive or decorative"), niet in plaats daarvan — de twee sluiten elkaar niet
uit, en zonder die basis stond de erotische omschrijving tegenover het
"confident, professional" van de mannelijke variant. Alleen `gender == "a woman"`
krijgt hem; man houdt de standaard. Er is geen toggle meer: `IMAGE_ATTRACTIVE_WOMEN`
is verwijderd (2026-08-04), net als de `non-binary`-variant en de
`_ADULT_REINFORCEMENT`-zin die onder de 21 jaar volwassenheid afdwong. Die zin
verviel samen met het optrekken van de jongste leeftijdsbucket van 18-22 naar
**20-22**; verlaag die ondergrens niet zonder de zin terug te zetten, want
beeldmodellen renderen een opgegeven leeftijd regelmatig duidelijk jonger en de
styling is expliciet.

`_ATTRACTIVE_MARKERS` moet meebewegen met die styling-teksten: die lijst is hoe
`_enforce_person_in_prompt()` ziet dát de styling al in de prompt zit (Claude
herformuleert hem meestal). Staat er geen enkel woord uit de tekst in, dan hangt
de clausule er een tweede keer achter.

**De kledingstijl is een eigen dimensie** (`outfit` in
`IMAGE_DISTRIBUTION_TARGETS`, 80% `casual` en 20% `streetwear`) en
rouleert dus via dezelfde teller als sekse en leeftijd. Zonder
instructie kiest het beeldmodel bij élk artikel zakelijke kantoorkleding, en
worden de beelden onderling inwisselbaar. `build_outfit_clause()` levert de zin,
`_enforce_person_in_prompt()` hangt hem er alsnog achter als Claude de stijl
liet vallen (`_OUTFIT_MARKERS` is de check — houd die lijst onderscheidend, want
overlappende woorden als "sneakers" of "denim" laten de check op de verkéérde
stijl slagen). De styling-clausule van de vrouwelijke groepsvariant zegt daarom
sindsdien alleen nog "form-fitting clothing" en niet meer "fashionable outfits":
twee instructies over kleding in één prompt en het model laat er willekeurig één
vallen — hetzelfde mechanisme dat de group-template eerder de sekse deed
weglaten.

Twee promptvarianten, bewust gescheiden — en op een fundamenteel andere manier
opgebouwd:

| | `generate_image_prompt()` (nieuws) | `generate_editorial_image_prompt()` |
|---|---|---|
| Wie schrijft de prompt | Claude schrijft de hele prompt | **vaste template**; Claude vult alleen `{thema}` in |
| Sfeer | vast "bright lighting, optimistic mood" | dramatisch zijlicht, hoog contrast, moody, 35mm, gedempt palet met één accentkleur |
| Uitsluitingen | "no text or lettering" in de prompt zelf | idem, plus "not a cartoon, not a 3d render, no oversaturated colours, no distorted hands" in de template |

Logo-uitsluitingen zijn er in alle drie de varianten uit (2026-08-04, op verzoek). Ze stonden er omdat flux/dev anders merktekens hallucineert; zie je vervormde pseudo-logo's terugkomen, dan is dát de plek om te kijken.

De beeldtaal voor editorials ligt vast in `_EDITORIAL_IMAGE_TEMPLATE` zodat ze als
**serie** herkenbaar zijn; alleen het thema wisselt. Claude levert daarvoor een
korte Engelse nominale frase (max 12 woorden) die zegt wát er op het spel staat —
geen fotobeschrijving, geen merknaam. Mislukt dat, dan valt het thema terug op de
titel, zodat er altijd een werkbare prompt uitkomt.

De nieuwsvariant past niet op een opiniestuk: een zonnig kantoorbeeld bij een
kritische editorial ondermijnt het betoog.

**Guard voor gevoelige onderwerpen.** `is_sensitive_topic()` draait vóór de
promptgeneratie van nieuwsartikelen. Bij menselijk leed (beeldmisbruik, geweld,
uitbuiting, kindveiligheid…) vervalt zowel de opgewekte toon als de
persoonsvariant: `_build_sensitive_image_prompt()` vraagt om een ingetogen,
conceptueel beeld zonder slachtoffers, met `_SENSITIVE_PROMPT_SUFFIX` er
deterministisch achter (sluit lachen, juichen, thumbs-up en verzadigde kleuren
expliciet uit — in de prompt zelf, niet als negative). Gewoon negatief zakelijk
nieuws — ontslagen, rechtszaken, boetes — telt bewust *niet* mee, anders vangt
de guard de halve nieuwsstroom.

De check zit bewust vóór `generate_person_variant()`: alleen zo blijft de teller
in `image_distribution.json` eerlijk voor de artikelen die wél een persoon
tonen. Bij een gevoelig artikel is de variant een lege dict, waardoor `mailer`
de "Beeld-variant"-regel vanzelf weglaat. Faalt de check, dan wordt hij als
niet-gevoelig behandeld — de guard mag de normale flow nooit blokkeren.

Aanleiding: op 2026-06-23 leverde de standaardstijl bij een artikel over
beeldmisbruik bijna een opgewekt beeld met een vrouw als middelpunt op.

> **Dode code:** `fetch_brand_logo()` en `composite_logo()` worden nergens
> aangeroepen — de logo-compositing die hier eerder beschreven stond, gebeurt
> niet meer. `brand_domain` zit ook niet meer in het JSON-antwoord.

## Tokenverbruik

Elke Claude-vraag in dit project loopt via de **Claude Code CLI** (`_call_claude()`
in `ai_processor.py`), en die rekent per *aanroep* een vaste berg context af —
systeemprompt, tooldefinities, CLAUDE.md — vóór de eigenlijke vraag begint.
Gemeten 2026-08-18: een vraag van 1.700 tekens kostte 35.957 tokens. Het aantal
aanroepen en het aantal beurten per aanroep bepalen dus het verbruik, niet de
lengte van de prompt. Drie regels die daaruit volgen:

1. **De CLI draait in `/tmp/tnv-claude-cwd`, niet in de projectmap**
   (`_neutral_cwd()`). De CLI zoekt CLAUDE.md-bestanden vanaf zijn werkmap
   omhoog en plakt ze integraal in élke aanroep; vanuit de projectmap was dat
   ~19.200 tokens per keer aan instructies die met de vraag niets te maken
   hebben (35.957 → 16.733 tokens context). Verplaats dit niet terug naar
   `BASE_DIR` — dan vindt de zoektocht omhoog de projectinstructies weer, en
   `/home/dgebbink/CLAUDE.md` erbij. Neveneffect: de sessielogs van de bot staan
   onder `~/.claude/projects/-tmp-tnv-claude-cwd/`.
2. **Voeg geen aanroep toe waar een bestaande vraag verbreed kan worden.**
   `triage_articles()` was vier losse aanroepen over dezelfde lijst; samengevoegd
   ging die stap van ~190.000 naar ~48.000 tokens per run. Let wel op de
   tegenhanger die elders in dit bestand terugkomt: twee *tegenstrijdige*
   opdrachten in één prompt laat het model er willekeurig één van vallen. Opeen-
   volgende filters op één lijst mogen samen; de gevoeligheidscheck van
   `image_generator.py` blijft bewust apart, want die moet juist *tegen* de
   opgewekte huisstijl in kunnen gaan.
3. **Laat Claude niet browsen naar tekst die je al hebt.** `process_article()`
   zei "lees het artikel via de meegeleverde link" óók als de tekst al in de
   prompt stond; de CLI ging dan zelf op onderzoek (WebFetch, WebSearch, Bash)
   en één samenvatting kostte 21 API-calls en ~549.000 tokens. Boven
   `_VOLLEDIGE_TEKST_DREMPEL` (1.200 tekens) draagt de prompt nu expliciet op om
   de link níét te openen: 2 calls, ~53.000 tokens. Zelf ophalen met
   `fetch_article_text()` kost een HTTP-request en geen tokens, dus dat proberen
   we eerst — maar het lukt niet altijd: **DPG Media-sites (o.a. tweakers.net)
   geven de scraper een consent-muur** ("DPG Media Privacy Gate", 22 tekens), en
   dan valt zo'n artikel alsnog terug op de dure route.

Meten kan achteraf zonder extra kosten: elke aanroep laat een sessielog achter
met `usage`-velden per API-call. `~/.claude/projects/-tmp-tnv-claude-cwd/*.jsonl`
optellen geeft het echte verbruik per stap.

**Wat níét helpt:** de bronnenlijst opdelen en per run een deel scannen.
Scrapen kost geen tokens, en `MAX_ARTICLES_FOR_SELECTION` kapt de lijst toch al
op 50 terwijl de bronnen samen 200–450 artikelen per run opleveren — een derde
van de bronnen levert nog steeds ruim 50 kandidaten, dus de prompt wordt geen
teken korter. `--tools ""` meegeven aan de CLI is ook averechts: gemeten 135.061
tokens in plaats van 35.957.

## Server (WordPress)

- **SSH:** `ssh -i ~/.ssh/ssh-key-oracle-web.key ubuntu@141.144.195.65`
- WordPress installatiepad: `/var/www/technieuwsvandaag/wordpress/`
- Actief theme: `tnv-news` (`wp-content/themes/tnv-news/`)
- Theme-bestanden zijn eigendom van `www-data` — gebruik `sudo` om te schrijven
- DB: `wpdbtech` op `localhost`, user `dgebbink`
- WP-CLI beschikbaar: `sudo -u www-data wp ...` (vanuit de WordPress root)
- Thema templates: `index.php` (homepage), `archive.php` (categorie/tag), `template-nieuws.php` (alle nieuws, pagina ID 778)
- **Mobiele navigatie zit in het thema, niet in WordPress-instellingen.** Onder
  640px verbergt de CSS de menubalk; de hamburgerknop (`#tnv-menu-toggle` in
  `header.php`, gedrag in `tnv-news.js`, opmaak in de `max-width: 640px`-blok van
  `style.css`) klapt `#primary-menu` uit als paneel. Tot 2026-08-09 bestond die
  knop niet en was er op mobiel dus géén navigatie — ook de categorieën waren
  alleen via de footer of zoeken bereikbaar. Twee dingen om te weten bij
  wijzigingen: de knop moet `background`/`box-shadow` expliciet uitzetten voor
  `:hover`/`:focus`/`:active` (GeneratePress geeft élke `<button>` een grijs vlak)
  en hij mag niet krimpen (`flex: 0 0 40px`), anders zakt hij terug naar de
  breedte van het icoon. De sociale iconen wijken onder 640px om ruimte te maken
  voor het logo — die regel heeft `.site-header` ervoor nodig, want de basisregel
  `.tnv-social-nav` staat verderop in `style.css` en wint anders.
- **Bump `Version:` in `style.css`** na elke wijziging aan het thema: dat nummer
  is de cache-buster van `wp_enqueue_*`, en zonder bump draaien terugkerende
  bezoekers de oude JS tegen de nieuwe markup.

## WordPress auth

HTTP Basic met base64-encoded `WP_USERNAME:WP_APP_PASSWORD`. Custom field `bron_url` vereist `register_post_meta()` in WordPress (zie README).

## Logging

- Dagelijkse runs: `logs/run_YYYY-MM-DD_HH-MM-SS.log`
- Approval server: `logs/approval_server.log`

## Versiebeheer

`origin` → github.com/dgebbink/technieuwsvandaag (**publiek**, bewust zo
gekozen — niet omzetten naar privé zonder overleg). Commit en push gewoon
naar `origin` na wijzigingen.
