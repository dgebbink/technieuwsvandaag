"""
Gemini via de web-interface (gemini.google.com) in plaats van de betaalde API.

Waarom dit bestaat: de webinterface is gratis en levert exact dezelfde resolutie
als de API op 1K (1376x768, gemeten). Het scheelt dus geld zolang de dagquota
van het consumentenaccount strekt — daarna neemt de volgende provider in de
keten het over.

**Dit is de fragiele provider van de drie, en dat is bewust ingecalculeerd.**
Hij hangt aan drie dingen die buiten ons beheer liggen:

1. *Een handmatig aangemaakte Google-sessie.* Er wordt niet ingelogd: Google
   weigert een geautomatiseerde login botweg ("Couldn't sign you in — this
   browser or app may not be secure"). In plaats daarvan lenen we de cookies uit
   het Firefox-profiel op meterkast, waar iemand ooit met de hand is ingelogd.
   Verlopen die cookies, dan faalt deze provider en valt de keten terug — er is
   geen manier om dat automatisch te herstellen, dan moet je opnieuw inloggen in
   de Firefox-container.
2. *De DOM van een web-app die Google zonder aankondiging verbouwt.* Selectors
   als `rich-textarea .ql-editor` zijn geen contract.
3. *De dagquota van het gratis account* (rond de 3 beelden). Daarna komt er
   simpelweg geen beeld meer uit.

Alle drie leveren hetzelfde op: `None`, waarna `generate_provider_image()`
doorschuift naar de volgende provider. Er wordt daarom nergens een fout
opgegooid — falen is hier een normale, verwachte uitkomst.

Let op: het beeld in de DOM is een **verkleinde preview** (1024x572). De volle
resolutie zit achter de knop "Download full size image", die een PNG in de
downloadmap van de Selenium-container zet. Die halen we eruit en schrijven we
als JPEG weg.
"""
import logging
import subprocess
import time
from typing import Optional

import gemini_web_session as sess
from config import (
    SELENIUM_GRID_URL,
    WEBGEMINI_SELENIUM_CONTAINER,
    WEBGEMINI_SSH_HOST,
    WEBGEMINI_TIMEOUT,
)

from .base import ImageOptions, ImageProvider, ImageProviderError
from .nanobanana import _BRAND_PROMPT_SUFFIX

logger = logging.getLogger(__name__)

# De webinterface kent geen aspect_ratio-parameter; dat moet in de prompt zelf.
# Zonder deze regel levert Gemini een vierkant of staand beeld.
_IMAGE_REQUEST_PREFIX = (
    "Generate one photorealistic landscape photograph in 16:9 aspect ratio. "
)

