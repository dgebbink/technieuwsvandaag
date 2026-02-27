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


def _parse_follower(f: dict) -> dict:
    """Normalises a raw Bluesky actor object into a follower dict.
    Pre:  f is a raw actor dict from the getFollowers response
    Post: dict with handle, displayName, description, createdAt
    """
    return {
        "handle":      f.get("handle", ""),
        "displayName": f.get("displayName", "") or f.get("handle", ""),
        "description": (f.get("description", "") or "").strip(),
        "createdAt":   f.get("createdAt", ""),
    }


def _get_followers_raw(session: dict, limit: int = 100) -> list[dict]:
    """Fetches up to `limit` most recent followers (raw actor dicts).
    Pre:  session is active
    Post: list of raw follower dicts, most recent first
    """
    resp = requests.get(
        f"{HOST}/xrpc/app.bsky.graph.getFollowers",
        headers=_auth(session),
        params={"actor": BLUESKY_HANDLE, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("followers", [])


def _get_new_followers(session: dict) -> list[dict]:
    """Fetches followers whose account was created today.
    Pre:  session is active
    Post: list of follower dicts — handle, displayName,
          description, createdAt — filtered to today only
    """
    today = date.today().isoformat()
    return [
        _parse_follower(f)
        for f in _get_followers_raw(session)
        if f.get("createdAt", "").startswith(today)
    ]


def _get_recent_followers(session: dict, n: int = 5) -> list[dict]:
    """Returns the n most recent followers with full details.
    Pre:  session is active; n >= 1
    Post: list of up to n follower dicts, most recent first
    """
    return [_parse_follower(f) for f in _get_followers_raw(session)[:n]]


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
        session          = _login()
        total_followers  = _get_follower_count(session)
        raw_followers    = _get_followers_raw(session)
        today            = date.today().isoformat()
        new_followers    = [
            _parse_follower(f) for f in raw_followers
            if f.get("createdAt", "").startswith(today)
        ]
        recent_followers = [_parse_follower(f) for f in raw_followers[:5]]
        todays_posts     = _get_todays_posts(session)

        posts_with_replies = []
        for post in todays_posts:
            replies = _get_replies(session, post["uri"]) if post["replyCount"] > 0 else []
            posts_with_replies.append({**post, "replies": replies})

        return {
            "success":          True,
            "handle":           BLUESKY_HANDLE,
            "total_followers":  total_followers,
            "new_followers":    new_followers,
            "recent_followers": recent_followers,
            "posts":            posts_with_replies,
            "error":            None,
        }

    except Exception as exc:
        logger.error("Bluesky rapport mislukt: %s", exc)
        return {
            "success":          False,
            "handle":           BLUESKY_HANDLE,
            "total_followers":  0,
            "new_followers":    [],
            "recent_followers": [],
            "posts":            [],
            "error":            str(exc),
        }


if __name__ == "__main__":
    r = collect_daily_bluesky_report()
    print(f"Success:     {r['success']}")
    print(f"Followers:   {r['total_followers']}")
    print(f"New today:   {len(r['new_followers'])}")
    print(f"Posts today: {len(r['posts'])}")
    if r["error"]:
        print(f"Error: {r['error']}")
