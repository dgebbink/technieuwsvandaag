"""
nextcloud_client.py — Lees en schrijf adhoc.txt via Nextcloud WebDAV publieke share.

Nextcloud publieke share WebDAV patroon:
  Share URL:    https://cloud.gebbink.nl/s/<token>
  WebDAV URL:   https://cloud.gebbink.nl/public.php/webdav/adhoc.txt
  Authenticatie: gebruikersnaam = share token, wachtwoord = ""
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

WEBDAV_URL  = os.getenv("NEXTCLOUD_WEBDAV_URL", "")
SHARE_TOKEN = os.getenv("NEXTCLOUD_SHARE_TOKEN", "")
_AUTH       = (SHARE_TOKEN, "")   # Nextcloud public share: token als username, leeg ww


def read_adhoc() -> list[str]:
    """Leest adhoc.txt van Nextcloud WebDAV share.

    Pre:  NEXTCLOUD_WEBDAV_URL en NEXTCLOUD_SHARE_TOKEN staan in .env
    Post: lijst van niet-lege, niet-commentaar URLs
          lege lijst als bestand niet bestaat (404)
    Raises: requests.exceptions.RequestException bij netwerk/auth-fout
    """
    if not WEBDAV_URL or not SHARE_TOKEN:
        raise RuntimeError("NEXTCLOUD_WEBDAV_URL / NEXTCLOUD_SHARE_TOKEN niet geconfigureerd in .env")

    resp = requests.get(WEBDAV_URL, auth=_AUTH, timeout=15)

    if resp.status_code == 404:
        return []

    resp.raise_for_status()

    lines = resp.text.splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Ondersteun Nextcloud Markdown link-formaat: <https://...>
        if line.startswith("<") and line.endswith(">"):
            line = line[1:-1].strip()
        result.append(line)
    return result


def write_adhoc(urls: list[str]) -> None:
    """Schrijft URL lijst terug naar Nextcloud WebDAV share.

    Pre:  NEXTCLOUD_WEBDAV_URL en NEXTCLOUD_SHARE_TOKEN staan in .env
          urls is een lijst van strings (mag leeg zijn)
    Post: adhoc.txt op Nextcloud bevat de resterende URLs
    Raises: requests.exceptions.RequestException bij netwerk/auth-fout
    """
    if not WEBDAV_URL or not SHARE_TOKEN:
        raise RuntimeError("NEXTCLOUD_WEBDAV_URL / NEXTCLOUD_SHARE_TOKEN niet geconfigureerd in .env")

    content = "\n".join(urls) + ("\n" if urls else "")

    resp = requests.put(
        WEBDAV_URL,
        auth=_AUTH,
        data=content.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
        timeout=15,
    )
    resp.raise_for_status()


def test_connection() -> bool:
    """Test lees- en schrijftoegang tot de Nextcloud share.

    Pre:  .env is geladen met correcte credentials
    Post: True als lezen én schrijven én round-trip werkt, anders False
    """
    print(f"Testing Nextcloud WebDAV: {WEBDAV_URL}")

    current: list[str] = []

    # Test 1: lezen
    try:
        current = read_adhoc()
        print(f"✅ Lezen OK — {len(current)} regel(s) gevonden")
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print("⚠️  Bestand bestaat nog niet (404) — wordt aangemaakt bij eerste schrijfactie")
        else:
            print(f"❌ Lezen mislukt: {e}")
            return False
    except Exception as e:
        print(f"❌ Lezen mislukt: {e}")
        return False

    # Test 2: schrijven (huidige inhoud terug)
    try:
        write_adhoc(current)
        print("✅ Schrijven OK")
    except Exception as e:
        print(f"❌ Schrijven mislukt: {e}")
        return False

    # Test 3: round-trip verificatie
    try:
        marker    = "https://tnv-test-marker.invalid"
        write_adhoc(current + [marker])
        verify = read_adhoc()
        assert marker in verify, "Marker niet teruggelezen"
        write_adhoc(current)   # herstel origineel
        print("✅ Round-trip verificatie OK")
    except Exception as e:
        print(f"❌ Round-trip verificatie mislukt: {e}")
        try:
            write_adhoc(current)
        except Exception:
            pass
        return False

    print("✅ Nextcloud connectie volledig operationeel")
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if test_connection() else 1)
