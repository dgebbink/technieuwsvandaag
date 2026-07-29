"""Genereert het TechNieuwsVandaag.nl-woordmerk rond het bestaande T-teken.

Draaien:  venv/bin/python3 assets/logo_src/make_logo.py
Schrijft de PNG's naar assets/ (logo-lockup*.png, logo-inline.png, logo-mark.png).

Alle maatverhoudingen zijn afgemeten aan assets/instagram_avatar.png (1000x1000,
drie vlakke kleuren) zodat een opnieuw gegenereerd logo exact hetzelfde teken
houdt als het Instagram-avatar en de favicon:

    witte balk : x 270-730 (461 breed), y 300-420 (121 hoog), hoekradius ~26
    cyaan stam : x 440-560 (121 breed), y 421-730

Er wordt op 4x gerenderd en daarna teruggeschaald: de ronde hoeken krijgen zo
dezelfde antialiasing-kwaliteit als de tekst.

Font: Montserrat ExtraBold (SIL Open Font License, zie OFL.txt hiernaast).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent

NAVY = (10, 22, 40, 255)
WHITE = (255, 255, 255, 255)
CYAN = (0, 212, 255, 255)

FONT = str(HERE / "Montserrat[wght].ttf")
WEIGHT = "ExtraBold"

SS = 4          # supersampling
TILE = 200      # tilehoogte in eindpixels

# verhoudingen t.o.v. de balkbreedte, overgenomen uit de avatar
BAR_H = 121 / 461
STEM_W = 121 / 461
MARK_H = 431 / 461
RADIUS = 26 / 461


def font_at(size):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_name(WEIGHT)
    return f


def fit_cap(target_cap):
    """Font waarvan de kaphoogte precies target_cap pixels is."""
    size = int(target_cap * 1.4)
    cap = cap_metrics(font_at(size))[0]
    return font_at(int(size * target_cap / cap))


def cap_metrics(font):
    """Hoogte van een kapitaal en de afstand van de tekst-origin tot de kaplijn."""
    _, top, _, bottom = font.getbbox("T")
    return bottom - top, top


def draw_mark(d, x, y, bar_w, bar_col, stem_col):
    """Tekent de T met de balk linksboven op (x, y)."""
    bar_h = bar_w * BAR_H
    stem_w = bar_w * STEM_W
    r = bar_w * RADIUS
    # De stam eerst, met de bovenkant een radius omhoog geschoven, en daarna de
    # balk eroverheen: zo sluit de stam vlak aan zoals in de avatar, i.p.v. met
    # ronde hoekjes of een cyaan inham in de balk.
    sx = x + (bar_w - stem_w) / 2
    d.rounded_rectangle([sx, y + bar_h - r, sx + stem_w, y + bar_w * MARK_H],
                        radius=r, fill=stem_col)
    d.rounded_rectangle([x, y, x + bar_w, y + bar_h], radius=r, fill=bar_col)


def draw_tile(d, x, y, size):
    """Navy tegel met de T erin, zoals het avatar maar met krappere marge."""
    d.rounded_rectangle([x, y, x + size, y + size],
                        radius=int(size * 0.22), fill=NAVY)
    bar_w = size * 0.62
    draw_mark(d, x + (size - bar_w) / 2, y + (size - bar_w * MARK_H) / 2,
              bar_w, WHITE, CYAN)


def save(img, name, scale=SS):
    img = img.resize((img.width // scale, img.height // scale), Image.LANCZOS)
    img.save(OUT / name)
    print(f"{name}  {img.width}x{img.height}  aspect {img.width / img.height:.1f}:1")


def build_lockup(name, tile=True, accent=True):
    """Teken links, naam op een regel ernaast."""
    S = TILE * SS
    pad = int(S * 0.10)

    font = fit_cap(S * 0.52)
    cap_h, cap_top = cap_metrics(font)
    tld = ".nl"
    tld_col = CYAN if accent else NAVY

    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    if tile:
        icon_w, gap, text = S, int(S * 0.26), "TechNieuwsVandaag"
    else:
        # de T zelf is het teken: even breed als de letter die hij vervangt
        icon_w = int(tmp.textlength("T", font=font))
        gap, text = int(S * 0.02), "echNieuwsVandaag"

    text_w = tmp.textlength(text, font=font)
    tld_w = tmp.textlength(tld, font=font)

    img = Image.new("RGBA",
                    (int(icon_w + gap + text_w + tld_w + pad * 2), S + pad * 2),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if tile:
        draw_tile(d, pad, pad, S)
    else:
        bar_w = cap_h / MARK_H
        draw_mark(d, pad + (icon_w - bar_w) / 2, pad + (S - cap_h) / 2,
                  bar_w, NAVY, CYAN)

    tx = pad + icon_w + gap
    ty = pad + (S - cap_h) / 2 - cap_top      # zet de kaplijn op de juiste hoogte
    d.text((tx, ty), text, font=font, fill=NAVY)
    d.text((tx + text_w, ty), tld, font=font, fill=tld_col)
    save(img, name)


def build_compact(name, accent=True):
    """Teken links, naam op twee regels ernaast.

    Een woordmerk van 20 tekens op een regel wordt ~9x zo breed als hoog; op
    twee regels is dat ~4x, wat naast het menu past in de 52px-hoge navbalk.
    """
    S = TILE * SS
    pad = int(S * 0.10)

    font = fit_cap(S * 0.40)
    cap_h, cap_top = cap_metrics(font)
    line1, line2, tld = "TechNieuws", "Vandaag", ".nl"

    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w1 = tmp.textlength(line1, font=font)
    w2 = tmp.textlength(line2, font=font)
    w_tld = tmp.textlength(tld, font=font)
    text_w = max(w1, w2 + w_tld)

    gap = int(S * 0.24)
    leading = cap_h * 1.28

    img = Image.new("RGBA", (int(S + gap + text_w + pad * 2), S + pad * 2),
                    (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    draw_tile(d, pad, pad, S)

    tx = pad + S + gap
    ty = pad + (S - (leading + cap_h)) / 2 - cap_top   # blok optisch centreren
    d.text((tx, ty), line1, font=font, fill=NAVY)
    d.text((tx, ty + leading), line2, font=font, fill=NAVY)
    d.text((tx + w2, ty + leading), tld, font=font,
           fill=CYAN if accent else NAVY)
    save(img, name)


def build_mark(name, size=512):
    """Losstaand teken zonder woordmerk (mobiele header, app-icoon)."""
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_tile(ImageDraw.Draw(img), 0, 0, S - 1)
    save(img, name)


if __name__ == "__main__":
    build_lockup("logo-lockup.png")
    build_lockup("logo-inline.png", tile=False)
    build_compact("logo-lockup-compact.png")
    build_mark("logo-mark.png")
