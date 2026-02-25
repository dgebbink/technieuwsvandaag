"""
update_bluesky_profile.py — Eenmalig script om het Bluesky profiel van
technieuwsvandaag.bsky.social bij te werken.

Stappen:
  1. Genereer profielfoto via FAL.ai (400x400)
  2. Genereer achtergrondafbeelding via FAL.ai (3000x1000)
  3. Login op Bluesky
  4. Upload beide afbeeldingen als blob
  5. Update profiel (displayName, description, avatar, banner)
  6. Sla afbeeldingen op in assets/

Gebruik:
  python3 update_bluesky_profile.py
"""
import shutil
import sys
import time
from pathlib import Path

import requests
from PIL import Image

from config import BLUESKY_APP_PASSWORD, BLUESKY_HANDLE, FAL_API_KEY

# ---------------------------------------------------------------------------
# Constanten
# ---------------------------------------------------------------------------

FAL_ENDPOINT   = "https://fal.run/fal-ai/flux/dev"
BLUESKY_HOST   = "https://bsky.social"
ASSETS_DIR     = Path(__file__).parent / "assets"

AVATAR_TMP     = "/tmp/tnv_avatar.jpg"
AVATAR_FINAL   = "/tmp/tnv_avatar_final.jpg"
BANNER_TMP     = "/tmp/tnv_banner.jpg"
BANNER_FINAL   = "/tmp/tnv_banner_final.jpg"

DISPLAY_NAME = "TechNieuwsVandaag"

DESCRIPTION = """\
🇳🇱 Dagelijks het belangrijkste tech-nieuws in helder Nederlands.
Automatisch samengesteld uit de beste bronnen wereldwijd.
Geen ruis, geen clickbait — alleen nieuws dat telt.

🌐 technieuwsvandaag.nl"""

IMAGE_PROMPT_AVATAR = (
    "A modern, clean tech news logo icon. A bold stylized letter T "
    "combined with a subtle circuit board pattern or data stream. "
    "Color palette: deep blue (#0A1628) background with bright cyan "
    "(#00D4FF) and white accents. Flat design, minimalist, "
    "professional. No text. Suitable as a social media profile "
    "picture. Sharp edges, high contrast, memorable."
)

IMAGE_PROMPT_BANNER = (
    "A wide panoramic tech banner image. Abstract digital cityscape "
    "at dawn with flowing data streams and glowing network nodes. "
    "Color palette: deep navy blue to purple gradient background, "
    "with bright cyan and white light trails suggesting fast-moving "
    "information. Clean, modern, professional atmosphere. "
    "Optimistic and energetic mood. No text, no logos. "
    "Cinematic wide format, high detail."
)


# ---------------------------------------------------------------------------
# FAL.ai — afbeelding genereren
# ---------------------------------------------------------------------------

