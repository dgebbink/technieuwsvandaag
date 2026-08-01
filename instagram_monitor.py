"""
Instagram daily activity monitor for TechNieuwsVandaag (Meta Graph API).

Alleen leesacties: profiel, posts van vandaag met likes/reacties en de stand
van de dagwachtrij. De Graph API kent geen volgerslijst (anders dan Bluesky),
dus de "nieuwe volgers"-regel komt uit een eigen dagelijkse telling in
INSTAGRAM_STATS_FILE. Insights (reach/profile views) vereisen de permissie
instagram_manage_insights; die heeft dit token niet, dus die vragen we niet op.
"""

import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from config import (
    INSTAGRAM_ACCESS_TOKEN,
    INSTAGRAM_ACCOUNT_ID,
    INSTAGRAM_API_VERSION,
    INSTAGRAM_QUEUE_FILE,
    INSTAGRAM_STATS_FILE,
)

logger = logging.getLogger(__name__)

CET = ZoneInfo("Europe/Amsterdam")

_HISTORY_DAYS = 60  # bewaartermijn in instagram_stats.json
_MAX_COMMENTS = 10  # per post opgehaald voor de mail


def _graph_base() -> str:
    return f"https://graph.facebook.com/{INSTAGRAM_API_VERSION}"


def _fetch_profile() -> dict:
    """Haalt het accountprofiel op.
    Pre:  INSTAGRAM_ACCOUNT_ID en INSTAGRAM_ACCESS_TOKEN zijn gezet
    Post: dict met username, followers_count, follows_count, media_count
    """
    resp = requests.get(
        f"{_graph_base()}/{INSTAGRAM_ACCOUNT_ID}",
        params={
            "fields": "username,followers_count,follows_count,media_count",
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _fetch_comments(media_id: str) -> list[dict]:
    """Haalt reacties op een post op (equivalent van de Bluesky-replies).
    Pre:  media_id is een geldig IG-media-ID
    Post: lijst van dicts met username, text, timestamp — leeg bij een fout,
          want een mislukte reactie mag het rapport niet blokkeren
    """
    try:
        resp = requests.get(
            f"{_graph_base()}/{media_id}/comments",
            params={
                "fields": "username,text,timestamp",
                "limit": _MAX_COMMENTS,
                "access_token": INSTAGRAM_ACCESS_TOKEN,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return [
            {
                "username":  c.get("username", ""),
                "text":      (c.get("text", "") or "").strip(),
                "timestamp": c.get("timestamp", ""),
            }
            for c in resp.json().get("data", [])
        ]
    except Exception as exc:
        logger.warning("Reacties ophalen mislukt (media %s): %s", media_id, exc)
        return []


def _post_type(media: dict) -> str:
    """Leesbaar posttype: onderscheidt de wekelijkse Reel van de dagdigest."""
    if media.get("media_product_type") == "REELS":
        return "Reel"
    if media.get("media_type") == "CAROUSEL_ALBUM":
        return "Carousel"
    if media.get("media_type") == "VIDEO":
        return "Video"
    return "Foto"


def _fetch_todays_media() -> list[dict]:
    """Haalt de posts op die vandaag (CET) zijn geplaatst.
    Pre:  credentials zijn gezet
    Post: lijst van post-dicts — id, type, permalink, time, caption,
          like_count, comments_count, comments — nieuwste eerst
    """
    resp = requests.get(
        f"{_graph_base()}/{INSTAGRAM_ACCOUNT_ID}/media",
        params={
            "fields": ("id,caption,media_type,media_product_type,permalink,"
                       "timestamp,like_count,comments_count"),
            "limit": 25,
            "access_token": INSTAGRAM_ACCESS_TOKEN,
        },
        timeout=15,
    )
    resp.raise_for_status()

    today = date.today()
    posts = []
    for media in resp.json().get("data", []):
        try:
            posted = datetime.fromisoformat(media["timestamp"]).astimezone(CET)
        except (KeyError, ValueError):
            continue
        if posted.date() != today:
            continue

        caption = (media.get("caption") or "").strip()
        comments_count = media.get("comments_count", 0) or 0
        posts.append({
            "id":             media.get("id", ""),
            "type":           _post_type(media),
            "permalink":      media.get("permalink", ""),
            "time":           posted.strftime("%H:%M"),
            "caption":        caption[:160],
            "like_count":     media.get("like_count", 0) or 0,
            "comments_count": comments_count,
            "comments":       _fetch_comments(media["id"]) if comments_count else [],
        })
    return posts


def _load_history() -> dict:
    """Leest de dagelijkse tellingen; lege dict als het bestand er nog niet is."""
    if not INSTAGRAM_STATS_FILE.exists():
        return {}
    try:
        with open(INSTAGRAM_STATS_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("instagram_stats.json onleesbaar: %s", exc)
        return {}


def _record_counts(profile: dict) -> tuple[int | None, str | None]:
    """Legt de telling van vandaag vast en geeft de groei sinds de vorige meting.

    De Graph API geeft geen volgerslijst en geen dagelijkse follower-insights
    zonder instagram_manage_insights, dus dit bestand is de enige bron voor
    "nieuwe volgers".

    Pre:  profile is de uitkomst van _fetch_profile()
    Post: (delta, datum van de vorige meting) — (None, None) als er nog geen
          eerdere dag is; het bestand bevat de laatste _HISTORY_DAYS dagen
    """
    today = date.today().isoformat()
    history = _load_history()

    previous_days = sorted(d for d in history if d < today)
    delta, since = None, None
    if previous_days:
        since = previous_days[-1]
        before = history[since].get("followers_count")
        now = profile.get("followers_count")
        if isinstance(before, int) and isinstance(now, int):
            delta = now - before

    history[today] = {
        "followers_count": profile.get("followers_count"),
        "follows_count":   profile.get("follows_count"),
        "media_count":     profile.get("media_count"),
    }
    for old in sorted(history)[:-_HISTORY_DAYS]:
        del history[old]

    try:
        with open(INSTAGRAM_STATS_FILE, "w", encoding="utf-8") as fh:
            json.dump(history, fh, indent=2, sort_keys=True)
    except OSError as exc:
        logger.warning("instagram_stats.json schrijven mislukt: %s", exc)

    return delta, since


def _queue_state() -> list[dict]:
    """Leest de dagwachtrij rechtstreeks (geen import van social_poster).

    Na een geslaagde digest hoort die leeg te zijn — iets wat er om 20:00 nog
    in staat, betekent dat de digest van 19:45 niet gelukt is.
    """
    if not INSTAGRAM_QUEUE_FILE.exists():
        return []
    try:
        with open(INSTAGRAM_QUEUE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("instagram_queue.json onleesbaar: %s", exc)
        return []


def collect_daily_instagram_report() -> dict:
    """Verzamelt alle Instagram-activiteit voor het dagrapport.
    Pre:  INSTAGRAM_ACCOUNT_ID en INSTAGRAM_ACCESS_TOKEN in .env
    Post: dict — success, username, followers_count, follows_count,
          media_count, followers_delta, delta_since, posts, queue, error
    """
    empty = {
        "success":         False,
        "username":        None,
        "followers_count": None,
        "follows_count":   None,
        "media_count":     None,
        "followers_delta": None,
        "delta_since":     None,
        "posts":           [],
        "queue":           [],
        "error":           None,
    }

    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        return {**empty,
                "error": "INSTAGRAM_ACCOUNT_ID/INSTAGRAM_ACCESS_TOKEN niet geconfigureerd"}

    try:
        profile = _fetch_profile()
        delta, since = _record_counts(profile)

        return {
            "success":         True,
            "username":        profile.get("username"),
            "followers_count": profile.get("followers_count"),
            "follows_count":   profile.get("follows_count"),
            "media_count":     profile.get("media_count"),
            "followers_delta": delta,
            "delta_since":     since,
            "posts":           _fetch_todays_media(),
            "queue":           _queue_state(),
            "error":           None,
        }
    except Exception as exc:
        logger.error("Instagram-rapport mislukt: %s", exc)
        return {**empty, "error": str(exc)}


if __name__ == "__main__":
    r = collect_daily_instagram_report()
    print(f"Success:     {r['success']}")
    print(f"Username:    {r['username']}")
    print(f"Followers:   {r['followers_count']} (delta {r['followers_delta']} sinds {r['delta_since']})")
    print(f"Volgt:       {r['follows_count']}")
    print(f"Posts totaal:{r['media_count']}")
    print(f"Posts vandaag: {len(r['posts'])}")
    for p in r["posts"]:
        print(f"  {p['time']} {p['type']:9s} ❤️ {p['like_count']} 💬 {p['comments_count']} {p['permalink']}")
    print(f"Wachtrij:    {len(r['queue'])}")
    if r["error"]:
        print(f"Error: {r['error']}")
