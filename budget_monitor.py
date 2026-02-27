"""
Available funds monitor for Anthropic and FAL.ai.
Tries multiple known API endpoints, logs failures.
"""

import logging
from pathlib import Path

import requests

from config import ANTHROPIC_API_KEY, FAL_API_KEY, LOGS_DIR

logger = logging.getLogger(__name__)

LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _log_debug(msg: str) -> None:
    """Appends a debug line to logs/funds_debug.log.
    Pre:  logs/ directory exists
    Post: message written with newline
    """
    with open(LOGS_DIR / "funds_debug.log", "a") as f:
        f.write(msg + "\n")


def get_anthropic_funds() -> dict:
    """Fetches available credit balance from Anthropic.
    Pre:  ANTHROPIC_API_KEY in .env
    Post: dict — success, available (USD float),
          source, error, link
    """
    headers = {
        "x-api-key":         ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }
    endpoints = [
        "https://api.anthropic.com/v1/organizations/me/billing/credits",
        "https://api.anthropic.com/v1/billing/credits",
        "https://api.anthropic.com/v1/account/credits",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            _log_debug(f"Anthropic {url}: HTTP {resp.status_code}")
            if resp.status_code == 200:
                data   = resp.json()
                amount = float(
                    data.get("available_credits")
                    or data.get("balance")
                    or data.get("credits")
                    or 0
                )
                return {
                    "success":   True,
                    "available": round(amount, 2),
                    "currency":  "USD",
                    "source":    "api",
                    "error":     None,
                }
        except Exception as exc:
            _log_debug(f"Anthropic {url}: {exc}")

    return {
        "success":   False,
        "available": None,
        "currency":  "USD",
        "source":    "unavailable",
        "error":     "Endpoint niet bereikbaar",
        "link":      "https://console.anthropic.com/settings/billing",
    }


def get_fal_funds() -> dict:
    """Fetches available credit balance from FAL.ai.
    Pre:  FAL_API_KEY in .env
    Post: dict — success, available (USD float),
          source, error, link
    """
    headers  = {"Authorization": f"Key {FAL_API_KEY}"}
    endpoints = [
        "https://fal.ai/api/billing/credits",
        "https://fal.ai/api/v1/billing/credits",
        "https://fal.ai/api/usage",
        "https://fal.ai/api/v1/account/balance",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            _log_debug(f"FAL {url}: HTTP {resp.status_code}")
            if resp.status_code == 200:
                data   = resp.json()
                amount = float(
                    data.get("credits")
                    or data.get("balance")
                    or data.get("available")
                    or data.get("available_credits")
                    or 0
                )
                return {
                    "success":   True,
                    "available": round(amount, 2),
                    "currency":  "USD",
                    "source":    "api",
                    "error":     None,
                }
        except Exception as exc:
            _log_debug(f"FAL {url}: {exc}")

    return {
        "success":   False,
        "available": None,
        "currency":  "USD",
        "source":    "unavailable",
        "error":     "Endpoint niet bereikbaar",
        "link":      "https://fal.ai/dashboard/billing",
    }


def collect_funds_report() -> dict:
    """Collects available balances for both services.
    Pre:  API keys in .env
    Post: dict with anthropic and fal sub-dicts
    """
    return {
        "anthropic": get_anthropic_funds(),
        "fal":       get_fal_funds(),
    }


if __name__ == "__main__":
    r = collect_funds_report()
    for svc, d in r.items():
        if d["success"]:
            print(f"{svc}: ${d['available']:.2f} USD")
        else:
            print(f"{svc}: {d['error']} → {d.get('link', '')}")
