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
- **Bronnenlijst-uitbreiding**: zondag 04:00 `source_discovery.py` (in de
  scheduler-template) — breidt `sources.txt` automatisch uit met domeinen die
  deze week door ≥2 bestaande bronnen werden gelinkt, plus een korte
  Claude-suggestie van ontbrekende gerenommeerde tech-sites. Kandidaten moeten
  eerst een bereikbaarheids-/RSS-check én een Claude-reputatie-oordeel
  doorstaan. Toevoegingen (en dry-run-resultaten) staan in
  `source_discovery_log.txt` — controleer/draai daar desgewenst iets terug.
- **Op oracle-web**: cron (ubuntu) 06:00+18:00 draait `nginx_stats.py` → JSON-cache
  voor de analytics-pagina; root-cron 05:15 UTC draait
  `/usr/local/bin/ssl_watchdog.sh` (kopie van `ssl_watchdog.sh` hier) — controleert
  en vernieuwt SSL-certs, herstelt nginx-config, meldt via Telegram (env:
  `/root/ssl_watchdog.env`).

## Hulpscripts (handmatig)

- `backfill.py` — vult de site geantidateerd met historische artikelen
- `publish_pages.py` — publiceert/updatet WP-pagina's vanuit `assets/`
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

> **Geen `negative_prompt` — dat veld bestaat niet.** `fal-ai/flux/dev` heeft
> alleen prompt, image_size, num_images, num_inference_steps, guidance_scale,
> seed, sync_mode, output_format, acceleration en enable_safety_checker; flux dev
> is guidance-distilled en heeft geen CFG-negative. Wat we meestuurden werd dus
> weggegooid. Uitsluitingen horen in de **positieve** prompt — flux volgt die
> wél, zoals "no text, no logos" al liet zien. `generate_fal_image()` heeft de
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

Twee promptvarianten, bewust gescheiden — en op een fundamenteel andere manier
opgebouwd:

| | `generate_image_prompt()` (nieuws) | `generate_editorial_image_prompt()` |
|---|---|---|
| Wie schrijft de prompt | Claude schrijft de hele prompt | **vaste template**; Claude vult alleen `{thema}` in |
| Sfeer | vast "bright lighting, optimistic mood" | dramatisch zijlicht, hoog contrast, moody, 35mm, gedempt palet met één accentkleur |
| Uitsluitingen | "no text, logos, lettering" in de prompt zelf | idem, plus "not a cartoon, not a 3d render, no oversaturated colours, no distorted hands" in de template |

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

## Versiebeheer

`origin` → github.com/dgebbink/technieuwsvandaag (**publiek**, bewust zo
gekozen — niet omzetten naar privé zonder overleg). Commit en push gewoon
naar `origin` na wijzigingen.
