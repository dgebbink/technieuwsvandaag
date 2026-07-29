"""
Instagram-postbeeld componeren in Volkskrant-stijl.

Neemt de bestaande (FAL.ai-gegenereerde) artikelafbeelding en bouwt er een
1080x1350 (4:5) feedbeeld van: foto full-bleed, witte balk onderin met kicker,
korte kop en het site-wordmark, plus een 'AI-gegenereerd'-label op het fotodeel.

De JPEG krijgt IPTC/XMP-metadata `DigitalSourceType=trainedAlgorithmicMedia`
mee — Meta leest die en toont automatisch het officiele "AI-info"-label; de
Graph API zelf heeft geen AI-vlag-parameter.

Puur PIL, geen netwerk. Handmatig testen:
    venv/bin/python3 instagram_image.py <bron.jpg> "<kop>" "<kicker>" <doel.jpg>
"""
import logging

from PIL import Image, ImageDraw, ImageFont

from brand import CYAN, NAVY, WHITE
from brand import background as brand_background
from brand import fit_cap, font_at, render_lockup

logger = logging.getLogger(__name__)

CANVAS_W = 1080
CANVAS_H = 1350
MARGIN = 64

# Donkerder cyaan voor de kicker: het merkcyaan #00D4FF is op wit onleesbaar
KICKER_CYAN = (0, 132, 168)  # #0084A8

HEADLINE_MAX_LINES = 2
# Montserrat loopt smaller en heeft een kleinere kaphoogte per punt dan het
# DejaVu dat hier eerder stond; vandaar dat de reeks hoger begint. _fit_headline
# pakt de grootste maat die past, dus korte koppen worden vanzelf groter gezet.
HEADLINE_FONT_SIZES = (80, 76, 72, 68, 64, 60, 56, 52, 48, 44)

# Weer AAN (2026-07-15): de "bekende bug" uit INSTAGRAM_PLAN.md fase 5 was dat
# WordPress' media-upload crashte op dit XMP-blok (vermoedelijk PHP-Imagick dat
# vastloopt tijdens thumbnail-regeneratie). Fix: het beeld gaat niet meer via de
# WP media library — social_poster.py host het nu rechtstreeks op ig-media.gebbink.nl
# (nginx op meterkast), dus WP/Imagick raakt dit bestand nooit meer aan.
_XMP_METADATA_ENABLED = True

# IPTC NewsCodes: gesynthetiseerd beeld uit een generatief model
_XMP_AI_METADATA = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '   xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"\n'
    '   Iptc4xmpExt:DigitalSourceType='
    '"http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"/>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n'
    '<?xpacket end="w"?>'
).encode("utf-8")


def _cover_crop(img: Image.Image, width: int, height: int) -> Image.Image:
    """Schaal het beeld tot het (width, height) volledig vult en center-crop.

    Pre:  img heeft positieve afmetingen
    Post: RGB Image van exact (width, height)
    """
    img = img.convert("RGB")
    scale = max(width / img.width, height / img.height)
    new_w = round(img.width * scale)
    new_h = round(img.height * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return img.crop((left, top, left + width, top + height))


def _wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
                max_width: int) -> list[str]:
    """Greedy word-wrap: breek text op in regels die binnen max_width passen."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fit_headline(draw: ImageDraw.ImageDraw, text: str,
                  max_width: int) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Zoek de grootste fontmaat waarop de kop in max 2 regels past.

    Pre:  text is niet leeg
    Post: (regels, font); past het op de kleinste maat nog niet, dan wordt de
          tweede regel afgekapt met een ellipsis
    """
    font = None
    lines: list[str] = []
    for size in HEADLINE_FONT_SIZES:
        # Bold, niet de ExtraBold van het woordmerk: over twee regels van 70+px
        # wordt ExtraBold een blok en verliest de kop zijn leesritme
        font = font_at(size, "Bold")
        lines = _wrap_lines(draw, text, font, max_width)
        if len(lines) <= HEADLINE_MAX_LINES and all(
            draw.textlength(line, font=font) <= max_width for line in lines
        ):
            return lines, font

    # Kleinste maat: afkappen op 2 regels met ellipsis
    lines = lines[:HEADLINE_MAX_LINES]
    last = lines[-1]
    while last and draw.textlength(last + "…", font=font) > max_width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    lines[-1] = last + "…"
    logger.warning("Instagram-kop afgekapt: %s", " / ".join(lines))
    return lines, font


