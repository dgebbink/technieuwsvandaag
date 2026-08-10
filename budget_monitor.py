"""
Tegoed- en kostenmonitor voor de twee beeldproviders.

FAL.ai gebruikt de officiële Platform APIs (admin-scoped key):
- account/billing → actueel tegoed (current_balance)
- models/usage    → werkelijke kosten over een periode (geen schatting)

Gemini heeft zoiets niet. Er is **geen billing-endpoint op een Gemini API-key**:
Google verwijst voor kosten naar de Cloud Billing-console, en die is alleen met
OAuth/service-account te bevragen — en loopt bovendien uren achter. Google's
eigen advies is daarom de `usage` van elk antwoord te loggen en zelf op te
tellen. Dat doen we hier: `record_gemini_usage()` schrijft per gegenereerd beeld
een regel in `gemini_usage.json` en `get_gemini_cost()` telt die op.

Die berekening is geen schatting. Een 1K-beeld rapporteerde 1120
image-output-tokens; à $60/1M is dat $0.0672, precies de $0.067 die Google zelf
voor een 1K-beeld noemt.

Let op het verschil in betaalmodel: FAL.ai is prepaid (er ís een resterend
tegoed), Google is postpaid (je krijgt achteraf een rekening). Een "resterend
tegoed" bestaat bij Gemini dus niet; wil je toch een plafond zien, zet dan
GEMINI_MONTHLY_BUDGET in .env — dan rekent het rapport terug hoeveel daarvan
nog over is.
"""

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from config import (
    FAL_API_KEY,
    FAL_ADMIN_API_KEY,
    GEMINI_MONTHLY_BUDGET,
    GEMINI_USAGE_FILE,
    LOGS_DIR,
)

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


# ---------------------------------------------------------------------------
# Gemini (Nano Banana) — eigen grootboek, want er is geen billing-endpoint
# ---------------------------------------------------------------------------

GEMINI_CONSOLE_LINK = "https://console.cloud.google.com/billing"

# Prijs per 1M tokens, standaard (niet-batch) tier. Bron: ai.google.dev/gemini-api/docs/pricing.
# Verifieerbaar tegen de per-beeld-prijzen van Google: 1120 image-tokens × $60/1M
# = $0.0672 ≈ de $0.067 die zij voor een 1K-beeld noemen.
_GEMINI_PRICING = {
    "gemini-3.1-flash-image": {"input": 0.50, "text_output": 3.00, "image_output": 60.00},
    "gemini-3.1-flash-lite-image": {"input": 0.50, "text_output": 3.00, "image_output": 60.00},
}
_GEMINI_PRICING_DEFAULT = _GEMINI_PRICING["gemini-3.1-flash-image"]

# Zonder grens groeit het grootboek eeuwig; twee maanden is ruim genoeg voor
# "vandaag" en "deze maand" en houdt het bestand klein.
_LEDGER_RETENTION_DAYS = 62


def cost_from_usage(usage: dict, model: str) -> float:
    """Reken het `usage`-blok van één Gemini-antwoord om naar dollars.

    Pre:  usage is het usage-object uit een Interactions-antwoord
    Post: kosten in USD. Onbekend model → tarieven van gemini-3.1-flash-image;
          een onleesbaar usage-blok levert 0.0 op in plaats van een fout, want
          dit mag de beeldgeneratie nooit onderuithalen.
    """
    try:
        price = _GEMINI_PRICING.get(model, _GEMINI_PRICING_DEFAULT)
        input_tokens = float(usage.get("total_input_tokens") or 0)
        output_total = float(usage.get("total_output_tokens") or 0)
        image_tokens = sum(
            float(m.get("tokens") or 0)
            for m in (usage.get("output_tokens_by_modality") or [])
            if m.get("modality") == "image"
        )
        # Wat niet als beeld is gerapporteerd is tekst/denkwerk en kost minder.
        text_tokens = max(output_total - image_tokens, 0.0)
        return (
            input_tokens * price["input"]
            + text_tokens * price["text_output"]
            + image_tokens * price["image_output"]
        ) / 1_000_000
    except Exception as exc:
        logger.warning("Gemini-kosten berekenen mislukt: %s", exc)
        return 0.0


