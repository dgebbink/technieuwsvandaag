# Instagram-integratie — implementatieplan

**Status (2026-07-22):** fase 0 t/m 6 klaar en live. Fase 6: `main.py` post niet
meer direct naar Instagram per artikel — bij een klein aantal volgers waren 5
losse posts/dag te veel. Artikelen komen in `instagram_queue.json` terecht
(`social_poster.queue_instagram_post()`) en `instagram_digest.py` bundelt die
om 19:45 CET tot één post: 1 artikel → los beeld, 2+ → carousel
(`social_poster.post_instagram_digest()`). Decline vóór 19:45 haalt het
artikel gewoon uit de wachtrij (`remove_from_instagram_queue`); ná de
digest-post kan dat niet meer (zie bekende beperking hieronder). Fase 7
(zelfde dag): wekelijkse silent Reel als extra bereik-kanaal, zie hieronder.

Doel: elk goedgekeurd artikel wordt automatisch als Instagram-post gepubliceerd op
**@technieuwsvandaag.nl**, in de stijl van @de_volkskrant: de (al gegenereerde)
artikelafbeelding met een witte balk eroverheen waarin een korte, heldere kop staat.
Volledig autopost, AI-transparant, en visueel in lijn met de site
(navy `#0A1628`, cyaan `#00D4FF`, wit).

---

## Fase 0 — Accountsetup (handmatig, eenmalig)

Instagram autoposting kan alleen via de **Meta Graph API** en vereist een
Professional (Business) account gekoppeld aan een Facebook-pagina.

1. [x] Instagram-account `technieuwsvandaag.nl` omzetten naar **Professioneel → Bedrijf**
       (app: Instellingen → Accounttype).
2. [x] Facebook-pagina "TechNieuwsVandaag" aanmaken (mag leeg blijven) en koppelen aan
       het IG-account (IG app: Bewerk profiel → Pagina).
       *Deze pagina is meteen ook de basis voor de geplande Facebook-posting.*
3. [x] Meta Developer-app aanmaken op https://developers.facebook.com (type "Business")
       met producten **Instagram Graph API** + **Facebook Login**.
4. [x] Via Graph API Explorer een user token genereren met scopes:
       `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
       `pages_read_engagement` → omwisselen voor long-lived token.
5. [x] **Page token afleiden** (verloopt nooit) — de logica hiervoor bestaat al in
       `projects/amsterdam/backend/app/services/instagram_poster.py`
       (`derive_page_token()`); we porten die als helper-script (fase 3).
6. [x] `INSTAGRAM_ACCOUNT_ID` ophalen: `GET /{page-id}?fields=instagram_business_account`.

Nieuwe `.env`-variabelen:

```
ENABLE_INSTAGRAM_POSTING=false      # pas aan het eind op true
INSTAGRAM_ACCOUNT_ID=
INSTAGRAM_ACCESS_TOKEN=             # never-expiring Page token
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=
INSTAGRAM_POST_DELAY_SECONDS=120    # na Bluesky, niet tegelijk
INSTAGRAM_API_VERSION=v19.0
```

---

## Fase 1 — Profielidentiteit (bio + logo)

### Bio (max 150 tekens, regels breken bewust)

```
⚡ Het belangrijkste technieuws, elke dag in het Nederlands
🤖 Samengevat met AI, beelden AI-gegenereerd
👇 Lees de artikelen op de site
```

Plus profielvelden: categorie "Media/nieuwsbedrijf", link `https://technieuwsvandaag.nl`.
De AI-transparantie staat bewust al in de bio — dat schept vertrouwen en dekt het
account als geheel, niet alleen losse posts.

### Logo / profielfoto

Regel voor avatars: leesbaar op 110×110 px in een cirkel. Het huidige Bluesky-avatar
(3D-glazen "T") is te druk voor dat formaat. Voorstel:

- **Vlakke, geometrische "T"** in wit/cyaan op effen navy `#0A1628`, gecentreerd,
  ruime marge zodat de cirkelcrop niets afsnijdt. Geen tagline, geen extra tekst.
