"""
Instagram daily follower monitor for TechNieuwsVandaag (Meta Graph API).
Alleen leesacties (followers_count) — de Graph API biedt geen follow/DM-endpoints.
"""

import logging

import requests

from config import INSTAGRAM_ACCOUNT_ID, INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_API_VERSION

logger = logging.getLogger(__name__)


def collect_instagram_report() -> dict:
    """Collects current Instagram follower count for the daily report.
    Pre:  INSTAGRAM_ACCOUNT_ID and INSTAGRAM_ACCESS_TOKEN in .env
    Post: dict — success, username, followers_count, follows_count, media_count, error
    """
    if not INSTAGRAM_ACCOUNT_ID or not INSTAGRAM_ACCESS_TOKEN:
        return {
            "success": False, "username": None, "followers_count": None,
            "follows_count": None, "media_count": None,
            "error": "INSTAGRAM_ACCOUNT_ID/INSTAGRAM_ACCESS_TOKEN niet geconfigureerd",
        }

    try:
        resp = requests.get(
            f"https://graph.facebook.com/{INSTAGRAM_API_VERSION}/{INSTAGRAM_ACCOUNT_ID}",
            params={
                "fields": "username,followers_count,follows_count,media_count",
                "access_token": INSTAGRAM_ACCESS_TOKEN,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "success":         True,
            "username":        data.get("username"),
            "followers_count": data.get("followers_count"),
            "follows_count":   data.get("follows_count"),
            "media_count":     data.get("media_count"),
            "error":           None,
        }
    except Exception as exc:
        logger.error("Instagram-rapport mislukt: %s", exc)
        return {
            "success": False, "username": None, "followers_count": None,
            "follows_count": None, "media_count": None, "error": str(exc),
        }


if __name__ == "__main__":
    r = collect_instagram_report()
    print(f"Success:   {r['success']}")
    print(f"Username:  {r['username']}")
    print(f"Followers: {r['followers_count']}")
    print(f"Follows:   {r['follows_count']}")
    print(f"Media:     {r['media_count']}")
    if r["error"]:
        print(f"Error: {r['error']}")
