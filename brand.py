"""
Merkidentiteit: het T-teken, het woordmerk en alle afgeleide beelden.

Eén bron voor het merk. `instagram_image.py` haalt hier het woordmerk vandaan
voor de postbalk en de Reel-kaarten, en `main()` schrijft de complete set
statische assets naar `assets/brand/`. Wie het logo wil wijzigen doet dat hier
en draait het script opnieuw — niet in een beeldbewerker, anders lopen site,
video en socials uit elkaar.

Het teken is vectorieel opgebouwd uit de verhoudingen van het oorspronkelijke
`assets/instagram_avatar.png` (1000x1000, drie vlakke kleuren), zodat een
opnieuw gegenereerd logo exact hetzelfde merk houdt als avatar en favicon:

    witte balk : x 270-730 (461 breed), y 300-420 (121 hoog), hoekradius ~26
    cyaan stam : x 440-560 (121 breed), y 421-730

Font: Montserrat ExtraBold (SIL OFL, zie assets/fonts/OFL.txt).

Gebruik:
    venv/bin/python3 brand.py            # (her)genereert assets/brand/
"""
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import BASE_DIR

logger = logging.getLogger(__name__)

ASSETS_DIR = BASE_DIR / "assets"
BRAND_DIR = ASSETS_DIR / "brand"
FONT_PATH = str(ASSETS_DIR / "fonts" / "Montserrat[wght].ttf")

# Merkkleuren — het palet van site, Bluesky en Instagram
NAVY = (10, 22, 40)       # #0A1628
CYAN = (0, 212, 255)      # #00D4FF
WHITE = (255, 255, 255)
# Iets lichter dan NAVY: voor watermerk en rasterlijnen op donkere vlakken.
# Bewust laag contrast — het mag de inhoud die eroverheen komt niet storen.
NAVY_SOFT = (22, 38, 62)
NAVY_DEEP = (5, 12, 24)   # onderkant van het verloop

NAME = "TechNieuwsVandaag"
TLD = ".nl"
TAGLINE = "aangedreven door AI"

SS = 4            # supersampling voor ronde hoeken

# verhoudingen t.o.v. de breedte van de dwarsbalk, afgemeten aan de avatar
BAR_H = 121 / 461
STEM_W = 121 / 461
MARK_H = 431 / 461
RADIUS = 26 / 461


# --------------------------------------------------------------------------
# typografie
# --------------------------------------------------------------------------

def font_at(size: int, weight: str = "ExtraBold") -> ImageFont.FreeTypeFont:
    """Montserrat op de gevraagde grootte. Raist als het font ontbreekt —
    geen stille fallback, het merk moet er overal identiek uitzien."""
    f = ImageFont.truetype(FONT_PATH, max(1, size))
    f.set_variation_by_name(weight)
    return f


