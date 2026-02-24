"""
Publiceert of update WordPress-pagina's vanuit de assets/-map.
Gebruik: python publish_pages.py
"""
import base64
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

from config import WP_APP_PASSWORD, WP_URL, WP_USERNAME

BASE_URL = WP_URL.rstrip("/") + "/wp-json/wp/v2"
ASSETS_DIR = Path(__file__).parent / "assets"

credentials = f"{WP_USERNAME}:{WP_APP_PASSWORD}"
token = base64.b64encode(credentials.encode()).decode()

session = requests.Session()
session.headers.update({"Authorization": f"Basic {token}"})

PAGES = [
    {
        "file": "pagina-colofon.html",
        "title": "Colofon",
        "slug": "colofon",
        "meta_description": (
            "Lees hoe TechNieuwsVandaag.nl werkt: AI-gegenereerde Nederlandstalige "
            "tech-samenvattingen, gereviewd door een menselijke redacteur. Geen advertenties, "
            "geen tracking."
        ),
    },
    {
        "file": "pagina-veel-gebruikte-bronnen.html",
        "title": "Bronnen",
        "slug": "bronnen",
        "meta_description": (
            "Ontdek welke toonaangevende tech-nieuwssites TechNieuwsVandaag gebruikt als bron "
            "voor zijn dagelijkse Nederlandstalige samenvattingen – van Tweakers tot TechCrunch."
        ),
    },
]


def strip_html_comments(html: str) -> str:
    """Remove HTML comment blocks (<!-- ... -->) from the top of the content."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL).strip()


def find_page_by_slug(slug: str) -> dict | None:
    resp = session.get(f"{BASE_URL}/pages", params={"slug": slug, "per_page": 1}, timeout=15)
    resp.raise_for_status()
    pages = resp.json()
    return pages[0] if pages else None


def publish_page(page_def: dict) -> None:
    file_path = ASSETS_DIR / page_def["file"]
    raw_html = file_path.read_text(encoding="utf-8")
    content = strip_html_comments(raw_html)

    existing = find_page_by_slug(page_def["slug"])

    payload = {
        "title": page_def["title"],
        "content": content,
        "slug": page_def["slug"],
        "status": "publish",
    }

    if existing:
        page_id = existing["id"]
        resp = session.post(f"{BASE_URL}/pages/{page_id}", json=payload, timeout=30)
        resp.raise_for_status()
        print(f"Bijgewerkt:  '{page_def['title']}' (ID {page_id}) — {resp.json().get('link', '')}")
    else:
        resp = session.post(f"{BASE_URL}/pages", json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        print(f"Aangemaakt:  '{page_def['title']}' (ID {data['id']}) — {data.get('link', '')}")


def main() -> None:
    for page_def in PAGES:
        try:
            publish_page(page_def)
        except Exception as exc:
            print(f"FOUT bij '{page_def['title']}': {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
