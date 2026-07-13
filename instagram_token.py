"""
Instagram tokenhelper (handmatig, eenmalig bij setup).

Zet een user token uit de Graph API Explorer om in een never-expiring Page
access token en zoekt het bijbehorende Instagram Business account-ID op.
Port van de bewezen aanpak in projects/amsterdam (instagram_poster.py):
een Page token afgeleid van een long-lived user token verloopt nooit
(debug_token geeft expires_at=0) — dít is het token voor INSTAGRAM_ACCESS_TOKEN.

Gebruik:
  1. Genereer op https://developers.facebook.com/tools/explorer een user token
     met scopes: instagram_basic, instagram_content_publish, pages_show_list,
     pages_read_engagement
  2. venv/bin/python3 instagram_token.py <user_token>            # toon resultaat
     venv/bin/python3 instagram_token.py <user_token> --write    # schrijf naar .env

Vereist FACEBOOK_APP_ID en FACEBOOK_APP_SECRET in .env (voor de long-lived
exchange en de verloopcontrole).
"""
import re
import sys

import requests

from config import BASE_DIR, FACEBOOK_APP_ID, FACEBOOK_APP_SECRET, INSTAGRAM_API_VERSION

GRAPH = f"https://graph.facebook.com/{INSTAGRAM_API_VERSION}"
ENV_PATH = BASE_DIR / ".env"
_PAGE_FIELDS = "name,access_token,instagram_business_account"


def _die(msg: str) -> None:
    sys.exit(f"FOUT: {msg}")


def exchange_long_lived(short_token: str) -> str:
    """Wissel een (short-lived) user token om voor een long-lived user token."""
    resp = requests.get(
        f"{GRAPH}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": FACEBOOK_APP_ID,
            "client_secret": FACEBOOK_APP_SECRET,
            "fb_exchange_token": short_token,
        },
        timeout=15,
    )
    data = resp.json()
    if "access_token" not in data:
        _die(f"long-lived exchange mislukt: {data.get('error', {}).get('message', data)}")
    print("✓ Long-lived user token verkregen")
    return data["access_token"]


def _pages_for_token(user_token: str) -> list[dict]:
    """Alle pagina's van deze gebruiker, incl. Business Manager-fallback.

    /me/accounts is leeg wanneer de pagina via Business Manager wordt beheerd;
    de toegekende page-IDs staan dan wél in de granular_scopes van het token.
    """
    resp = requests.get(
        f"{GRAPH}/me/accounts",
        params={"fields": _PAGE_FIELDS, "access_token": user_token},
        timeout=15,
    )
    payload = resp.json()
    if "data" not in payload:
        _die(f"/me/accounts mislukt: {payload.get('error', {}).get('message', payload)}")
    pages = payload["data"]

    if not pages:
        print("… /me/accounts leeg — probeer Business Manager-fallback via debug_token")
        dbg = requests.get(
            f"{GRAPH}/debug_token",
            params={
                "input_token": user_token,
                "access_token": f"{FACEBOOK_APP_ID}|{FACEBOOK_APP_SECRET}",
            },
            timeout=15,
        )
        page_ids: list[str] = []
        for gs in dbg.json().get("data", {}).get("granular_scopes", []):
            if gs.get("scope") in ("pages_show_list", "pages_read_engagement", "pages_manage_posts"):
                for tid in gs.get("target_ids", []) or []:
                    if tid not in page_ids:
                        page_ids.append(tid)
        for pid in page_ids:
            pr = requests.get(
                f"{GRAPH}/{pid}",
                params={"fields": _PAGE_FIELDS, "access_token": user_token},
                timeout=15,
            )
            pj = pr.json()
            if pj.get("access_token"):
                pages.append(pj)
    return pages


def _verify_never_expires(page_token: str) -> bool:
    """Controleer via debug_token dat het Page token nooit verloopt."""
    dbg = requests.get(
        f"{GRAPH}/debug_token",
        params={
            "input_token": page_token,
            "access_token": f"{FACEBOOK_APP_ID}|{FACEBOOK_APP_SECRET}",
        },
        timeout=15,
    )
    data = dbg.json().get("data", {})
    return bool(data.get("is_valid")) and data.get("type") == "PAGE" and data.get("expires_at") == 0


def _write_env(updates: dict[str, str]) -> None:
    """Vervang of voeg KEY=VALUE regels toe in .env."""
    content = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    for key, value in updates.items():
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", content, flags=re.MULTILINE):
            content = re.sub(rf"^{key}=.*$", line, content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\n{line}\n"
    ENV_PATH.write_text(content, encoding="utf-8")
    print(f"✓ {', '.join(updates)} weggeschreven naar {ENV_PATH}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--write"]
    write = "--write" in sys.argv
    if len(args) != 1:
        sys.exit(__doc__)
    if not FACEBOOK_APP_ID or not FACEBOOK_APP_SECRET:
        _die("FACEBOOK_APP_ID / FACEBOOK_APP_SECRET ontbreken in .env")

    user_token = exchange_long_lived(args[0])
    pages = _pages_for_token(user_token)
    if not pages:
        _die("geen Facebook-pagina's gevonden voor dit token (pages_show_list-scope nodig, "
             "en de pagina moet aan het token zijn toegekend)")

    # Kies de pagina met een gekoppeld Instagram Business account
    chosen = next((p for p in pages if p.get("instagram_business_account")), pages[0])
    page_token = chosen.get("access_token")
    if not page_token:
        _die(f"pagina '{chosen.get('name')}' heeft geen access_token (pages-permissies ontbreken?)")

    iba = (chosen.get("instagram_business_account") or {}).get("id", "")
    never_expires = _verify_never_expires(page_token)

    print(f"\nPagina                : {chosen.get('name')}")
    print(f"Instagram account-ID  : {iba or 'NIET GEVONDEN — is het IG-account gekoppeld aan de pagina?'}")
    print(f"Token verloopt nooit  : {'JA' if never_expires else 'NEE — controleer met debug_token!'}")
    print(f"\nINSTAGRAM_ACCOUNT_ID={iba}")
    print(f"INSTAGRAM_ACCESS_TOKEN={page_token}")

    if write:
        if not iba:
            _die("geen Instagram account-ID gevonden — .env niet aangepast")
        _write_env({
            "INSTAGRAM_ACCOUNT_ID": iba,
            "INSTAGRAM_ACCESS_TOKEN": page_token,
        })
        print("Klaar. Zet ENABLE_INSTAGRAM_POSTING=true zodra de testpost is gelukt.")


if __name__ == "__main__":
    main()
