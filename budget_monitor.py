"""
FAL.ai tegoed- en kostenmonitor.

Gebruikt de officiële FAL.ai Platform APIs (admin-scoped key):
- account/billing → actueel tegoed (current_balance)
- models/usage    → werkelijke kosten over een periode (geen schatting)

Het dagoverzicht (daily_digest.py) toont hiermee het echte tegoed en de echte
kosten van vandaag en deze maand.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from config import FAL_API_KEY, FAL_ADMIN_API_KEY, LOGS_DIR

logger = logging.getLogger(__name__)

LOGS_DIR.mkdir(parents=True, exist_ok=True)

CET = ZoneInfo("Europe/Amsterdam")

# Officiële FAL.ai Platform-endpoints. Vereisen een ADMIN-scoped key; een gewone
# API-scoped key geeft 403 ("not permitted to perform this action").
FAL_BILLING_URL = "https://api.fal.ai/v1/account/billing"
FAL_USAGE_URL = "https://api.fal.ai/v1/models/usage"
DASHBOARD_LINK = "https://fal.ai/dashboard/usage-billing/credits"


def _log_debug(msg: str) -> None:
    """Appends a debug line to logs/funds_debug.log.
    Pre:  logs/ directory exists
    Post: message written with newline
    """
    with open(LOGS_DIR / "funds_debug.log", "a") as f:
        f.write(msg + "\n")


def _api_key() -> str:
    """Admin-key voor billing/usage; valt terug op de gewone key."""
    return FAL_ADMIN_API_KEY or FAL_API_KEY


def get_fal_balance() -> dict:
    """Haalt het actuele FAL.ai-tegoed op.

    Post: dict — success, available (USD float), currency, error.
          success=False als geen key beschikbaar is of het endpoint faalt
          (bv. 403 bij een niet-admin key).
    """
    key = _api_key()
    if not key:
        return {"success": False, "available": None, "currency": "USD",
                "error": "Geen FAL API-key geconfigureerd"}

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
            return {
                "success": True,
                "available": round(float(credits.get("current_balance") or 0), 2),
                "currency": credits.get("currency", "USD"),
                "error": None,
            }
        error = _http_error(resp)
    except Exception as exc:
        error = f"Billing-endpoint niet bereikbaar: {exc}"
        _log_debug(f"FAL {FAL_BILLING_URL}: {exc}")

    return {"success": False, "available": None, "currency": "USD", "error": error}


def get_fal_cost(start: datetime) -> dict:
    """Haalt de werkelijke FAL.ai-kosten op vanaf `start` tot nu.

    Pre:  start is een timezone-aware datetime; ADMIN-key in .env.
    Post: dict — success, cost (USD float, som van alle billing-events),
          currency, error. Geen schatting: de bedragen komen uit de usage-API.
    """
    key = _api_key()
    if not key:
        return {"success": False, "cost": None, "currency": "USD",
                "error": "Geen FAL API-key geconfigureerd"}

    try:
        resp = requests.get(
            FAL_USAGE_URL,
            params={"start": start.isoformat(), "expand": "summary"},
            headers={"Authorization": f"Key {key}"},
            timeout=20,
        )
        _log_debug(f"FAL {FAL_USAGE_URL} (start={start.isoformat()}): HTTP {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            summary = data.get("summary") or []
            total = sum(float(item.get("cost") or 0) for item in summary)
            currency = summary[0].get("currency", "USD") if summary else "USD"
            return {
                "success": True,
                "cost": round(total, 4),
                "currency": currency,
                "error": None,
            }
        error = _http_error(resp)
    except Exception as exc:
        error = f"Usage-endpoint niet bereikbaar: {exc}"
        _log_debug(f"FAL {FAL_USAGE_URL}: {exc}")

    return {"success": False, "cost": None, "currency": "USD", "error": error}


def _http_error(resp: requests.Response) -> str:
    """Vertaalt een mislukte HTTP-respons naar een leesbare foutmelding."""
    _log_debug(f"  body {resp.text[:200]}")
    if resp.status_code in (401, 403):
        return ("API-key heeft geen billing-rechten — maak een ADMIN-scoped key "
                "aan en zet die in FAL_ADMIN_API_KEY")
    return f"FAL-endpoint gaf HTTP {resp.status_code}"


def collect_funds_report() -> dict:
    """Verzamelt actueel tegoed en de werkelijke kosten van vandaag en deze maand.

    Post: dict met 'fal' sub-dict — success, available (tegoed), currency,
          today_cost, month_cost, error, link. Alle bedragen zijn echt (uit de
          FAL.ai billing/usage-API), geen schattingen.
    """
    now = datetime.now(CET)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)

    balance = get_fal_balance()
    today = get_fal_cost(today_start)
    month = get_fal_cost(month_start)

    success = balance["success"] or today["success"] or month["success"]
    error = balance.get("error") or today.get("error") or month.get("error")

    fal = {
        "success": success,
        "available": balance.get("available"),
        "currency": balance.get("currency", "USD"),
        "today_cost": today.get("cost"),
        "month_cost": month.get("cost"),
        "error": None if success else error,
        "link": DASHBOARD_LINK,
    }
    return {"fal": fal}


if __name__ == "__main__":
    r = collect_funds_report()["fal"]
    if r["success"]:
        bal = f"${r['available']:.2f}" if r["available"] is not None else "onbekend"
        today = f"${r['today_cost']:.2f}" if r["today_cost"] is not None else "—"
        month = f"${r['month_cost']:.2f}" if r["month_cost"] is not None else "—"
        print(f"FAL.ai tegoed: {bal} USD | vandaag: {today} | deze maand: {month}")
    else:
        print(f"FAL.ai: {r.get('error', 'onbekend')} → {r.get('link', '')}")