def _fal_generate(prompt: str, image_size: str, dest_tmp: str, label: str) -> str:
    """Genereer een afbeelding via FAL.ai en sla op als dest_tmp.

    Pre:  FAL_API_KEY is geconfigureerd
    Post: afbeelding geschreven naar dest_tmp; geeft dest_tmp terug
    Raises: RuntimeError bij aanhoudende fout
    """
    if not FAL_API_KEY:
        raise RuntimeError("FAL_API_KEY is niet geconfigureerd in .env")

    payload = {
        "prompt": prompt,
        "image_size": image_size,
        "num_inference_steps": 28,
        "guidance_scale": 3.5,
        "num_images": 1,
        "enable_safety_checker": True,
    }

    for attempt in (1, 2):
        try:
            print(f"  FAL.ai aanroep ({label}, poging {attempt})…")
            resp = requests.post(
                FAL_ENDPOINT,
                headers={
                    "Authorization": f"Key {FAL_API_KEY}",
                    "Content-Type":  "application/json",
                },
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            image_url = resp.json()["images"][0]["url"]

            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()
            with open(dest_tmp, "wb") as f:
                f.write(img_resp.content)
            print(f"  ✓ {label} opgeslagen: {dest_tmp}")
            return dest_tmp

        except Exception as exc:
            print(f"  ✗ Poging {attempt} mislukt: {exc}")
            if attempt == 1:
                print("  Wacht 10 seconden voor retry…")
                time.sleep(10)
            else:
                raise RuntimeError(f"FAL.ai genereren mislukt voor {label}: {exc}") from exc

    raise RuntimeError("Onbereikbaar")  # noqa: mypy


def generate_avatar() -> None:
    """Genereer profielfoto en resize naar 400x400."""
    _fal_generate(IMAGE_PROMPT_AVATAR, "square_hd", AVATAR_TMP, "profielfoto")
    img = Image.open(AVATAR_TMP).convert("RGB")
    img = img.resize((400, 400), Image.LANCZOS)
    img.save(AVATAR_FINAL, "JPEG", quality=92)
    print(f"  ✓ Profielfoto resized naar 400×400: {AVATAR_FINAL}")


def generate_banner() -> None:
    """Genereer achtergrond en resize naar 3000x1000."""
    _fal_generate(IMAGE_PROMPT_BANNER, "landscape_16_9", BANNER_TMP, "achtergrond")
    img = Image.open(BANNER_TMP).convert("RGB")
    img = img.resize((3000, 1000), Image.LANCZOS)
    img.save(BANNER_FINAL, "JPEG", quality=92)
    print(f"  ✓ Achtergrond resized naar 3000×1000: {BANNER_FINAL}")


# ---------------------------------------------------------------------------
# Bluesky — login
# ---------------------------------------------------------------------------

def bluesky_login() -> dict:
    """Login op Bluesky en geef sessie terug.

    Pre:  BLUESKY_HANDLE en BLUESKY_APP_PASSWORD zijn geconfigureerd
    Post: dict met accessJwt, did, host
    Raises: RuntimeError als credentials ontbreken of login mislukt
    """
    if not BLUESKY_HANDLE or not BLUESKY_APP_PASSWORD:
        raise RuntimeError(
            "BLUESKY_HANDLE en/of BLUESKY_APP_PASSWORD niet geconfigureerd in .env"
        )

    resp = requests.post(
        f"{BLUESKY_HOST}/xrpc/com.atproto.server.createSession",
        json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"accessJwt": data["accessJwt"], "did": data["did"], "host": BLUESKY_HOST}


# ---------------------------------------------------------------------------
# Bluesky — blob uploaden
# ---------------------------------------------------------------------------

def upload_image_blob(filepath: str, session: dict, mime: str = "image/jpeg") -> dict:
    """Upload een afbeelding als blob naar Bluesky.

    Pre:  filepath bestaat en is leesbaar; session is actief
    Post: geeft blob object terug voor gebruik in profiel record
    Raises: requests.HTTPError bij upload-fout
    """
    with open(filepath, "rb") as f:
        data = f.read()

    resp = requests.post(
        f"{session['host']}/xrpc/com.atproto.repo.uploadBlob",
        headers={
            "Authorization": f"Bearer {session['accessJwt']}",
            "Content-Type":  mime,
        },
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    blob = resp.json()["blob"]
    print(f"  ✓ Blob geüpload ({len(data) // 1024} KB)")
    return blob


# ---------------------------------------------------------------------------
# Bluesky — profiel bijwerken
# ---------------------------------------------------------------------------

def update_profile(
    session: dict,
    display_name: str,
    description: str,
    avatar_blob: dict,
    banner_blob: dict,
) -> dict:
    """Werk het Bluesky profiel bij.

    Pre:  session actief; blobs zijn geüpload via uploadBlob
    Post: geeft updated profile record terug
    Raises: requests.HTTPError bij API-fout
    """
    # Haal huidig profiel op om overige velden te bewaren
    current: dict = {}
    profile_resp = requests.get(
        f"{session['host']}/xrpc/com.atproto.repo.getRecord",
        params={
            "repo":       session["did"],
            "collection": "app.bsky.actor.profile",
            "rkey":       "self",
        },
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        timeout=15,
    )
    if profile_resp.status_code == 200:
        current = profile_resp.json().get("value", {})

    record = {
        **current,
        "$type":       "app.bsky.actor.profile",
        "displayName": display_name,
        "description": description,
        "avatar":      avatar_blob,
        "banner":      banner_blob,
    }

    resp = requests.post(
        f"{session['host']}/xrpc/com.atproto.repo.putRecord",
        headers={
            "Authorization": f"Bearer {session['accessJwt']}",
            "Content-Type":  "application/json",
        },
        json={
            "repo":       session["did"],
            "collection": "app.bsky.actor.profile",
            "rkey":       "self",
            "record":     record,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Hoofdflow: genereer beelden, upload, update profiel.

    Pre:  FAL_API_KEY, BLUESKY_HANDLE, BLUESKY_APP_PASSWORD in .env
    Post: Bluesky profiel bijgewerkt; afbeeldingen in assets/
    """
    ASSETS_DIR.mkdir(exist_ok=True)

    print("\n🎨 Stap 1 — Profielfoto genereren via FAL.ai…")
    try:
        generate_avatar()
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1

    print("\n🖼️  Stap 2 — Achtergrond genereren via FAL.ai…")
    try:
        generate_banner()
    except RuntimeError as exc:
        print(f"❌ {exc}")
        return 1

    print("\n🔐 Stap 3 — Inloggen op Bluesky…")
    try:
        session = bluesky_login()
        print(f"  ✓ Ingelogd als {BLUESKY_HANDLE} (DID: {session['did']})")
    except Exception as exc:
        print(f"❌ Login mislukt: {exc}")
        return 1

    print("\n⬆️  Stap 4 — Profielfoto uploaden…")
    try:
        avatar_blob = upload_image_blob(AVATAR_FINAL, session)
    except Exception as exc:
        print(f"❌ Profielfoto upload mislukt: {exc}")
        return 1

    print("\n⬆️  Stap 5 — Achtergrond uploaden…")
    try:
        banner_blob = upload_image_blob(BANNER_FINAL, session)
    except Exception as exc:
        print(f"❌ Achtergrond upload mislukt: {exc}")
        return 1

    print("\n✏️  Stap 6 — Profiel bijwerken…")
    try:
        result = update_profile(session, DISPLAY_NAME, DESCRIPTION, avatar_blob, banner_blob)
        uri = result.get("uri", result.get("cid", "onbekend"))
        print(f"  ✓ Profiel bijgewerkt (uri: {uri})")
    except Exception as exc:
        print(f"❌ Profiel update mislukt: {exc}")
        return 1

    print("\n💾 Stap 7 — Afbeeldingen opslaan in assets/…")
    shutil.copy(AVATAR_FINAL, ASSETS_DIR / "bluesky_avatar.jpg")
    shutil.copy(BANNER_FINAL, ASSETS_DIR / "bluesky_banner.jpg")
    print(f"  ✓ assets/bluesky_avatar.jpg")
    print(f"  ✓ assets/bluesky_banner.jpg")

    print(
        f"\n✅ Klaar! Controleer het profiel op:\n"
        f"   https://bsky.app/profile/{BLUESKY_HANDLE}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
