"""
Eenmalige Instagram-profielassets genereren: de avatar.

Bewust puur PIL (geen FAL.ai): een logo moet pixel-precies en reproduceerbaar
zijn.

Het woordmerk staat sinds 2026-07-29 in `brand.py` (Montserrat, gemengde
kapitalen) — daar komt ook het logo van de site en van de Reel-kaarten vandaan.
De oude getrackte kapitalen die hier stonden zijn vervallen; twee woordmerken
naast elkaar liepen onvermijdelijk uit elkaar.

Gebruik:
    venv/bin/python3 generate_instagram_assets.py
"""
import logging

from PIL import Image, ImageDraw, ImageFont

from brand import CYAN, NAVY, WHITE
from config import BASE_DIR

logger = logging.getLogger(__name__)

ASSETS_DIR = BASE_DIR / "assets"

# Merkkleuren komen uit brand.py — één palet voor site, video en socials
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

AVATAR_SIZE = 1000
AVATAR_PATH = ASSETS_DIR / "instagram_avatar.png"


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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    generate_avatar(str(AVATAR_PATH))
    logger.info("Woordmerk-exports: venv/bin/python3 brand.py")


if __name__ == "__main__":
    main()