def _draw_tracked_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str,
                       font: ImageFont.FreeTypeFont, fill: tuple,
                       tracking: int) -> None:
    """Teken tekst met vaste extra letterafstand (PIL kent geen tracking)."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _draw_ai_label(canvas: Image.Image) -> Image.Image:
    """Plaats het 'AI-gegenereerd'-label linksboven op het fotodeel.

    Zelfde pill-stijl als image_generator.add_ai_label(), maar linksboven:
    het origineel-label linksonder verdwijnt hier onder de witte balk.
    """
    label = "AI-gegenereerd"
    font = font_at(22, "SemiBold")

    draw_tmp = ImageDraw.Draw(canvas)
    bbox = draw_tmp.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pad_x, pad_y = 14, 9
    margin = 24
    x, y = margin, margin
    bg_w = text_w + pad_x * 2
    bg_h = text_h + pad_y * 2

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle(
        [x, y, x + bg_w, y + bg_h], radius=bg_h // 2, fill=(255, 255, 255, 215)
    )
    canvas = Image.alpha_composite(canvas, overlay)
    ImageDraw.Draw(canvas).text(
        (x + pad_x, y + pad_y - bbox[1]), label, font=font, fill=(*NAVY, 255)
    )
    return canvas


def compose_reel_card(dest_path: str, subtitle: str = "",
                      canvas_w: int = CANVAS_W,
                      canvas_h: int = CANVAS_H) -> str | None:
    """Merkkaart voor de intro/outro van de wekelijkse Reel.

    Geen artikelbeeld maar een merkvlak met het woordmerk; daarom los van
    compose_instagram_image(), dat een bronfoto nodig heeft. Krijgt bewust
    géén AI-label: er zit geen gegenereerd beeldmateriaal in.

    De achtergrond komt uit brand.background(): hetzelfde verloop met het grote
    T-watermerk als de story-achtergronden, zodat de video en de socials één
    beeldtaal delen. Vlak navy was hier eerder erg leeg.

    Pre:  dest_path is schrijfbaar; subtitle mag leeg zijn (regel vervalt dan)
    Post: canvas_w x canvas_h JPEG op dest_path; None bij elke fout (nooit
          raisen — zelfde contract als de rest van de pijplijn)
    """
    try:
        canvas = brand_background(canvas_w, canvas_h, "gradient").convert("RGB")
        draw = ImageDraw.Draw(canvas)

        # Woordmerk op ~72% van de breedte; render_lockup schaalt op hoogte,
        # dus eerst native renderen en daarna op breedte terugschalen. De
        # inline-vorm heeft geen navy tegel, die op deze navy kaart een gat zou
        # slaan; 72% i.p.v. 78% omdat dit merk veel hoger opbouwt dan de oude
        # getrackte kapitalen en anders de accentstreep zou verdringen.
        target_w = int(canvas_w * 0.72)
        mark = render_lockup(height=120, form="inline", on_dark=True)
        scale = target_w / mark.width
        mark = mark.resize(
            (target_w, max(1, round(mark.height * scale))), Image.LANCZOS
        )

        mark_y = (canvas_h - mark.height) // 2
        canvas.paste(mark, ((canvas_w - mark.width) // 2, mark_y), mark)

        # Cyaan accentstreep onder het wordmark
        rule_w, rule_h = int(canvas_w * 0.16), 6
        rule_y = mark_y + mark.height + 48
        draw.rounded_rectangle(
            [(canvas_w - rule_w) // 2, rule_y,
             (canvas_w + rule_w) // 2, rule_y + rule_h],
            radius=rule_h // 2, fill=CYAN,
        )

        if subtitle:
            # Montserrat, net als het woordmerk erboven — DejaVu ernaast las als
            # een tweede huisstijl op dezelfde kaart
            font = fit_cap(34, weight="SemiBold")
            lines, y = [], rule_y + 64
            words, current = subtitle.split(), ""
            for word in words:
                probe = f"{current} {word}".strip()
                if draw.textlength(probe, font=font) <= canvas_w - MARGIN * 2:
                    current = probe
                else:
                    lines.append(current)
                    current = word
            if current:
                lines.append(current)
            for line in lines:
                w = draw.textlength(line, font=font)
                draw.text(((canvas_w - w) / 2, y), line, font=font, fill=WHITE)
                y += round(font.size * 1.3)

        canvas.save(dest_path, "JPEG", quality=92)
        logger.info("Reel-kaart gemaakt: %s", dest_path)
        return dest_path

    except Exception as exc:
        logger.error("Reel-kaart maken mislukt: %s", exc)
        return None


def compose_instagram_image(src_path: str, headline: str, kicker: str,
                            dest_path: str, canvas_w: int = CANVAS_W,
                            canvas_h: int = CANVAS_H,
                            band_bottom_frac: float = 1.0) -> str | None:
    """Componeer het Instagram-feedbeeld vanaf de artikelafbeelding.

    Pre:  src_path is een geldig afbeeldingsbestand; headline is niet leeg;
          kicker mag leeg zijn (regel wordt dan overgeslagen).
          band_bottom_frac bepaalt waar de ónderrand van de witte balk ligt, als
          fractie van de canvashoogte: 1.0 = tegen de onderkant (feedpost).
    Post: canvas_w x canvas_h JPEG (standaard 1080x1350, 4:5) met witte balk
          + XMP AI-metadata op dest_path — canvas_w/canvas_h laten dit ook
          hergebruiken voor 9:16 Reel-slides (zie weekly_reel.py); de balk
          blijft even hoog (op basis van de tekst), dus bij een hogere canvas
          is er gewoon meer foto zichtbaar erboven.
          Retourneert dest_path, of None bij elke fout (nooit raisen —
          zelfde contract als de rest van de social-pijplijn)

    Bij een Reel moet band_bottom_frac omhoog: Instagram legt onderin z'n eigen
    interface over de video (caption, accountnaam, muziekregel, knoppen), waardoor
    tekst tegen de onderkant onleesbaar wordt. Zie REEL_BAND_BOTTOM in
    weekly_reel.py.
    """
    try:
        photo = Image.open(src_path)
        canvas = _cover_crop(photo, canvas_w, canvas_h).convert("RGBA")
        draw = ImageDraw.Draw(canvas)

        text_width = canvas_w - MARGIN * 2

        # --- Balkinhoud opmeten (hoogte is dynamisch: 1 of 2 kopregels) ---
        pad_top, pad_bottom = 52, 52
        kicker_font = font_at(30, "Bold")
        kicker_h = 36 if kicker else 0
        kicker_gap = 26 if kicker else 0

        lines, headline_font = _fit_headline(draw, headline, text_width)
        line_h = round(headline_font.size * 1.16)
        headline_h = line_h * len(lines)

        # Op de witte balk: navy tekst, cyaan stam — dezelfde inline-vorm als op
        # de intro/outro-kaart, zodat één Reel niet twee woordmerken toont
        wordmark = render_lockup(height=34, form="inline", on_dark=False)
        wordmark_gap = 40

        band_h = (pad_top + kicker_h + kicker_gap + headline_h
                  + wordmark_gap + wordmark.height + pad_bottom)
        # Nooit boven de bovenrand uitschuiven als de balk hoog uitvalt (2 kopregels)
        band_bottom = max(band_h, round(canvas_h * band_bottom_frac))
        band_top = band_bottom - band_h

        # --- Witte balk (full-bleed, effen — de Volkskrant-stripe) ---
        draw.rectangle([0, band_top, canvas_w, band_bottom], fill=(*WHITE, 255))
        # Dun cyaan accentlijntje op de balkrand
        draw.rectangle([0, band_top, canvas_w, band_top + 8], fill=(*CYAN, 255))

        y = band_top + pad_top
        if kicker:
            _draw_tracked_text(draw, (MARGIN, y), kicker.upper(),
                               kicker_font, (*KICKER_CYAN, 255), tracking=4)
            y += kicker_h + kicker_gap

        for line in lines:
            draw.text((MARGIN, y), line, font=headline_font, fill=(*NAVY, 255))
            y += line_h
        y += wordmark_gap

        canvas.paste(wordmark, (MARGIN, y), wordmark)

        # --- AI-label op het fotodeel + AI-metadata in het bestand ---
        canvas = _draw_ai_label(canvas)
        save_kwargs = {"xmp": _XMP_AI_METADATA} if _XMP_METADATA_ENABLED else {}
        canvas.convert("RGB").save(dest_path, "JPEG", quality=92, **save_kwargs)
        logger.info("Instagram-beeld gecomponeerd: %s (kop: %s)", dest_path, headline)
        return dest_path

    except Exception as exc:
        logger.error("Instagram-beeld componeren mislukt (%s): %s", src_path, exc)
        return None


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) != 5:
        sys.exit("Gebruik: instagram_image.py <bron.jpg> <kop> <kicker> <doel.jpg>")
    result = compose_instagram_image(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    print(result or "MISLUKT")
