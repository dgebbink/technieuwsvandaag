"""
Reel-beelden laten bewegen via de gratis Gemini-webinterface.

De wekelijkse Reel bestond uit stilstaande 9:16-slides. Hier wordt per artikel
het bronbeeld geanimeerd (Veo, via gemini.google.com) en daarna dezelfde
opmaaklaag eroverheen gelegd, zodat balk, kop en woordmerk exact blijven zoals
in de stilstaande versie — die blijft ook gewoon werken als terugval.

Twee dingen die niet vanzelf spreken:

- **De opmaaklaag wordt uit de bestaande compositie gelicht**, niet nagebouwd.
  `compose_instagram_image()` tekent de witte balk ondoorzichtig óver de foto,
  dus het verschil tussen de gecomponeerde slide en dezelfde kale bijgesneden
  foto ís precies die opmaak. Zo blijft er één bron voor het ontwerp en kan de
  Reel-opmaak niet uit de pas gaan lopen met de feed-opmaak.
- **Het AI-label gaat niet mee de animatie in.** Dat label zit al in het
  artikelbeeld (`add_ai_label()`), en Veo animeert het dan mee: het gaat
  zichtbaar wiebelen onder de stilstaande overlay. Daarom wordt het weggesneden
  vóór het animeren; de overlay levert het label alsnog.
"""
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageChops

import gemini_web_session as sess
from config import (
    REEL_ANIMATE_SECONDS,
    REEL_ANIMATE_TIMEOUT,
    WEBGEMINI_SELENIUM_CONTAINER,
    WEBGEMINI_SSH_HOST,
)

logger = logging.getLogger(__name__)

# Veo krijgt het beeld als vertrekpunt; alles wat naar nieuwe elementen ruikt is
# hier ongewenst — het moet dezelfde foto blijven, alleen bewegend.
_ANIMATE_PROMPT = (
    "Animate this photograph into a short video with subtle, natural motion: "
    "gentle camera drift, slight parallax, and small realistic movements of the "
    "people and the scene. Keep the framing, composition, colours and all "
    "on-screen content exactly as they are. Photorealistic, no new elements, "
    "no added text."
)

# Het AI-label staat linksonder in het artikelbeeld; deze strook eraf snijden
# haalt het weg zonder de compositie merkbaar aan te tasten.
_LABEL_STRIP_FRACTION = 0.07


def _strip_ai_label(src: Path, dest: Path) -> Path:
    """Snijd de onderste strook met het AI-label eraf vóór het animeren."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        cut = int(im.height * _LABEL_STRIP_FRACTION)
        im.crop((0, 0, im.width, im.height - cut)).save(dest, "JPEG", quality=95)
    return dest


def _cover_crop(src: Path, dest: Path, width: int, height: int) -> Path:
    """Zelfde bijsnijding als in instagram_image, los bruikbaar."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        scale = max(width / im.width, height / im.height)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        left, top = (im.width - width) // 2, (im.height - height) // 2
        im.crop((left, top, left + width, top + height)).save(dest, "JPEG", quality=95)
    return dest


def build_overlay(slide_path: str, src_path: str, dest_path: str,
                  width: int, height: int) -> Optional[str]:
    """Licht de opmaaklaag (balk, kop, woordmerk, label) uit een slide.

    Pre:  slide_path is het resultaat van compose_instagram_image() op src_path,
          met dezelfde canvasmaten
    Post: PNG met alfakanaal op dest_path: opmaak ondoorzichtig, de rest
          transparant. None bij elke fout.
    """
    try:
        base = _cover_crop(Path(src_path), Path(f"{dest_path}.base.jpg"), width, height)
        with Image.open(slide_path) as slide_im, Image.open(base) as base_im:
            slide_rgb = slide_im.convert("RGB")
            diff = ImageChops.difference(slide_rgb, base_im.convert("RGB")).convert("L")
            # Drempel tegen JPEG-ruis; de balk is effen wit, dus het echte
            # verschil is fors en haalt deze grens ruim.
            mask = diff.point(lambda p: 255 if p > 12 else 0)
            out = slide_rgb.convert("RGBA")
            out.putalpha(mask)
            out.save(dest_path, "PNG")
        Path(base).unlink(missing_ok=True)
        return dest_path
    except Exception as exc:
        logger.warning("Opmaaklaag maken mislukt: %s", exc)
        return None