- Genereren met een nieuw script `generate_instagram_assets.py` (PIL, deterministisch —
  géén FAL: een logo moet pixel-precies en reproduceerbaar zijn).
- Zelfde script genereert ook het wordmark-strookje ("TECHNIEUWSVANDAAG.NL") dat op
  elke post terugkomt (zie fase 2), zodat avatar en posts uit dezelfde bron komen.

Deliverables: `assets/instagram_avatar.png` (1000×1000), `assets/ig_wordmark.png`.

---

## Fase 2 — Postbeeld in Volkskrant-stijl (`instagram_image.py`)

Nieuwe module die de bestaande WP-afbeelding omzet naar een IG-feedbeeld.

### Formaat & compositie

- Canvas **1080×1350 (4:5 staand)** — maximale feedruimte, de standaard voor
  nieuwsaccounts. Onze bron is 16:9, dus: schalen tot de hoogte vult en
  **center-croppen** in de breedte. De FAL-prompts zijn centraal gecomponeerd, dus
  dat gaat vrijwel altijd goed; de witte balk maskeert bovendien de onderste strook.
- **Witte balk (de "stripe")**: volle breedte, onderin op ~62% hoogte beginnend,
  effen wit `#FFFFFF`, met daarin:
  - **Kicker** (categorie, bijv. "AI · CHIPS"): klein, kapitaal, cyaan `#00D4FF`
    of rood accent — 1 regel.
  - **Kop**: zwart `#0A1628`, extra bold (Ubuntu-B / DejaVuSans-Bold, ~64px),
    max **2 regels / ±10 woorden**. Dit is níét de WP-titel maar een aparte, kortere
    Instagram-kop (zie fase 4) — Volkskrant-koppen zijn korter en actiever dan
    webkoppen.
  - **Wordmark** "TECHNIEUWSVANDAAG.NL" klein onderin de balk, navy.
- **"AI gegenereerd"-label** blijft zichtbaar op het fotodeel (bestaande
  `add_ai_label()` staat al op het bronbeeld linksonder — checken dat hij niet onder
  de balk verdwijnt; zo wel, opnieuw plaatsen op het fotodeel).

### AI-transparantie in het bestand zelf

- **IPTC-metadata `DigitalSourceType = trainedAlgorithmicMedia`** embedden in de JPEG.
  Meta leest deze metadata en hangt er automatisch het officiële "AI-info"-label aan —
  dit is de enige betrouwbare route; de Graph API heeft geen expliciete AI-vlag-parameter.
  Implementatie: klein IPTC/XMP-blok wegschrijven (via `pillow` + handmatig APP13-segment
  of het `iptcinfo3`/`python-xmp` pakket — uitzoeken wat het lichtst is).

### Interface

```python
def compose_instagram_image(src_path, headline, kicker, dest_path) -> str | None
```

Puur PIL, geen netwerk. Los testbaar met een bestaand beeld uit `tmp/`.

---

## Fase 3 — Posting (`social_poster.py` + tokenhelper)

### `post_to_instagram()` naast `post_to_bluesky()`

Port van de amsterdam-flow, maar **synchroon** (requests i.p.v. httpx/async — dit
project is synchroon):

1. IG-beeld composen (fase 2) vanaf de WP featured image.
2. **Publieke URL regelen**: Graph API accepteert alleen een publieke `image_url`.
   We uploaden het IG-beeld gewoon naar de **WordPress media library** (bestaande
   upload-code in `wordpress_client.py`, niet gekoppeld aan de post) — geen
   Imgur-fallback nodig zoals bij amsterdam, want WP is publiek bereikbaar.
3. Container aanmaken: `POST /{ig-account-id}/media` met `image_url` + `caption`.
4. Pollen op `status_code == FINISHED` (max ~60s).
5. Publiceren: `POST /{ig-account-id}/media_publish`.
6. Media-ID loggen. **Let op:** IG-posts kunnen níét via de API verwijderd worden —
   een decline-achtige rollback bestaat dus niet. Geen probleem: wij posten pas ná
   goedkeuring.

