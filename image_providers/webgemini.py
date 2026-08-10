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
import base64
import json
import logging
import re
import subprocess
import time
from typing import Optional

import requests

from config import (
    WEBGEMINI_FIREFOX_CONTAINER,
    WEBGEMINI_PROFILE,
    WEBGEMINI_SELENIUM_CONTAINER,
    WEBGEMINI_SSH_HOST,
    WEBGEMINI_TIMEOUT,
    SELENIUM_GRID_URL,
)

from .base import ImageOptions, ImageProvider, ImageProviderError
from .nanobanana import _BRAND_PROMPT_SUFFIX

logger = logging.getLogger(__name__)

# De webinterface kent geen aspect_ratio-parameter; dat moet in de prompt zelf.
# Zonder deze regel levert Gemini een vierkant of staand beeld.
_IMAGE_REQUEST_PREFIX = (
    "Generate one photorealistic landscape photograph in 16:9 aspect ratio. "
)

_DOWNLOAD_DIR = "/home/seluser/Downloads"


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

    # -- kleine WebDriver-helpers -------------------------------------------

    def _req(self, method: str, path: str, **kw):
        kw.setdefault("timeout", 120)
        return requests.request(method, f"{self.grid}{path}", **kw)

    def _js(self, sid: str, script: str, args: Optional[list] = None):
        return self._req("POST", f"/session/{sid}/execute/sync",
                         json={"script": script, "args": args or []}).json().get("value")

    def _find(self, sid: str, css: str) -> Optional[str]:
        value = self._req("POST", f"/session/{sid}/element",
                          json={"using": "css selector", "value": css}).json().get("value")
        if isinstance(value, dict) and "error" not in value:
            return list(value.values())[0]
        return None

    def _ssh(self, command: str, timeout: int = 90) -> str:
        out = subprocess.run(["ssh", WEBGEMINI_SSH_HOST, command],
                             capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip()

    # -- sessie ---------------------------------------------------------------

    def _cookies(self) -> list:
        """Leen de Google-cookies uit het handmatig ingelogde Firefox-profiel.

        Post: lijst met cookie-dicts; lege lijst als het profiel niet te lezen is
              (dan mislukt de generatie verderop en valt de keten terug)
        """
        script = (
            'docker cp "%s:/config/.mozilla/firefox/%s/cookies.sqlite" /tmp/tnv_ck.sqlite '
            '>/dev/null 2>&1 && python3 -c \''
            'import sqlite3,json;'
            'c=sqlite3.connect("/tmp/tnv_ck.sqlite");'
            'r=c.execute("select host,name,value,path,isSecure,isHttpOnly,expiry from moz_cookies '
            'where host like \\"%%google.com\\"").fetchall();'
            'print(json.dumps([{"domain":a,"name":b,"value":v,"path":p,"secure":bool(s),'
            '"httpOnly":bool(h),"expiry":e} for a,b,v,p,s,h,e in r]))\'; rm -f /tmp/tnv_ck.sqlite'
        ) % (WEBGEMINI_FIREFOX_CONTAINER, WEBGEMINI_PROFILE)
        try:
            raw = self._ssh(script)
            return json.loads(raw.splitlines()[-1]) if raw else []
        except Exception as exc:
            logger.warning("Web-Gemini: cookies lezen mislukt: %s", exc)
            return []

    def _open_session(self) -> Optional[str]:
        """Nieuwe Firefox-sessie die downloads zonder dialoog wegschrijft."""
        caps = {"capabilities": {"alwaysMatch": {
            "browserName": "firefox",
            "timeouts": {"script": 60000},
            "moz:firefoxOptions": {"prefs": {
                "browser.download.folderList": 2,
                "browser.download.dir": _DOWNLOAD_DIR,
                "browser.download.useDownloadDir": True,
                # Zonder deze regel opent Firefox een downloaddialoog en blijft
                # de generatie hangen tot de time-out.
                "browser.helperApps.neverAsk.saveToDisk":
                    "image/jpeg,image/png,image/webp,application/octet-stream",
            }},
        }}}
        try:
            return self._req("POST", "/session", json=caps).json()["value"]["sessionId"]
        except Exception as exc:
            logger.warning("Web-Gemini: geen Selenium-sessie: %s", exc)
            return None

    def _authenticate(self, sid: str) -> bool:
        """Zet de geleende cookies en open de app; True als we ingelogd zijn."""
        cookies = self._cookies()
        if not cookies:
            return False
        # Cookies kunnen alleen op het eigen domein gezet worden, dus eerst een
        # lichte pagina op google.com openen.
        self._req("POST", f"/session/{sid}/url",
                  json={"url": "https://www.google.com/robots.txt"})
        time.sleep(2)
        for c in cookies:
            payload = {k: c[k] for k in ("name", "value", "path", "secure", "httpOnly")}
            payload["domain"] = c["domain"]
            if c.get("expiry"):
                payload["expiry"] = int(c["expiry"])
            self._req("POST", f"/session/{sid}/cookie", json={"cookie": payload})

        self._req("POST", f"/session/{sid}/url", json={"url": "https://gemini.google.com/app"})
        time.sleep(9)
        self._js(sid, """for (const b of document.querySelectorAll('button'))
                           if (/dismiss/i.test(b.textContent)) { b.click(); return; }""")
        time.sleep(2)
        return self._find(sid, 'rich-textarea .ql-editor, div[contenteditable="true"]') is not None

    # -- generatie -------------------------------------------------------------

    def _submit(self, sid: str, prompt: str) -> bool:
        """Typ de prompt en verstuur hem."""
        eid = None
        for css in ('rich-textarea .ql-editor', 'div[contenteditable="true"]'):
            eid = self._find(sid, css)
            if eid:
                break
        if not eid:
            logger.warning("Web-Gemini: invoerveld niet gevonden (UI gewijzigd?)")
            return False

        self._req("POST", f"/session/{sid}/element/{eid}/click", json={})
        time.sleep(1)
        # Native typen, niet via JS: de pagina draait Trusted Types, waardoor een
        # innerHTML-toewijzing door de CSP wordt geblokkeerd.
        self._req("POST", f"/session/{sid}/element/{eid}/value", json={"text": prompt})
        time.sleep(2)

        sent = self._js(sid, """
          for (const b of document.querySelectorAll('button')) {
            const a = (b.getAttribute('aria-label')||'');
            if (/send|verstuur|submit/i.test(a) && !b.disabled &&
                b.getAttribute('aria-disabled') !== 'true') { b.click(); return true; }
          }
          return false;""")
        if not sent:
            # Terugval: Enter in het veld.
            self._req("POST", f"/session/{sid}/element/{eid}/value", json={"text": "\ue007"})
        return True

    def _wait_for_image(self, sid: str, deadline: float) -> bool:
        """Wacht tot er een gegenereerd beeld in de DOM staat."""
        while time.time() < deadline:
            time.sleep(5)
            found = self._js(sid, """
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
        self._ssh(f"docker exec {container} sh -c 'rm -f {_DOWNLOAD_DIR}/*' 2>/dev/null; true")

        clicked = self._js(sid, """
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
            name = self._ssh(
                f"docker exec {container} sh -c 'ls {_DOWNLOAD_DIR} 2>/dev/null' | grep -v '\\.part$' | head -1"
            )
            if name:
                break
        if not name:
            logger.warning("Web-Gemini: download verscheen niet in de container")
            return None

        tmp_remote = "/tmp/tnv_webgemini_dl"
        self._ssh(f"docker cp '{container}:{_DOWNLOAD_DIR}/{name}' {tmp_remote}")
        local_tmp = f"{dest_path}.download"
        try:
            subprocess.run(["scp", "-q", f"{WEBGEMINI_SSH_HOST}:{tmp_remote}", local_tmp],
                           check=True, timeout=120)
        except Exception as exc:
            logger.warning("Web-Gemini: ophalen van het bestand mislukt: %s", exc)
            return None
        finally:
            self._ssh(f"rm -f {tmp_remote}; docker exec {container} sh -c 'rm -f {_DOWNLOAD_DIR}/*' 2>/dev/null; true")

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
        sid = self._open_session()
        if not sid:
            return None
        try:
            if not self._authenticate(sid):
                logger.warning(
                    "Web-Gemini: niet ingelogd — sessiecookies verlopen? "
                    "Log opnieuw in via de Firefox-container op %s", WEBGEMINI_SSH_HOST
                )
                return None
            if not self._submit(sid, _IMAGE_REQUEST_PREFIX + prompt):
                return None
            if not self._wait_for_image(sid, deadline):
                return None
            return self._download_full_size(sid, dest_path)
        except Exception as exc:
            logger.warning("Web-Gemini: onverwachte fout: %s", exc)
            return None
        finally:
            try:
                self._req("DELETE", f"/session/{sid}", timeout=30)
            except Exception:
                pass
