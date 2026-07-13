"""
Eenmalige Instagram-profielassets genereren: avatar + wordmark.

Bewust puur PIL (geen FAL.ai): een logo moet pixel-precies en reproduceerbaar
zijn. `render_wordmark()` wordt ook door instagram_image.py gebruikt zodat het
wordmark op elke post uit dezelfde bron komt als het profiel.

Gebruik:
    venv/bin/python3 generate_instagram_assets.py
"""
import logging

from PIL import Image, ImageDraw, ImageFont

from config import BASE_DIR

logger = logging.getLogger(__name__)

ASSETS_DIR = BASE_DIR / "assets"

# Merkkleuren (zelfde palet als site/Bluesky: zie generate_header_image.py)
NAVY = (10, 22, 40)      # #0A1628
CYAN = (0, 212, 255)     # #00D4FF
WHITE = (255, 255, 255)

_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

AVATAR_SIZE = 1000
AVATAR_PATH = ASSETS_DIR / "instagram_avatar.png"
WORDMARK_PATH = ASSETS_DIR / "ig_wordmark.png"
WORDMARK_TEXT = "TECHNIEUWSVANDAAG.NL"


def _bold_font(size: int) -> ImageFont.FreeTypeFont:
    """Laad DejaVuSans-Bold; raist als het font ontbreekt (geen stille fallback
    — de assets moeten er altijd identiek uitzien)."""
    return ImageFont.truetype(_FONT_BOLD, size)


def generate_avatar(dest_path: str) -> str:
    """Genereer de profielfoto: vlakke twee-tonige 'T' op navy.

    Ontwerp: witte dwarsbalk + cyaan stam, afgeronde hoeken, gecentreerd met
    ruime marge zodat de cirkelcrop van Instagram (110px weergave) niets
    afsnijdt en het merk leesbaar blijft op klein formaat.

    Pre:  dest_path is schrijfbaar
    Post: PNG van AVATAR_SIZE² geschreven naar dest_path; pad geretourneerd
    """
    img = Image.new("RGB", (AVATAR_SIZE, AVATAR_SIZE), NAVY)
    draw = ImageDraw.Draw(img)

    # Stam eerst (cyaan), zodat de afgeronde bovenhoeken achter de balk verdwijnen
    draw.rounded_rectangle([440, 300, 560, 730], radius=28, fill=CYAN)
    # Dwarsbalk (wit) er bovenop
    draw.rounded_rectangle([270, 300, 730, 420], radius=28, fill=WHITE)

    img.save(dest_path, "PNG")
    logger.info("Instagram avatar opgeslagen: %s", dest_path)
    return str(dest_path)


def render_wordmark(height: int, color: tuple = NAVY) -> Image.Image:
    """Render het wordmark 'TECHNIEUWSVANDAAG.NL' als transparante RGBA-image.

    Opbouw: cyaan afgerond vierkant (accent) + getrackte kapitalen. Wordt op
    3x geanti-aliased gerenderd en teruggeschaald voor scherpe randen op
    kleine hoogtes.

    Pre:  height > 0
    Post: RGBA Image met exact de gevraagde hoogte, breedte naar rato
    """
    scale = 3
    font_size = height * scale
    font = _bold_font(font_size)
    tracking = round(font_size * 0.14)

    # Meet totale tekstbreedte inclusief tracking
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    char_widths = [tmp.textlength(ch, font=font) for ch in WORDMARK_TEXT]
    text_w = int(sum(char_widths)) + tracking * (len(WORDMARK_TEXT) - 1)

    ascent, descent = font.getmetrics()
    line_h = ascent + descent

    square = int(font_size * 0.72)          # cyaan accentblok, ~kapitaalhoogte
    gap = int(font_size * 0.45)

    canvas = Image.new("RGBA", (square + gap + text_w, line_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Accentblok verticaal uitlijnen met de kapitalen
    cap_top = ascent - square
    draw.rounded_rectangle(
        [0, cap_top, square, cap_top + square],
        radius=int(square * 0.22),
        fill=CYAN,
    )

    x = square + gap
    for ch, w in zip(WORDMARK_TEXT, char_widths):
        draw.text((x, 0), ch, font=font, fill=color)
        x += int(w) + tracking

    # Strak croppen op inhoud en terugschalen naar de gevraagde hoogte
    bbox = canvas.getbbox()
    canvas = canvas.crop(bbox)
    target_w = max(1, round(canvas.width * height / canvas.height))
    return canvas.resize((target_w, height), Image.LANCZOS)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    generate_avatar(str(AVATAR_PATH))

    # Referentie-export van het wordmark (instagram_image.py rendert 'm zelf
    # via render_wordmark, dit bestand is voor hergebruik buiten de pipeline)
    wordmark = render_wordmark(height=120)
    wordmark.save(WORDMARK_PATH, "PNG")
    logger.info("Wordmark opgeslagen: %s (%dx%d)", WORDMARK_PATH, wordmark.width, wordmark.height)


if __name__ == "__main__":
    main()
