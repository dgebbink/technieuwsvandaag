"""
Lokale uitgaventeller voor FAL.ai beeldgeneratie.

FAL.ai biedt geen publieke billing-API (alle endpoints geven 404), dus houden we
de uitgaven zelf bij: per gegenereerd beeld wordt een geschatte kostprijs
(config.FAL_COST_PER_IMAGE) bijgeteld in een per-dag ledger (fal_usage.json).
Het dagoverzicht (daily_digest.py) gebruikt get_usage_summary() om de uitgaven
van vandaag, deze maand en totaal te tonen.
"""
import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from config import FAL_USAGE_FILE, FAL_COST_PER_IMAGE, FAL_BUDGET_USD

logger = logging.getLogger(__name__)

CET = ZoneInfo("Europe/Amsterdam")


def _today_iso() -> str:
    """Datum van vandaag in CET (zelfde basis als het dagoverzicht)."""
    return datetime.now(CET).date().isoformat()


def _load() -> dict:
    """Lees de ledger; bij ontbreken/corruptie een lege dict."""
    try:
        with open(FAL_USAGE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    """Schrijf de ledger weg; faalt stil met een waarschuwing."""
    try:
        with open(FAL_USAGE_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Kon FAL-uitgaven niet opslaan: %s", exc)


def record_generation(cost: float | None = None, count: int = 1) -> None:
    """Boek een (of meer) gegenereerd beeld in op de ledger van vandaag.

    Pre:  cost is de geschatte kostprijs per beeld; None gebruikt de config-default.
    Post: de bucket voor vandaag in FAL_USAGE_FILE is opgehoogd met `count`
          beelden en `count * cost` aan kosten.
    """
    if cost is None:
        cost = FAL_COST_PER_IMAGE

    data = _load()
    today = _today_iso()
    bucket = data.setdefault(today, {"count": 0, "cost": 0.0})
    bucket["count"] = bucket.get("count", 0) + count
    bucket["cost"] = round(bucket.get("cost", 0.0) + cost * count, 6)
    _save(data)
    logger.info(
        "FAL-uitgave geboekt: +%d beeld(en) ($%.4f) — vandaag totaal $%.4f",
        count, cost * count, bucket["cost"],
    )


def get_usage_summary() -> dict:
    """Vat de FAL.ai-uitgaven samen voor het dagoverzicht.

    Post: dict met today_count/today_cost, month_count/month_cost,
          total_count/total_cost, cost_per_image, budget en remaining
          (remaining = budget - total_cost, of None als geen budget ingesteld).
    """
    data = _load()
    today = _today_iso()
    month_prefix = today[:7]  # "YYYY-MM"

    today_count = today_cost = 0
    month_count = month_cost = 0
    total_count = total_cost = 0
    today_cost = month_cost = total_cost = 0.0

    for day, bucket in data.items():
        if not isinstance(bucket, dict):
            continue
        c = int(bucket.get("count", 0))
        cost = float(bucket.get("cost", 0.0))
        total_count += c
        total_cost += cost
        if day.startswith(month_prefix):
            month_count += c
            month_cost += cost
        if day == today:
            today_count += c
            today_cost += cost

    budget = FAL_BUDGET_USD if FAL_BUDGET_USD > 0 else None
    remaining = round(budget - total_cost, 2) if budget is not None else None

    return {
        "today_count": today_count,
        "today_cost": round(today_cost, 4),
        "month_count": month_count,
        "month_cost": round(month_cost, 4),
        "total_count": total_count,
        "total_cost": round(total_cost, 4),
        "cost_per_image": FAL_COST_PER_IMAGE,
        "budget": budget,
        "remaining": remaining,
    }


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(get_usage_summary(), indent=2, ensure_ascii=False))
