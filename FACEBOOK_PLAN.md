# Facebook Page-integratie — implementatieplan

**Status:** gepland, nog niet gebouwd. Ingeschat op 1–2 uur werk plus ~15 min
handmatige Meta-setup.

**Doel:** artikelen automatisch naar een Facebook-pagina posten, naast de
bestaande Bluesky- en Instagram-kanalen. Facebook past beter bij een
Nederlandstalig nieuwsformat dan Instagram: linkposts met een OG-preview zijn
daar het native formaat, en de doelgroep voor tech-nieuws in het Nederlands zit
er in de praktijk meer dan op Bluesky.

**Vertrekpunt is gunstiger dan bij Instagram.** Fase 0 van `INSTAGRAM_PLAN.md`
heeft het zware werk al gedaan: er ís een Meta Developer-app
(`FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` staan in `.env`) en er ís een
Facebook-pagina — een Instagram Business-account kan niet zonder gekoppelde
pagina bestaan. Deze integratie leunt op diezelfde app en pagina; er hoeft geen
tweede Meta-setup doorlopen te worden.

---

## Waarom dit eenvoudiger is dan Instagram

Het verschil bepaalt de hele opzet, dus het staat vooraan:

| | Instagram | Facebook |
|---|---|---|
| Beeld | verplicht; eigen compositie (`instagram_image.py`), publiek hosten op `ig-media.gebbink.nl` | geen; FB haalt de OG-tags van het WP-artikel op |
| Link | niet klikbaar → "link in bio" | klikbaar, is juist het hoofdbestanddeel |
| API-flow | container aanmaken → pollen tot `FINISHED` → publiceren | één `POST` |
| Frequentie | 1 digest/dag (5 losse posts is te veel bij weinig volgers) | per artikel, net als Bluesky |

Geen mediapijplijn, geen wachtrij, geen carousel-limiet, geen captionlimiet van
2200 tekens. Facebook is daarmee qua vorm veel dichter bij `post_to_bluesky()`
dan bij de Instagram-flow — en zo moet het ook gebouwd worden.

---

## Fase 0 — Meta-setup (handmatig, eenmalig, ~15 min)

1. [ ] Controleer welke pagina aan het IG Business-account hangt en noteer het
       Page-ID (Meta Business Suite → Instellingen, of
       `GET /me/accounts?access_token=…`).
2. [ ] Voeg de permissie `pages_manage_posts` toe aan de bestaande app en
       genereer een nieuw Page Access Token. `pages_read_engagement` is er al
       voor Instagram; posten vereist expliciet die extra scope.
3. [ ] Wissel om naar een **never-expiring** Page token — dezelfde route als
       voor `INSTAGRAM_ACCESS_TOKEN` (long-lived user token → `/me/accounts`).
       Een token dat na 60 dagen omvalt is voor een cron-pijplijn onbruikbaar.
4. [ ] Zet in `.env`: `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_ACCESS_TOKEN`,
       `ENABLE_FACEBOOK_POSTING=false` (aan het eind pas op `true`).

> **Mogelijke afkorting:** als het bestaande `INSTAGRAM_ACCESS_TOKEN` al een
> Page token van dezelfde pagina is, dekt het na toevoeging van
> `pages_manage_posts` mogelijk beide kanalen. Toch een eigen variabele
> aanhouden — één token voor twee kanalen betekent dat een intrekking beide
> tegelijk sloopt, en dat is precies het soort koppeling dat je bij een
> storing niet wilt uitzoeken.

---

## Fase 1 — `post_to_facebook()` in `social_poster.py`

Naast `post_to_bluesky()` (regel 371) en met exact hetzelfde contract:
retourneert een identifier bij succes, `""` bij falen, **raist nooit** — een
mislukt sociaal kanaal mag de pijplijn niet omvertrekken.

```
POST https://graph.facebook.com/{FACEBOOK_API_VERSION}/{page_id}/feed
  message=...        # de tekst
  link=...           # de WP-artikel-URL; FB haalt titel/beeld uit de OG-tags
  access_token=...
→ {"id": "{page-id}_{post-id}"}
```

- Hergebruik `FACEBOOK_APP_ID`/`FACEBOOK_APP_SECRET` niet voor de post zelf —
  die zijn alleen voor tokenbeheer.
- API-versie via `INSTAGRAM_API_VERSION` (nu `v19.0`) of een eigen
  `FACEBOOK_API_VERSION`; het is dezelfde Graph API.
- Permalink terug te geven als `https://www.facebook.com/{post_id}`.