Foutafhandeling zoals bij Bluesky: loggen, nooit raisen, lege string terug.
API-limiet (50 gepubliceerde posts/24u) is met max 5 artikelen/dag geen issue.

### Aanhaken in de approval-flow

In `approval_server.py` (`/approve` background thread): na de Bluesky-post ook
Instagram, met eigen delay `INSTAGRAM_POST_DELAY_SECONDS`. Zelfde patroon in
`post_articles_to_social()` voor de dry-run/log-variant.

### Tokenhelper `instagram_token.py` (handmatig script)

Port van `derive_page_token()` + `refresh_instagram_token()` uit amsterdam:
short-lived user token erin → never-expiring Page token in `.env`. Eén keer draaien
bij setup; daarna alleen als Meta de app-permissies reset.

---

## Fase 4 — Caption & kop (AI, `ai_processor.py`)

Instagram is een ander medium dan de site: links in captions zijn niet klikbaar,
de eerste ~125 tekens bepalen of iemand "meer" tapt. Daarom per artikel twee extra
velden laten genereren door Claude (uitbreiding van de bestaande JSON-vraag, géén
extra API-call):

- `ig_kop` — max 10 woorden, actief, geen punt aan het eind (voor op de witte balk).
- `ig_caption` — opbouw:

```
{Sterke openingszin — de hook, max ±125 tekens, geen emoji-spam}

{1–2 zinnen context uit de samenvatting}

🔗 Lees het volledige artikel via de link in bio.

#technieuws #tech {+2–3 specifieke NL hashtags uit de trefwoorden}
```

Regels: Nederlands, geen clickbait ("Dit wist je nog niet…"), maximaal 5 hashtags
(meer oogt als spam en helpt het bereik niet).
`ProcessedArticle` dataclass uitbreiden met beide velden; fallback = bestaande
titel + eerste zinnen samenvatting (zelfde truc als `_build_post_text`).

**Harde grens: 2200 tekens** (`IG_CAPTION_MAX` in `ai_processor.py`). Dit is geen
richtlijn maar een muur: gaat de caption erover, dan weigert de Graph API de héle
post met `The caption was too long` — er wordt niets afgekapt. Voor een losse post
is dat ruim voldoende, maar de digest stapelt hooks en zit er vanaf ~9 artikelen
overheen. Elk vast blok dat je aan de caption toevoegt gaat van die 2200 af, en
verlaagt dus het aantal artikelen dat in een digest past. Zie fase 8.

De AI-disclosureregel in de caption is in fase 8 geschrapt: `instagram_image.py`
zet al een zichtbaar `AI-gegenereerd`-label op het fotodeel én XMP-metadata
waarmee Meta z'n eigen AI-info-label toont (fase 2). In de caption was het een
derde vermelding — en in een digest van 8 beelden stond die er bovendien in het
enkelvoud.

---

## Fase 5 — Activeren & nazorg

