"""
Bluesky daily activity monitor for TechNieuwsVandaag.
Collects new followers, today's posts and their replies.
"""

import logging
from datetime import date

import requests

from config import BLUESKY_HANDLE, BLUESKY_APP_PASSWORD

logger = logging.getLogger(__name__)

HOST = "https://bsky.social"


def _login() -> dict:
    """Logs in to Bluesky and returns active session.
    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD in .env
    Post: dict with accessJwt, did, host
    """
    resp = requests.post(
        f"{HOST}/xrpc/com.atproto.server.createSession",
        json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    d = resp.json()
    return {"accessJwt": d["accessJwt"], "did": d["did"], "host": HOST}


def _auth(session: dict) -> dict:
    """Returns Authorization header dict.
    Pre:  session contains accessJwt
    Post: dict suitable for requests headers=
    """
    return {"Authorization": f"Bearer {session['accessJwt']}"}


def _get_follower_count(session: dict) -> int:
    """Returns total follower count from profile.
    Pre:  session is active
    Post: integer follower count, 0 on error
    """
    resp = requests.get(
        f"{HOST}/xrpc/app.bsky.actor.getProfile",
        headers=_auth(session),
        params={"actor": BLUESKY_HANDLE},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("followersCount", 0)


def _get_new_followers(session: dict) -> list[dict]:
    """Fetches followers who joined today.
    Pre:  session is active
    Post: list of dicts — handle, displayName, createdAt
          only entries where createdAt starts with today
    """
    today = date.today().isoformat()
    resp = requests.get(
        f"{HOST}/xrpc/app.bsky.graph.getFollowers",
        headers=_auth(session),
        params={"actor": BLUESKY_HANDLE, "limit": 100},
        timeout=15,
    )
    resp.raise_for_status()
    return [
        {
            "handle":      f.get("handle", ""),
            "displayName": f.get("displayName", "") or f.get("handle", ""),
            "createdAt":   f.get("createdAt", ""),
        }
        for f in resp.json().get("followers", [])
        if f.get("createdAt", "").startswith(today)
    ]


def _get_todays_posts(session: dict) -> list[dict]:
    """Fetches posts published today on the account.
    Pre:  session is active
    Post: list of post dicts — uri, text, likeCount,
          repostCount, replyCount, indexedAt
    """
    today = date.today().isoformat()
    resp = requests.get(
        f"{HOST}/xrpc/app.bsky.feed.getAuthorFeed",
        headers=_auth(session),
        params={"actor": BLUESKY_HANDLE, "limit": 50},
        timeout=15,
    )
    resp.raise_for_status()
    posts = []
    for item in resp.json().get("feed", []):
        post = item.get("post", {})
        if not post.get("indexedAt", "").startswith(today):
            continue
        record = post.get("record", {})
        posts.append({
            "uri":         post.get("uri", ""),
            "text":        record.get("text", "")[:120],
            "likeCount":   post.get("likeCount", 0),
            "repostCount": post.get("repostCount", 0),
            "replyCount":  post.get("replyCount", 0),
            "indexedAt":   post.get("indexedAt", ""),
        })
    return posts


def _get_replies(session: dict, uri: str) -> list[dict]:
    """Fetches external replies to a specific post.
    Pre:  session active; uri is a valid AT Protocol URI
    Post: list of reply dicts — handle, displayName, text
          own replies (BLUESKY_HANDLE) excluded
    """
    resp = requests.get(
        f"{HOST}/xrpc/app.bsky.feed.getPostThread",
        headers=_auth(session),
        params={"uri": uri, "depth": 3},
        timeout=15,
    )
    resp.raise_for_status()
    replies = []
    for r in resp.json().get("thread", {}).get("replies", []):
        post   = r.get("post", {})
        author = post.get("author", {})
        if author.get("handle", "") == BLUESKY_HANDLE:
            continue
        record = post.get("record", {})
        replies.append({
            "handle":      author.get("handle", ""),
            "displayName": author.get("displayName", "") or author.get("handle", ""),
            "text":        record.get("text", ""),
            "indexedAt":   post.get("indexedAt", ""),
        })
    return replies


def collect_daily_bluesky_report() -> dict:
    """Collects all Bluesky activity for the daily report.
    Pre:  BLUESKY_HANDLE and BLUESKY_APP_PASSWORD in .env
    Post: dict — success, handle, total_followers,
          new_followers, posts (each with replies list), error
    """
    try:
        session         = _login()
        total_followers = _get_follower_count(session)
        new_followers   = _get_new_followers(session)
        todays_posts    = _get_todays_posts(session)

        posts_with_replies = []
        for post in todays_posts:
            replies = _get_replies(session, post["uri"]) if post["replyCount"] > 0 else []
            posts_with_replies.append({**post, "replies": replies})

        return {
            "success":         True,
            "handle":          BLUESKY_HANDLE,
            "total_followers": total_followers,
            "new_followers":   new_followers,
            "posts":           posts_with_replies,
            "error":           None,
        }

    except Exception as exc:
        logger.error("Bluesky rapport mislukt: %s", exc)
        return {
            "success":         False,
            "handle":          BLUESKY_HANDLE,
            "total_followers": 0,
            "new_followers":   [],
            "posts":           [],
            "error":           str(exc),
        }


if __name__ == "__main__":
    r = collect_daily_bluesky_report()
    print(f"Success:     {r['success']}")
    print(f"Followers:   {r['total_followers']}")
    print(f"New today:   {len(r['new_followers'])}")
    print(f"Posts today: {len(r['posts'])}")
    if r["error"]:
        print(f"Error: {r['error']}")