### Berichttekst

Geen aparte AI-call. Facebook toont de titel en het beeld al via de OG-preview,
dus de tekst eróver moet niet dezelfde titel herhalen. Hergebruik `ig_tekst` —
de hook + context die er voor Instagram tóch al is — en laat de hashtags weg of
beperk ze tot twee. Hashtags doen op Facebook nauwelijks iets en ogen er
sneller als spam dan op Instagram.

Er geldt geen praktische tekenlimiet (63.206), maar alles boven ~400 tekens
wordt ingeklapt achter "Meer weergeven".

---

## Fase 2 — Aanhaken in de flows

`post_to_bluesky()` wordt op twee plekken aangeroepen; Facebook moet op
allebei mee, anders post het kanaal alleen bij een deel van de artikelen:

1. `main.py:356` — de directe pijplijn.
2. `social_poster.post_articles_to_social()` (regel 912) — de approval-flow,
   aangeroepen vanuit `approval_server.py` na goedkeuring.

Achter `ENABLE_FACEBOOK_POSTING`, zodat het kanaal los aan/uit kan zonder
Bluesky of Instagram te raken. De `BLUESKY_POST_DELAY_SECONDS`-wachttijd
hoeft niet herhaald te worden: die staat er om WordPress de kans te geven het
artikel te renderen vóór de eerste crawler langskomt, en na de Bluesky-post is
die tijd al verstreken. Facebook crawlt de OG-tags op het moment van posten,
dus de volgorde Bluesky → Facebook is de veilige.

**Decline-afhandeling:** `approval_store` bewaart al een Bluesky-URI en een
Instagram-permalink per token (`update_bluesky_uri`,
`update_instagram_permalink`). Voeg hetzelfde toe voor de Facebook-post-ID,
zodat de decline-pagina kan vertellen wat er handmatig weg moet. In
tegenstelling tot een Instagram-carousel is een losse FB-post wél via de API
te verwijderen (`DELETE /{post-id}`) — dat is een logische uitbreiding zodra
het posten staat, maar niet nodig voor de eerste versie.

---

## Fase 3 — Testen & activeren

1. [ ] `--dry-run`: berichttekst loggen zonder te posten (zelfde patroon als
       de andere kanalen).
2. [ ] Eén echte post naar de pagina met een bestaand artikel; controleer of de
       OG-preview klopt (titel, beeld, domein). Gaat dit mis, dan ligt het aan
       de WP-kant — controleer de OG-tags in `tnv-news` en draai de URL door de
       [Sharing Debugger](https://developers.facebook.com/tools/debug/) om
       Facebooks cache te legen.
3. [ ] `ENABLE_FACEBOOK_POSTING=true`, één dag meedraaien, logs nakijken.
4. [ ] CLAUDE.md bijwerken: Facebook noemen bij de sociale kanalen.

---

## Aandachtspunten

- **Tokenverloop is de klassieke faalmodus.** Zie fase 8 van
  `INSTAGRAM_PLAN.md`: een sociaal kanaal dat stilletjes faalt, valt pas dagen
  later op. Neem de Facebook-post mee in `daily_digest.py`, zodat een reeks
  mislukkingen zichtbaar wordt zonder dat je de logs hoeft te openen.
- **Meta-app in Development mode** post alleen naar pagina's waar het
  app-account beheerder van is. Voor één eigen pagina is dat genoeg — App
  Review is niet nodig, wat de setup aanzienlijk korter maakt dan hij op het
  eerste gezicht lijkt.
- **Geen dubbele posts bij herpublicatie.** `posted_urls.txt` beschermt de
  scrape-kant, niet de post-kant; de approval-flow kan een artikel in theorie
  twee keer langs `post_articles_to_social()` sturen. Bij Bluesky is dat nu
  ook zo — geen reden om het voor Facebook op te lossen, wél om het te weten.

---

## Openstaande keuze

**Per artikel of een dagbundel?** Dit plan gaat uit van per artikel, zoals
Bluesky. De reden dat Instagram een dagdigest kreeg — 5 losse posts per dag
zijn te veel voor een klein account — geldt op Facebook minder: de feed is
chronologisch-met-ranking en linkposts concurreren daar niet met elkaar zoals
5 losse beeldposts in een Instagram-profielraster. Blijkt het in de praktijk
toch te veel, dan is een `facebook_digest.py` naar het model van
`instagram_digest.py` de terugvaloptie — met de kanttekening dat een bundel
maar één klikbare link kan hebben, wat het grootste voordeel van het kanaal
juist wegneemt.