def animate_image(src_path: str, dest_path: str) -> Optional[str]:
    """Laat één beeld animeren door de Gemini-webinterface.

    Pre:  src_path bestaat; de Selenium-grid draait en de geleende Google-sessie
          is nog geldig
    Post: MP4 op dest_path, of None bij élke tegenslag (quota op, verlopen
          sessie, gewijzigde UI). None is een normale uitkomst: de Reel valt dan
          voor dít artikel terug op de stilstaande slide.
    """
    deadline = time.time() + REEL_ANIMATE_TIMEOUT
    stripped = Path(f"{dest_path}.src.jpg")
    try:
        _strip_ai_label(Path(src_path), stripped)
    except Exception as exc:
        logger.warning("AI-label wegsnijden mislukt (%s) — origineel gebruikt", exc)
        stripped = Path(src_path)

    # Het bestand moet in de Selenium-container staan: het file-input daar leest
    # van de lokale schijf van díe container, niet van ons werkstation.
    remote = "/tmp/tnv_anim_src.jpg"
    try:
        subprocess.run(["scp", "-q", str(stripped), f"{WEBGEMINI_SSH_HOST}:{remote}"],
                       check=True, timeout=120)
        sess.ssh(f"docker cp {remote} {WEBGEMINI_SELENIUM_CONTAINER}:{remote} && rm -f {remote}")
    except Exception as exc:
        logger.warning("Bronbeeld niet in de container gekregen: %s", exc)
        return None

    sid = sess.open_session()
    if not sid:
        return None
    try:
        if not sess.authenticate(sid):
            logger.warning(
                "Reel-animatie: niet ingelogd — sessiecookies verlopen? "
                "Log opnieuw in via de Firefox-container."
            )
            return None

        fid = None
        for _ in range(3):
            fid = sess.find(sid, 'input[type="file"]')
            if fid:
                break
            # Nog geen input in de DOM: eerst het plus-menu openen.
            sess.js(sid, """for (const b of document.querySelectorAll('button')) {
                              const a=(b.getAttribute('aria-label')||'');
                              if (/add|upload|bestand|plus|insert/i.test(a)) { b.click(); return; }
                            }""")
            time.sleep(3)
        if not fid:
            logger.warning("Reel-animatie: geen bestandsveld gevonden")
            return None

        sess.req("POST", f"/session/{sid}/element/{fid}/value", json={"text": remote})
        time.sleep(8)

        if not sess.submit_prompt(sid, _ANIMATE_PROMPT):
            return None

        src_url = ""
        while time.time() < deadline and not src_url:
            time.sleep(8)
            src_url = sess.js(sid, """
              const v = document.querySelector('video');
              return v ? (v.src || v.currentSrc || '') : '';""") or ""
        if not src_url:
            logger.warning("Reel-animatie: geen video binnen de tijd (quota op?)")
            return None

        # De video-URL zit achter dezelfde sessie, dus met de cookies ophalen.
        jar = {c["name"]: c["value"] for c in sess.fetch_cookies()}
        resp = requests.get(src_url, cookies=jar, timeout=180)
        if resp.status_code != 200 or len(resp.content) < 10_000:
            logger.warning("Reel-animatie: download mislukt (HTTP %s, %d bytes)",
                           resp.status_code, len(resp.content))
            return None
        Path(dest_path).write_bytes(resp.content)
        logger.info("Reel-animatie klaar: %s (%.1f MB)", dest_path, len(resp.content) / 1e6)
        return dest_path

    except Exception as exc:
        logger.warning("Reel-animatie: onverwachte fout: %s", exc)
        return None
    finally:
        sess.close_session(sid)
        if stripped != Path(src_path):
            stripped.unlink(missing_ok=True)


def build_clip(video_path: str, overlay_path: str, dest_path: str,
               width: int, height: int, seconds: float = REEL_ANIMATE_SECONDS,
               fps: int = 30) -> Optional[str]:
    """Snijd de animatie naar 9:16 en leg de opmaaklaag eroverheen.

    Post: stille MP4 van `seconds` op dest_path, zelfde formaat en fps als de
          stilstaande slides, zodat beide soorten in één Reel passen. None bij
          elke fout.
    """
    try:
        vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
              f"crop={width}:{height},setsar=1[v];[v][1:v]overlay=0:0")
        subprocess.run(
            ["ffmpeg", "-y", "-t", str(seconds), "-i", str(video_path),
             "-i", str(overlay_path), "-filter_complex", vf,
             "-r", str(fps), "-pix_fmt", "yuv420p", "-an", str(dest_path)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        return dest_path
    except subprocess.CalledProcessError as exc:
        logger.warning("Clip bouwen mislukt: %s", (exc.stderr or "")[-400:])
        return None
    except Exception as exc:
        logger.warning("Clip bouwen mislukt: %s", exc)
        return None