1. [x] `--dry-run`: caption + gecomposeerd beeld lokaal bekijken (beeld naar `tmp/`).
2. [x] Eén handmatige testpost naar het (nog lege) account:
       https://www.instagram.com/p/Daz2JcSjgfD/ — crop, witte balk, wordmark en
       caption-afbreking zien er goed uit. **AI-info-label NIET zichtbaar** — zie
       bekende bug hieronder; deze post staat zonder XMP-metadata (kan niet
       verwijderd worden via de API, dus blijft zo staan).

   > **Bug (2026-07-15, opgelost):** `compose_instagram_image()`s XMP-blok
   > (`Iptc4xmpExt:DigitalSourceType`) liet de WordPress media-upload
   > (`wp-json/wp/v2/media`) crashen met een 500 — vermoedelijk PHP-Imagick dat
   > vastloopt op het XMP-profiel tijdens thumbnail-regeneratie. Gereproduceerd via
   > bisectie: zelfs een lege `<x:xmpmeta xmlns:x="adobe:ns:meta/"></x:xmpmeta>`
   > triggerde het al; platte tekst in het xmp-veld werkte wel.
   >
   > **Echte fix (2026-07-15):** het Instagram-beeld gaat niet meer via de WP
   > media library. Nieuwe publieke image-host **los van WordPress**:
   > `ig-media.gebbink.nl` → nginx:alpine static server op **meterkast**
   > (`192.168.2.56`, compose-group `ig-media`), TLS via de bestaande Caddy
   > (`192.168.2.40`). `social_poster._publish_image_publicly()` scp't het
   > gecomponeerde beeld naar `/mnt/data/containers/ig-media/html/` (SSH-alias
   > `meterkast`) en ruimt bij elke upload bestanden ouder dan
   > `INSTAGRAM_MEDIA_RETENTION_DAYS` (default 2) op — Meta haalt het beeld toch
   > maar één keer op, bij het aanmaken van de media container.
   > `_XMP_METADATA_ENABLED` staat weer op `True` in `instagram_image.py`.
   > Geen nieuwe router-poort nodig: 80/443 stonden al open naar Caddy (zelfde
   > pad als `garage.gebbink.nl`/`recorder.gebbink.nl`).
   >
   > **Resterend:** publieke DNS A-record `ig-media.gebbink.nl → 82.169.132.41`
   > toevoegen (het wildcard-record voor `*.gebbink.nl` wijst naar oracle-web,
   > dus dit domein heeft een eigen record nodig, zoals ook `garage`/`recorder`
   > dat hebben). Caddy staat al klaar en probeert het Let's Encrypt-certificaat
   > automatisch op te halen zodra de DNS live is (geen verdere actie nodig na
   > het toevoegen van het record).
3. [ ] Profiel invullen (avatar, bio, link) — eventueel eerste post = introductiepost.
4. [x] `ENABLE_INSTAGRAM_POSTING=true` in `.env`.
5. [ ] `daily_digest.py`: Instagram-regel toevoegen (aantal posts vandaag), naast de
       bestaande Bluesky-stats. *(mag later)*
6. [x] CLAUDE.md bijwerken: Instagram-digest job in de scheduling-sectie.

---

## Fase 6 — Dagelijkse digest (batching, 2026-07-22)

**Aanleiding:** lage volgersaantallen rechtvaardigen geen 5 losse Instagram-posts
per dag (één per `main.py`-run). Nieuwe flow: verzamelen overdag, bundelen 's avonds.

