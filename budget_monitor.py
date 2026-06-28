"""
Available funds monitor for FAL.ai.
Tries multiple known API endpoints, logs failures.
"""

import logging
from pathlib import Path

import requests

from config import FAL_API_KEY, FAL_ADMIN_API_KEY, LOGS_DIR

logger = logging.getLogger(__name__)

LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _log_debug(msg: str) -> None:
    """Appends a debug line to logs/funds_debug.log.
    Pre:  logs/ directory exists
    Post: message written with newline
    """
    with open(LOGS_DIR / "funds_debug.log", "a") as f:
        f.write(msg + "\n")


# Officieel FAL.ai billing-endpoint. Vereist een ADMIN-scoped key; de gewone
# API-scoped key geeft hier 403 ("not permitted to perform this action").
FAL_BILLING_URL = "https://api.fal.ai/v1/account/billing"


def get_fal_funds() -> dict:
    """Fetches the real available credit balance from FAL.ai.

    Pre:  FAL_ADMIN_API_KEY (admin-scope) in .env; valt terug op FAL_API_KEY.
    Post: dict — success, available (USD float), currency, source, error, link.
          success=False als geen admin-key beschikbaar is of het endpoint faalt
          (bv. 403 bij een niet-admin key); de caller toont dan de schatting.
    """
    key = FAL_ADMIN_API_KEY or FAL_API_KEY
    if not key:
        return {
            "success":   False,
            "available": None,
            "currency":  "USD",
            "source":    "unavailable",
            "error":     "Geen FAL API-key geconfigureerd",
            "link":      "https://fal.ai/dashboard/keys",
        }

    try:
        resp = requests.get(
            FAL_BILLING_URL,
            params={"expand": "credits"},
            headers={"Authorization": f"Key {key}"},
            timeout=15,
        )
        _log_debug(f"FAL {FAL_BILLING_URL}: HTTP {resp.status_code}")
        if resp.status_code == 200:
            credits = resp.json().get("credits") or {}
            amount = float(credits.get("current_balance") or 0)
            return {
                "success":   True,
                "available": round(amount, 2),
                "currency":  credits.get("currency", "USD"),
                "source":    "api",
                "error":     None,
            }
        if resp.status_code in (401, 403):
            error = (
                "API-key heeft geen billing-rechten — maak een ADMIN-scoped key "
                "aan en zet die in FAL_ADMIN_API_KEY"
            )
        else:
            error = f"Billing-endpoint gaf HTTP {resp.status_code}"
        _log_debug(f"FAL {FAL_BILLING_URL}: body {resp.text[:200]}")
    except Exception as exc:
        error = f"Billing-endpoint niet bereikbaar: {exc}"
        _log_debug(f"FAL {FAL_BILLING_URL}: {exc}")

    return {
        "success":   False,
        "available": None,
        "currency":  "USD",
        "source":    "unavailable",
        "error":     error,
        "link":      "https://fal.ai/dashboard/keys",
    }


def collect_funds_report() -> dict:
    """Collects FAL.ai budget info for the daily overview.

    FAL.ai biedt geen publieke billing-API, dus de werkelijke kosten worden
    lokaal geschat (fal_usage.py). We proberen alsnog de API (voor het geval die
    ooit beschikbaar komt) en voegen de lokale schatting toe als basis.

    Post: dict met 'fal' sub-dict die zowel de API-poging als de lokale
          geschatte uitgaven (vandaag/maand/totaal + resterend budget) bevat.
    """
    from fal_usage import get_usage_summary  # noqa: PLC0415

    fal = get_fal_funds()
    usage = get_usage_summary()
    fal.update(usage)
    # Markeer dat er bruikbare (geschatte) cijfers zijn, ook als de API faalde.
    fal["estimate"] = True

    return {
        "fal": fal,
    }


if __name__ == "__main__":
    r = collect_funds_report()
    for svc, d in r.items():
        if d["success"]:
            print(f"{svc}: ${d['available']:.2f} USD")
        else:
            print(f"{svc}: {d.get('error', 'onbekend')} → {d.get('link', '')}")