def _load_ledger() -> list:
    """Lees het grootboek; ontbrekend of corrupt levert een lege lijst op."""
    try:
        with open(GEMINI_USAGE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def record_gemini_usage(usage: dict, model: str) -> None:
    """Boek één gegenereerd beeld in `gemini_usage.json`.

    Pre:  usage komt uit een geslaagd Interactions-antwoord
    Post: één regel toegevoegd (tijdstip, model, tokens, kosten) en regels ouder
          dan _LEDGER_RETENTION_DAYS opgeruimd. Faalt stil met een waarschuwing:
          een boekhoudprobleem mag nooit een beeld kosten.
    """
    try:
        now = datetime.now(CET)
        ledger = _load_ledger()
        ledger.append({
            "ts": now.isoformat(),
            "model": model,
            "cost": round(cost_from_usage(usage, model), 6),
            "total_tokens": usage.get("total_tokens"),
        })
        cutoff = now - timedelta(days=_LEDGER_RETENTION_DAYS)
        ledger = [e for e in ledger if _parse_ts(e.get("ts")) and _parse_ts(e["ts"]) >= cutoff]
        with open(GEMINI_USAGE_FILE, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2)
    except Exception as exc:
        logger.warning("Gemini-verbruik vastleggen mislukt: %s", exc)


def _parse_ts(value) -> datetime | None:
    """ISO-tijdstip uit het grootboek naar datetime; None bij onzin."""
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CET)
    except (TypeError, ValueError):
        return None


def get_gemini_cost(start: datetime) -> dict:
    """Tel de geboekte Gemini-kosten vanaf `start`.

    Post: dict — success, cost (USD), images (aantal beelden), error.
          success=True ook bij een leeg grootboek: nul beelden is een geldige
          uitkomst, geen storing.
    """
    try:
        entries = [e for e in _load_ledger()
                   if (ts := _parse_ts(e.get("ts"))) and ts >= start]
        return {
            "success": True,
            "cost": round(sum(float(e.get("cost") or 0) for e in entries), 4),
            "images": len(entries),
            "error": None,
        }
    except Exception as exc:
        return {"success": False, "cost": None, "images": 0, "error": str(exc)}


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

    # Gemini: geen tegoed maar verbruik. `remaining` bestaat alleen als er een
    # eigen maandplafond is ingesteld — Google is postpaid en kent geen saldo.
    g_today = get_gemini_cost(today_start)
    g_month = get_gemini_cost(month_start)
    budget = GEMINI_MONTHLY_BUDGET or None
    remaining = None
    if budget and g_month.get("cost") is not None:
        remaining = round(budget - g_month["cost"], 2)

    gemini = {
        "success": g_today["success"] and g_month["success"],
        "today_cost": g_today.get("cost"),
        "month_cost": g_month.get("cost"),
        "today_images": g_today.get("images"),
        "month_images": g_month.get("images"),
        "budget": budget,
        "remaining": remaining,
        "currency": "USD",
        "error": g_today.get("error") or g_month.get("error"),
        "link": GEMINI_CONSOLE_LINK,
    }
    return {"fal": fal, "gemini": gemini}


if __name__ == "__main__":
    r = collect_funds_report()["fal"]
    if r["success"]:
        bal = f"${r['available']:.2f}" if r["available"] is not None else "onbekend"
        today = f"${r['today_cost']:.2f}" if r["today_cost"] is not None else "—"
        month = f"${r['month_cost']:.2f}" if r["month_cost"] is not None else "—"
        print(f"FAL.ai tegoed: {bal} USD | vandaag: {today} | deze maand: {month}")
    else:
        print(f"FAL.ai: {r.get('error', 'onbekend')} → {r.get('link', '')}")