def cap_metrics(font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    """Kapitaalhoogte en de afstand van de tekst-origin tot de kaplijn."""
    _, top, _, bottom = font.getbbox("T")
    return bottom - top, top


def fit_cap(target_cap: float, weight: str = "ExtraBold") -> ImageFont.FreeTypeFont:
    """Font waarvan de kapitaalhoogte precies target_cap pixels is."""
    probe = max(2, int(target_cap * 1.4))
    cap = cap_metrics(font_at(probe, weight))[0]
    return font_at(max(2, round(probe * target_cap / cap)), weight)


# --------------------------------------------------------------------------
# het teken
# --------------------------------------------------------------------------

def draw_mark(d: ImageDraw.ImageDraw, x: float, y: float, bar_w: float,
              bar_col: tuple, stem_col: tuple) -> None:
    """Tekent de T met de dwarsbalk linksboven op (x, y)."""
    bar_h = bar_w * BAR_H
    stem_w = bar_w * STEM_W
    r = bar_w * RADIUS
    # Stam eerst, met de bovenkant een radius omhoog geschoven, en dan de balk
    # eroverheen: zo sluit de stam vlak aan zoals in de avatar, in plaats van
    # met ronde hoekjes of een inham in de balk.
    sx = x + (bar_w - stem_w) / 2
    d.rounded_rectangle([sx, y + bar_h - r, sx + stem_w, y + bar_w * MARK_H],
                        radius=r, fill=stem_col)
    d.rounded_rectangle([x, y, x + bar_w, y + bar_h], radius=r, fill=bar_col)


def draw_tile(d: ImageDraw.ImageDraw, x: float, y: float, size: float) -> None:
    """Navy tegel met de T erin — krappere marge dan de avatar, die zijn ruime
    marge heeft om Instagrams cirkelcrop te overleven."""
    d.rounded_rectangle([x, y, x + size, y + size],
                        radius=size * 0.22, fill=NAVY)
    bar_w = size * 0.62
    draw_mark(d, x + (size - bar_w) / 2, y + (size - bar_w * MARK_H) / 2,
              bar_w, WHITE, CYAN)


def render_mark(size: int, tile: bool = True) -> Image.Image:
    """Het losse teken als RGBA-vierkant; zonder tegel alleen de T zelf."""
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if tile:
        draw_tile(d, 0, 0, S - 1)
    else:
        bar_w = S * 0.92
        draw_mark(d, (S - bar_w) / 2, (S - bar_w * MARK_H) / 2,
                  bar_w, WHITE, CYAN)
    return img.resize((size, size), Image.LANCZOS)


# --------------------------------------------------------------------------
# het woordmerk
# --------------------------------------------------------------------------

def render_lockup(height: int, form: str = "inline", on_dark: bool = False,
                  accent: bool = True) -> Image.Image:
    """Het woordmerk als transparante RGBA-image van exact `height` hoog.

    form="tile"    tegel + naam op één regel      (~8.9:1) — breed, veel presence
    form="compact" tegel + naam op twee regels    (~4.3:1) — de site-header
    form="inline"  geen tegel, de T is de eerste letter (~7.9:1) — op foto's en
                   gekleurde vlakken, waar een navy tegel een gat zou slaan

    on_dark zet tekst en dwarsbalk in wit; de stam blijft cyaan.
    """
    if form not in ("tile", "compact", "inline"):
        raise ValueError(f"onbekende vorm: {form}")

    text_col = WHITE if on_dark else NAVY
    tld_col = CYAN if accent else text_col
    S = max(SS * 8, height * SS)          # werkhoogte van het teken
    pad = round(S * 0.10)

    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    if form == "compact":
        font = fit_cap(S * 0.40)
        cap_h, cap_top = cap_metrics(font)
        line1, line2 = "TechNieuws", "Vandaag"
        w1 = tmp.textlength(line1, font=font)
        w2 = tmp.textlength(line2, font=font)
        w_tld = tmp.textlength(TLD, font=font)
        text_w = max(w1, w2 + w_tld)
        gap = round(S * 0.24)
        leading = cap_h * 1.28

        img = Image.new("RGBA", (round(S + gap + text_w + pad * 2), S + pad * 2),
                        (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        draw_tile(d, pad, pad, S)
        tx = pad + S + gap
        ty = pad + (S - (leading + cap_h)) / 2 - cap_top   # blok optisch centreren
        d.text((tx, ty), line1, font=font, fill=text_col)
        d.text((tx, ty + leading), line2, font=font, fill=text_col)
        d.text((tx + w2, ty + leading), TLD, font=font, fill=tld_col)
    else:
        font = fit_cap(S * 0.52)
        cap_h, cap_top = cap_metrics(font)
        if form == "tile":
            icon_w, gap, text = S, round(S * 0.26), NAME
        else:
            # de getekende T vervangt de eerste letter en krijgt haar advance
            icon_w, gap, text = round(tmp.textlength("T", font=font)), round(S * 0.02), NAME[1:]

        text_w = tmp.textlength(text, font=font)
        tld_w = tmp.textlength(TLD, font=font)

        img = Image.new("RGBA",
                        (round(icon_w + gap + text_w + tld_w + pad * 2), S + pad * 2),
                        (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if form == "tile":
            draw_tile(d, pad, pad, S)
        else:
            bar_w = cap_h / MARK_H
            draw_mark(d, pad + (icon_w - bar_w) / 2, pad + (S - cap_h) / 2,
                      bar_w, text_col, CYAN)
        tx = pad + icon_w + gap
        ty = pad + (S - cap_h) / 2 - cap_top       # kaplijn op de juiste hoogte
        d.text((tx, ty), text, font=font, fill=text_col)
        d.text((tx + text_w, ty), TLD, font=font, fill=tld_col)

    img = img.crop(img.getbbox())
    return img.resize((max(1, round(img.width * height / img.height)), height),
                      Image.LANCZOS)


def paste_lockup_by_width(canvas: Image.Image, width: int, form: str = "inline",
                          on_dark: bool = True) -> Image.Image:
    """Woordmerk op een doelbreedte i.p.v. -hoogte (render_lockup schaalt op
    hoogte, maar in layouts is de breedte de bindende maat)."""
    probe = render_lockup(height=200, form=form, on_dark=on_dark)
    return probe.resize((width, max(1, round(probe.height * width / probe.width))),
                        Image.LANCZOS)


# --------------------------------------------------------------------------
# achtergronden
# --------------------------------------------------------------------------

def _gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    """Verticaal verloop. Zonder numpy: klein renderen en opschalen — een
    verloop bevat geen detail, dus bicubisch opschalen is verliesloos genoeg."""
    strip = Image.new("RGB", (1, 256))
    px = strip.load()
    for i in range(256):
        t = i / 255
        px[0, i] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return strip.resize((w, h), Image.BICUBIC)


def _watermark(canvas: Image.Image, height_frac: float = 0.92,
               bleed: float = 0.30, opacity: float = 0.55) -> None:
    """Groot, laag-contrast T-teken als textuur tegen de rechterrand.

    Het teken loopt bewust `bleed` van zijn breedte het kader uit: zo leest het
    als patroon en niet als een tweede logo dat met het woordmerk concurreert.
    De hoogte wordt uitgerekend uit de vorm zelf — bij een vast canvasformaat
    liep de T anders volledig buiten beeld en bleef er een anonieme afgeronde
    rechthoek over.
    """
    w, h = canvas.size
    # min(w, h) als maatstaf: op staande formaten (story) zou een op de hoogte
    # geschaalde T breder worden dan het doek en volledig weglopen
    size = round(min(w, h) * height_frac / (0.92 * MARK_H))
    mark = render_mark(size, tile=False)
    tinted = Image.new("RGBA", mark.size, (*NAVY_SOFT, 0))
    tinted.putalpha(mark.split()[3].point(lambda a: round(a * opacity)))
    canvas.alpha_composite(tinted, (w - round(size * (1 - bleed)),
                                    (h - mark.height) // 2))


def _grid(canvas: Image.Image, step_frac: float = 0.055) -> None:
    """Fijn rasterpatroon — geeft een vlak vlak wat tech-textuur zonder ruis."""
    w, h = canvas.size
    step = max(12, round(min(w, h) * step_frac))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(*NAVY_SOFT, 110), width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(*NAVY_SOFT, 110), width=1)
    canvas.alpha_composite(layer)


def background(w: int, h: int, style: str = "dark") -> Image.Image:
    """Kaal merkvlak van w x h — de basis onder banners en postachtergronden.

    dark      vlak navy met een groot, zacht T-watermerk
    gradient  navy verloop met het watermerk
    grid      navy met fijn raster
    light     wit met een lichtgrijs raster, voor donkere tekst erop
    """
    if style == "light":
        canvas = Image.new("RGBA", (w, h), (*WHITE, 255))
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        step = max(12, round(min(w, h) * 0.055))
        for x in range(0, w, step):
            d.line([(x, 0), (x, h)], fill=(*NAVY, 16), width=1)
        for y in range(0, h, step):
            d.line([(0, y), (w, y)], fill=(*NAVY, 16), width=1)
        canvas.alpha_composite(layer)
        return canvas

    if style == "gradient":
        canvas = _gradient(w, h, NAVY, NAVY_DEEP).convert("RGBA")
        _watermark(canvas)
    elif style == "grid":
        canvas = Image.new("RGBA", (w, h), (*NAVY, 255))
        _grid(canvas)
    else:
        canvas = Image.new("RGBA", (w, h), (*NAVY, 255))
        _watermark(canvas)
    return canvas


def accent_bar(canvas: Image.Image, thickness_frac: float = 0.012,
               edge: str = "bottom") -> None:
    """Cyaan accentrand — hetzelfde signaal als de rode rand onder de site-header."""
    w, h = canvas.size
    t = max(3, round(h * thickness_frac))
    d = ImageDraw.Draw(canvas)
    if edge == "bottom":
        d.rectangle([0, h - t, w, h], fill=(*CYAN, 255))
    else:
        d.rectangle([0, 0, w, t], fill=(*CYAN, 255))


def banner(w: int, h: int, style: str = "dark", logo_frac: float = 0.62,
           tagline: bool = True, center: bool = True,
           safe: tuple[int, int] | None = None) -> Image.Image:
    """Profielbanner: achtergrond + woordmerk (+ tagline) binnen de veilige zone.

    `safe` beperkt het logo tot een (breedte, hoogte)-venster midden in het
    doek. YouTube en X snijden banners per apparaat anders af; alleen dat
    venster is overal zichtbaar.
    """
    canvas = background(w, h, style)
    on_dark = style != "light"
    safe_w, safe_h = safe if safe else (w, h)

    logo_w = min(round(safe_w * logo_frac), round(w * 0.62))
    logo = paste_lockup_by_width(canvas, logo_w, form="inline", on_dark=on_dark)

    block_h = logo.height
    tag_font = tag_y = None
    if tagline:
        tag_font = fit_cap(logo.height * 0.30, weight="SemiBold")
        block_h += round(logo.height * 0.62)

    x = (w - logo.width) // 2 if center else round((w - safe_w) / 2 + safe_w * 0.06)
    y = (h - block_h) // 2
    if block_h > safe_h:                       # nooit buiten de veilige zone
        y = (h - safe_h) // 2
    canvas.alpha_composite(logo, (x, y))

    if tagline:
        d = ImageDraw.Draw(canvas)
        cap_h, cap_top = cap_metrics(tag_font)
        tag_y = y + logo.height + round(logo.height * 0.30) - cap_top
        col = (*CYAN, 255) if on_dark else (0, 132, 168, 255)
        tw = d.textlength(TAGLINE, font=tag_font)
        tx = x + (logo.width - tw) / 2 if center else x
        d.text((tx, tag_y), TAGLINE, font=tag_font, fill=col)

    accent_bar(canvas)
    return canvas


def post_background(w: int, h: int, style: str = "dark") -> Image.Image:
    """Postachtergrond: merkvlak met het woordmerk klein onderin, zodat het
    midden vrij blijft voor eigen tekst, screenshot of foto."""
    canvas = background(w, h, style)
    on_dark = style != "light"
    logo = paste_lockup_by_width(canvas, round(w * 0.42), form="inline",
                                 on_dark=on_dark)
    margin = round(min(w, h) * 0.07)
    canvas.alpha_composite(logo, ((w - logo.width) // 2,
                                  h - margin - logo.height - round(h * 0.012)))
    accent_bar(canvas)
    return canvas


# --------------------------------------------------------------------------
# de set
# --------------------------------------------------------------------------

# (bestandsnaam, breedte, hoogte, veilige zone of None)
BANNERS = [
    ("banner-bluesky-3000x1000.png", 3000, 1000, None),
    ("banner-x-1500x500.png", 1500, 500, (1200, 380)),
    ("banner-linkedin-1584x396.png", 1584, 396, (1200, 330)),
    ("banner-facebook-1640x624.png", 1640, 624, (1200, 480)),
    ("banner-youtube-2560x1440.png", 2560, 1440, (1546, 423)),
    ("share-og-1200x630.png", 1200, 630, None),
    ("email-header-1200x300.png", 1200, 300, None),
]

POST_SIZES = [
    ("ig-post-1080x1080", 1080, 1080),
    ("ig-portrait-1080x1350", 1080, 1350),
    ("ig-story-1080x1920", 1080, 1920),
]
POST_STYLES = ("dark", "gradient", "grid", "light")

ICON_SIZES = (1024, 512, 192, 180, 32)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    written = 0

    def save(img: Image.Image, name: str) -> None:
        nonlocal written
        img.convert("RGBA").save(BRAND_DIR / name, "PNG")
        written += 1
        logger.info("%-38s %dx%d", name, img.width, img.height)

    # logo's — transparant, voor licht én donker
    for form, name in (("tile", "logo-lockup"), ("compact", "logo-lockup-compact"),
                       ("inline", "logo-inline")):
        save(render_lockup(240, form=form), f"{name}.png")
        save(render_lockup(240, form=form, on_dark=True), f"{name}-wit.png")

    # het losse teken
    for size in ICON_SIZES:
        save(render_mark(size), f"logo-mark-{size}.png")
    save(render_mark(512), "logo-mark.png")

    # profielbanners
    for name, w, h, safe in BANNERS:
        save(banner(w, h, style="gradient", safe=safe), name)

    # postachtergronden om zelf inhoud op te zetten
    for name, w, h in POST_SIZES:
        for style in POST_STYLES:
            save(post_background(w, h, style=style), f"{name}-{style}.png")

    logger.info("Klaar: %d bestanden in %s", written, BRAND_DIR)


if __name__ == "__main__":
    main()