- `ai_processor.ProcessedArticle` kreeg een `ig_tekst`-veld (de losse hook, náást de
  al samengestelde `ig_caption`) zodat een dagcaption meerdere hooks kan combineren
  zonder de link/disclosure/hashtag-blokken te dupliceren
  (`ai_processor.build_combined_ig_caption()` — hernoemd in fase 7 toen de
  wekelijkse Reel 'm ook ging gebruiken).
- `social_poster.post_to_instagram()` is vervangen door
  `social_poster.queue_instagram_post()`: componeert en host het beeld zoals
  voorheen, maar post niet — schrijft een entry naar `instagram_queue.json`
  (pad in `config.INSTAGRAM_QUEUE_FILE`, gitignored, zelfde stijl als
  `approval_tokens.json`).
- Nieuw script `instagram_digest.py`, cron 19:45 CET (`scheduler.py`, ruim ná het
  laatste artikel-slot van 07:00–19:00 en vóór `daily_digest.py` om 20:00): leest de
  wachtrij, bouwt de gecombineerde caption, post 1 beeld los of 2+ als carousel
  (`social_poster.post_instagram_digest()`, Graph API-limiet 10 items/carousel), en
  leegt de wachtrij bij succes. Wachtrij blijft staan bij een mislukte post, voor een
  volgende poging. → *Dat laatste bleek een voetangel: zie fase 8.*
- `approval_server.py` decline haalt het artikel eerst uit de wachtrij
  (`remove_from_instagram_queue`) zodat een gedeclineerd artikel nooit meepost. Dat
  werkt alleen vóór 19:45 — de decline-tokens verlopen sowieso al na 4 uur
  (`approval_store.TTL_HOURS`), dus in de praktijk is dit vrijwel altijd op tijd.
- **Bekende beperking:** staat een artikel eenmaal in een gepubliceerde
  (carousel-)post, dan kan een los item daar niet meer uit via de API — net zomin
  als voorheen een hele post verwijderd kon worden. De decline-pagina herinnert dan
  alleen nog aan handmatig verwijderen (van de hele post, incl. de andere
  artikelen erin).

---

## Fase 7 — Wekelijkse Reel (2026-07-22)

**Aanleiding:** feedposts (ook de digest) worden bij een klein aantal volgers
nauwelijks aan niet-volgers getoond — Reels zijn Instagrams belangrijkste
ontdekkingskanaal. Bewust **minimale** versie: silent (geen audio — Meta's
muziekbibliotheek is alleen via de app te koppelen, niet via de Graph API; een
zelf-gehoste track brengt licentievragen mee) en **wekelijks** (niet dagelijks,
om niet weer in de "te veel posts"-val van fase 6 te lopen), statische slides
met harde cuts (geen Ken Burns-zoom — kan later als polish).

- `instagram_image.compose_instagram_image()` accepteert nu optionele
  `canvas_w`/`canvas_h` (was hardcoded op 1080×1350). De witte balk-hoogte is al
  content-based (niet een vast % van de canvas), dus 9:16 (1080×1920) hergebruikt
  dezelfde compositielogica — bij een hogere canvas is er gewoon meer foto
  zichtbaar boven de balk. Geverifieerd met een echte render (zie git-log).
- `wordpress_client.fetch_posts_for_reel(days=7)`: haalt via `_embed=wp:featuredmedia`
  het laatst-gepubliceerde artikel van elke dag op (titel + featured-image URL) —
  onafhankelijk van `instagram_queue.json`, dat elke avond al geleegd wordt.
- Nieuwe module `instagram_reel.py` (puur ffmpeg via subprocess, geen netwerk):
  `build_reel_video()` plakt de 9:16-slides aaneen tot een silent MP4 (concat-filter,
  harde cuts, standaard 3s/slide @ 30fps). ffmpeg stond al op de machine.
  `weekly_reel.py` composeert per artikel een slide met de weekdag (NL, hardcoded
  lijst — locale op de machine is niet nl_NL) als kicker i.p.v. nogmaals het wordmark.
- `social_poster.py`: `publish_video_publicly()` (scp naar dezelfde ig-media-host,
  eigen `reel-*.mp4`-prefix zodat de cleanup-glob's elkaar niet raken),
  `post_instagram_reel()` (Reel-container via `media_type=REELS`+`video_url`,
  hergebruikt de bestaande container/poll/publish-flow maar met een ruimere
  polltijd — `_ig_wait_finished()` kreeg optionele `attempts`/`delay`-params,
  video-encoding aan Meta's kant duurt langer dan een los beeld).
- `ai_processor.build_daily_ig_caption()` hernoemd naar `build_combined_ig_caption()`
  (nu 2 callers: dagdigest én weekly reel — de oude naam was een dagdigest-only naam
  geworden).
- Nieuw script `weekly_reel.py`, cron zondag 19:00 CET (`scheduler.py`, vóór de
  gewone 19:45-digest en het 20:00-dagoverzicht). Slaat over bij < 2 artikelen
  die week (geen zinvolle slideshow).
- **Getest:** volledige pipeline lokaal gedraaid tegen live WP-data (8 artikelen,
  24s video, 1080x1920 h264, geen audiospoor) — zie renders in de sessie waarin dit
  gebouwd is. Nog niet als echte post naar Meta gestuurd (dat gebeurt voor het eerst
  bij de cron van komende zondag).

---

## Fase 8 — Captionlimiet & wachtrijbegrenzing (storing 24–26 juli 2026)

**Storing:** de digest van 24 juli faalde op een transient Graph API-fout
(`An unexpected error has occurred`). Conform fase 6 bleef de wachtrij staan voor
een nieuwe poging — maar die wachtrij groeit elke dag met ~5 artikelen, en vanaf
~9 hooks gaat de gecombineerde caption over de 2200 tekens. Vanaf 25 juli faalde
elke run dus op `The caption was too long`, en omdat falen de wachtrij liet staan,
werd de caption elke dag alleen maar langer. Een spiraal die zichzelf nooit meer
herstelt: ~2,5 dag geen Instagram-posts, wachtrij opgelopen tot 13 artikelen.

De retry-op-falen uit fase 6 was op zichzelf goed bedacht; wat ontbrak was een
bovengrens. Een wachtrij die alleen kan groeien, groeit tot voorbij elke limiet.

**Wijzigingen:**

- `ai_processor.fit_ig_entries(entries, max_items)` — geeft de grootste voorloop
  terug waarvan `build_combined_ig_caption()` binnen `IG_CAPTION_MAX` (2200) past.
  De digest snoeit dus vóóraf i.p.v. achteraf een 400 op te vangen. Bij een backlog
  worden de *nieuwste* artikelen gekozen (verse tech is relevanter), daarna terug op
  chronologische volgorde voor de nummering in de caption.
- **De wachtrij is een dagwachtrij, geen backlog.** Na een geslaagde digest gaat
  álles eruit wat bij het inlezen klaarstond, ook wat niet in de caption paste. Die
  artikelen staan gewoon op de site; ze een dag later alsnog als "nieuws" posten is
  slechter dan ze overslaan — en meeslepen is precies hoe het hierboven vastliep.
  Legen gebeurt per `post_id`, niet door het bestand te truncaten, zodat een
  `main.py`-run die tijdens het posten (~50s) iets toevoegt blijft staan.
- Alleen een *mislukte* digest laat de wachtrij staan, en dan nog maar 2 dagen
  (`_IG_MAX_AGE_DAYS`) — vangnet tegen dezelfde spiraal bij een reeks storingen.
- **Bug meegenomen:** het oude `entries[:10]` postte er 10 maar `clear_instagram_queue()`
  leegde de héle wachtrij — alles boven de carouselgrens verdween ongepost. Die
  helper is verwijderd.
- Caption-tekst: link-regel nu in het meervoud bij 2+ artikelen ("Lees de volledige
  artikelen…"), en de AI-disclosureregel is geschrapt (zie fase 4).

**Nazorg:** de vastgelopen wachtrij is handmatig afgewikkeld — 3 artikelen van
24 juli vervallen als te oud, 8 + 2 alsnog gepost in twee carousels. De cron van
19:45 op 26 juli is die avond eenmalig overgeslagen (crontab-regel uitgecommentarieerd;
`scheduler.py` zette 'm om 00:00 vanzelf terug).

---

## Volgorde & omvang

| Stap | Bestand(en) | Inschatting |
|---|---|---|
| 0. Meta-accountsetup | — (handmatig, Dennis) | 30–45 min |
| 1. Avatar + wordmark | `generate_instagram_assets.py` | klein |
| 2. Beeldcompositie | `instagram_image.py` | middelgroot (kern van de stijl) |
| 3. Posting + token | `social_poster.py`, `instagram_token.py`, `config.py` | middelgroot (port) |
| 4. Caption/kop | `ai_processor.py` | klein |
| 5. Approval-hook + rollout | `approval_server.py`, `.env`, docs | klein |

Fase 1, 2 en 4 kunnen volledig gebouwd en getest worden **zonder** dat de
Meta-accountsetup (fase 0) af is — alleen fase 3/5 hebben de tokens nodig.

## Open keuzes (defaults gekozen, terug te draaien)

- **4:5 staand** i.p.v. 1:1 vierkant — meer feedruimte, standaard bij nieuwsmedia;
  kost wel meer center-crop van het 16:9-bronbeeld.
- **Witte balk onderin** (Volkskrant) i.p.v. bovenin — foto krijgt de eerste blik.
- **WP media library** als publieke image-host i.p.v. aparte export-map op oracle-web.
- **Geen Stories/Reels** in v1 — alleen feedposts; carrousel en Stories kunnen later
  op dezelfde container-API.