class WebGeminiImageProvider(ImageProvider):
    """Beeld via de gratis Gemini-webinterface, aangestuurd met Selenium."""

    name = "webgemini"
    api_key_env = "SELENIUM_GRID_URL"
    # Zelfde merkbehandeling als de API-variant: het is hetzelfde model, dus
    # zonder deze regel wijken de web-beelden qua logo's af van de API-beelden.
    prompt_suffix = _BRAND_PROMPT_SUFFIX

    def __init__(self, grid_url: Optional[str] = None) -> None:
        self.grid = (grid_url or SELENIUM_GRID_URL).rstrip("/")
        if not self.grid:
            raise ImageProviderError(
                "IMAGE_PROVIDER=webgemini, maar SELENIUM_GRID_URL is niet gezet. "
                "Zet die in .env (bijv. http://192.168.2.44:4444), of kies een "
                "andere provider."
            )

    # -- generatie -------------------------------------------------------------

    def _wait_for_image(self, sid: str, deadline: float) -> bool:
        """Wacht tot er een gegenereerd beeld in de DOM staat."""
        while time.time() < deadline:
            time.sleep(5)
            found = sess.js(sid, """
              return [...document.querySelectorAll('img')]
                .some(i => i.naturalWidth >= 400 && i.naturalHeight >= 300);""")
            if found:
                return True
        logger.warning("Web-Gemini: geen beeld binnen de tijd (quota op of UI gewijzigd)")
        return False

    def _download_full_size(self, sid: str, dest_path: str) -> Optional[str]:
        """Klik 'Download full size image' en haal het bestand uit de container.

        Het beeld in de DOM is maar 1024px breed; de knop levert de volle
        1376x768 als PNG. Die wordt hier als JPEG naar dest_path geschreven.
        """
        container = WEBGEMINI_SELENIUM_CONTAINER
        sess.ssh(f"docker exec {container} sh -c 'rm -f {sess.DOWNLOAD_DIR}/*' 2>/dev/null; true")

        clicked = sess.js(sid, """
          for (const b of document.querySelectorAll('button, a')) {
            const a = ((b.getAttribute('aria-label')||'') + ' ' + (b.textContent||''));
            if (/download full size/i.test(a)) { b.click(); return true; }
          }
          return false;""")
        if not clicked:
            logger.warning("Web-Gemini: knop 'Download full size image' niet gevonden")
            return None

        # Wachten tot het bestand er staat én niet meer groeit (.part = bezig).
        name = ""
        for _ in range(20):
            time.sleep(2)
            name = sess.ssh(
                f"docker exec {container} sh -c 'ls {sess.DOWNLOAD_DIR} 2>/dev/null' | grep -v '\\.part$' | head -1"
            )
            if name:
                break
        if not name:
            logger.warning("Web-Gemini: download verscheen niet in de container")
            return None

        tmp_remote = "/tmp/tnv_webgemini_dl"
        sess.ssh(f"docker cp '{container}:{sess.DOWNLOAD_DIR}/{name}' {tmp_remote}")
        local_tmp = f"{dest_path}.download"
        try:
            subprocess.run(["scp", "-q", f"{WEBGEMINI_SSH_HOST}:{tmp_remote}", local_tmp],
                           check=True, timeout=120)
        except Exception as exc:
            logger.warning("Web-Gemini: ophalen van het bestand mislukt: %s", exc)
            return None
        finally:
            sess.ssh(f"rm -f {tmp_remote}; docker exec {container} sh -c 'rm -f {sess.DOWNLOAD_DIR}/*' 2>/dev/null; true")

        # De download is PNG; de rest van de pijplijn verwacht JPEG.
        try:
            from PIL import Image  # noqa: PLC0415

            with Image.open(local_tmp) as im:
                im.convert("RGB").save(dest_path, "JPEG", quality=92)
            size = im.size
        except Exception as exc:
            logger.warning("Web-Gemini: omzetten naar JPEG mislukt: %s", exc)
            return None
        finally:
            import os  # noqa: PLC0415

            if os.path.exists(local_tmp):
                os.remove(local_tmp)

        logger.info("Web-Gemini beeld opgeslagen: %s (%dx%d)", dest_path, *size)
        return dest_path

    def generate_image(
        self,
        prompt: str,
        dest_path: str,
        options: Optional[ImageOptions] = None,
    ) -> Optional[str]:
        """Genereer gratis via de webinterface. Zie ImageProvider.

        Post: dest_path bij succes, None bij élke tegenslag (verlopen sessie,
              quota op, gewijzigde UI). None is hier een normale uitkomst: de
              keten schuift dan door naar de volgende provider.
        """
        deadline = time.time() + WEBGEMINI_TIMEOUT
        sid = sess.open_session(self.grid)
        if not sid:
            return None
        try:
            if not sess.authenticate(sid, self.grid):
                logger.warning(
                    "Web-Gemini: niet ingelogd — sessiecookies verlopen? "
                    "Log opnieuw in via de Firefox-container op %s", WEBGEMINI_SSH_HOST
                )
                return None
            if not sess.submit_prompt(sid, _IMAGE_REQUEST_PREFIX + prompt, self.grid):
                return None
            if not self._wait_for_image(sid, deadline):
                return None
            return self._download_full_size(sid, dest_path)
        except Exception as exc:
            logger.warning("Web-Gemini: onverwachte fout: %s", exc)
            return None
        finally:
            try:
                sess.req("DELETE", f"/session/{sid}", timeout=30)
            except Exception:
                pass
