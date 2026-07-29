# Merkbeelden

Alles hier wordt gegenereerd door `brand.py` in de projectroot:

```bash
venv/bin/python3 brand.py
```

Niets in deze map met de hand bijwerken — de volgende run overschrijft het.
Wil je het logo wijzigen, pas dan `brand.py` aan en draai het opnieuw. Zo
blijven site, Reel en socials hetzelfde merk tonen.

## Logo's (transparant PNG, 240px hoog)

| Bestand | Vorm | Waar |
|---|---|---|
| `logo-lockup.png` | tegel + naam op één regel (8.9:1) | brede koppen, presentaties |
| `logo-lockup-compact.png` | tegel + naam op twee regels (4.3:1) | **de site-header** |
| `logo-inline.png` | geen tegel, de T ís de eerste letter (7.9:1) | op foto's en gekleurde vlakken |

Elk logo heeft een `-wit.png`-variant voor donkere ondergronden. De gewone
variant is navy en werkt alleen op licht.

## Teken

`logo-mark-{1024,512,192,180,32}.png` — de losse T-tegel, vierkant. Voor
app-iconen, avatars en plekken waar de naam niet past. `logo-mark.png` is een
kopie van de 512px-versie.

De favicon van de site staat los: die komt uit `../instagram_avatar.png`, met
ruimere marge omdat Instagram er een cirkel uit snijdt.

## Profielbanners

Klaar voor upload, logo binnen de veilige zone van elk platform:

`banner-bluesky-3000x1000` · `banner-x-1500x500` · `banner-linkedin-1584x396` ·
`banner-facebook-1640x624` · `banner-youtube-2560x1440` · `share-og-1200x630`
(social preview van de site) · `email-header-1200x300`

Bij YouTube en X is het logo klein t.o.v. het doek. Dat is opzet: die
platforms snijden de banner per apparaat anders af en alleen het midden is
overal zichtbaar.

## Achtergronden om zelf op te werken

`ig-post-1080x1080` (feed), `ig-portrait-1080x1350` (feed, staand) en
`ig-story-1080x1920` (story/Reel), elk in vier stijlen:

- `dark` — vlak navy met een groot, zacht T-watermerk
- `gradient` — navy verloop met hetzelfde watermerk
- `grid` — navy met een fijn raster
- `light` — wit met lichtgrijs raster, voor donkere tekst

Het midden is bewust leeg; het woordmerk staat onderin en de cyaan accentrand
onderaan. Zet er je eigen tekst, screenshot of foto overheen.
