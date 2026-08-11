"""
Gedeelde sessielaag voor de Gemini-webinterface (Selenium op meterkast).

Twee dingen gebruiken dit: de beeldprovider (`image_providers/webgemini.py`) en
de Reel-animator (`reel_animator.py`). Ze delen dezelfde geleende Google-sessie
en dezelfde valkuilen, dus die staan hier één keer.

Er wordt niet ingelogd. Google weigert een geautomatiseerde login botweg
("this browser or app may not be secure"), dus de cookies komen uit het
Firefox-profiel op meterkast waar met de hand is ingelogd. Verlopen ze, dan
faalt alles wat hierop leunt en moet je daar opnieuw inloggen — automatisch
herstellen kan niet.
"""
import json
import logging
import subprocess
import time
from typing import Optional

import requests

from config import (
    SELENIUM_GRID_URL,
    WEBGEMINI_FIREFOX_CONTAINER,
    WEBGEMINI_PROFILE,
    WEBGEMINI_SSH_HOST,
)

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = "/home/seluser/Downloads"


def ssh(command: str, timeout: int = 120) -> str:
    """Voer een commando uit op de host met de containers."""
    out = subprocess.run(["ssh", WEBGEMINI_SSH_HOST, command],
                         capture_output=True, text=True, timeout=timeout)
    return out.stdout.strip()


def req(method: str, path: str, grid: str = "", **kw):
    kw.setdefault("timeout", 120)
    return requests.request(method, f"{(grid or SELENIUM_GRID_URL).rstrip('/')}{path}", **kw)


def js(sid: str, script: str, args: Optional[list] = None, grid: str = ""):
    return req("POST", f"/session/{sid}/execute/sync", grid=grid,
               json={"script": script, "args": args or []}).json().get("value")


def find(sid: str, css: str, grid: str = "") -> Optional[str]:
    value = req("POST", f"/session/{sid}/element", grid=grid,
                json={"using": "css selector", "value": css}).json().get("value")
    if isinstance(value, dict) and "error" not in value:
        return list(value.values())[0]
    return None


def fetch_cookies() -> list:
    """Leen de Google-cookies uit het handmatig ingelogde Firefox-profiel.

    Post: lijst met cookie-dicts; lege lijst als het profiel niet te lezen is.
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
        raw = ssh(script)
        return json.loads(raw.splitlines()[-1]) if raw else []
    except Exception as exc:
        logger.warning("Gemini-web: cookies lezen mislukt: %s", exc)
        return []


def open_session(grid: str = "") -> Optional[str]:
    """Nieuwe Firefox-sessie die downloads zonder dialoog wegschrijft."""
    caps = {"capabilities": {"alwaysMatch": {
        "browserName": "firefox",
        "timeouts": {"script": 60000},
        "moz:firefoxOptions": {"prefs": {
            "browser.download.folderList": 2,
            "browser.download.dir": DOWNLOAD_DIR,
            "browser.download.useDownloadDir": True,
            # Zonder deze regel opent Firefox een downloaddialoog en blijft de
            # boel hangen tot de time-out.
            "browser.helperApps.neverAsk.saveToDisk":
                "image/jpeg,image/png,image/webp,video/mp4,application/octet-stream",
        }},
    }}}
    try:
        return req("POST", "/session", grid=grid, json=caps).json()["value"]["sessionId"]
    except Exception as exc:
        logger.warning("Gemini-web: geen Selenium-sessie: %s", exc)
        return None


def authenticate(sid: str, grid: str = "") -> bool:
    """Zet de geleende cookies en open de app; True als er een invoerveld staat."""
    cookies = fetch_cookies()
    if not cookies:
        return False
    # Cookies kunnen alleen op het eigen domein gezet worden, dus eerst een
    # lichte pagina op google.com openen.
    req("POST", f"/session/{sid}/url", grid=grid,
        json={"url": "https://www.google.com/robots.txt"})
    time.sleep(2)
    for c in cookies:
        payload = {k: c[k] for k in ("name", "value", "path", "secure", "httpOnly")}
        payload["domain"] = c["domain"]
        if c.get("expiry"):
            payload["expiry"] = int(c["expiry"])
        req("POST", f"/session/{sid}/cookie", grid=grid, json={"cookie": payload})

    req("POST", f"/session/{sid}/url", grid=grid, json={"url": "https://gemini.google.com/app"})
    time.sleep(9)
    js(sid, """for (const b of document.querySelectorAll('button'))
                 if (/dismiss/i.test(b.textContent)) { b.click(); return; }""", grid=grid)
    time.sleep(2)
    return find(sid, 'rich-textarea .ql-editor, div[contenteditable="true"]', grid=grid) is not None


def submit_prompt(sid: str, prompt: str, grid: str = "") -> bool:
    """Typ de prompt en verstuur hem.

    Native typen, niet via JS: de pagina draait Trusted Types, waardoor een
    innerHTML-toewijzing door de CSP wordt geblokkeerd.
    """
    eid = None
    for css in ('rich-textarea .ql-editor', 'div[contenteditable="true"]'):
        eid = find(sid, css, grid=grid)
        if eid:
            break
    if not eid:
        logger.warning("Gemini-web: invoerveld niet gevonden (UI gewijzigd?)")
        return False

    req("POST", f"/session/{sid}/element/{eid}/click", grid=grid, json={})
    time.sleep(1)
    req("POST", f"/session/{sid}/element/{eid}/value", grid=grid, json={"text": prompt})
    time.sleep(2)

    sent = js(sid, """
      for (const b of document.querySelectorAll('button')) {
        const a = (b.getAttribute('aria-label')||'');
        if (/send|verstuur|submit/i.test(a) && !b.disabled &&
            b.getAttribute('aria-disabled') !== 'true') { b.click(); return true; }
      }
      return false;""", grid=grid)
    if not sent:
        req("POST", f"/session/{sid}/element/{eid}/value", grid=grid, json={"text": "\ue007"})
    return True


def close_session(sid: str, grid: str = "") -> None:
    try:
        req("DELETE", f"/session/{sid}", grid=grid, timeout=30)
    except Exception:
        pass
