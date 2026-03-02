"""
Persistent token store for article approval/decline.
Uses a simple JSON file with 24h expiry per token.
"""

import json
import os
import secrets
from datetime import datetime, timedelta

STORE_FILE = "approval_tokens.json"
TTL_HOURS  = 24


def _load() -> dict:
    """Loads token store from disk.
    Pre:  STORE_FILE may or may not exist
    Post: returns dict of token -> metadata
    """
    if not os.path.exists(STORE_FILE):
        return {}
    with open(STORE_FILE, "r") as f:
        return json.load(f)


def _save(store: dict) -> None:
    """Persists token store to disk.
    Pre:  store is a valid dict
    Post: STORE_FILE written atomically
    """
    with open(STORE_FILE, "w") as f:
        json.dump(store, f, indent=2)


def create_tokens(
    post_id:    int,
    post_title: str,
    wp_url:     str,
    meta:       dict | None = None,
) -> tuple[str, str, str]:
    """Creates accept, decline and reimage tokens for a draft article.
    Pre:  post_id is a valid WordPress draft post ID
    Post: returns (accept_token, decline_token, reimage_token) strings
          tokens stored with 24h expiry
          meta dict (e.g. article_text) stored for reimage use
    """
    store         = _load()
    accept_token  = secrets.token_urlsafe(32)
    decline_token = secrets.token_urlsafe(32)
    reimage_token = secrets.token_urlsafe(32)
    expires_at    = (
        datetime.utcnow() + timedelta(hours=TTL_HOURS)
    ).isoformat()

    for token, action in [
        (accept_token,  "accept"),
        (decline_token, "decline"),
        (reimage_token, "reimage"),
    ]:
        store[token] = {
            "action":     action,
            "post_id":    post_id,
            "post_title": post_title,
            "wp_url":     wp_url,
            "expires_at": expires_at,
            "used":       False,
            "meta":       meta or {},
        }

    _save(store)
    return accept_token, decline_token, reimage_token


def get_token(token: str) -> dict | None:
    """Retrieves token metadata if valid and unexpired.
    Pre:  token is a string
    Post: returns metadata dict if valid and unused,
          None if not found, expired or already used
    """
    store = _load()
    entry = store.get(token)
    if not entry:
        return None
    if entry.get("used"):
        return None
    expires = datetime.fromisoformat(entry["expires_at"])
    if datetime.utcnow() > expires:
        return None
    return entry


def mark_used(token: str) -> None:
    """Marks a token as used to prevent replay.
    Pre:  token exists in store
    Post: token.used set to True in store
    """
    store = _load()
    if token in store:
        store[token]["used"] = True
        _save(store)


def cleanup_expired() -> int:
    """Removes expired and used tokens from store.
    Pre:  STORE_FILE exists
    Post: returns count of removed tokens
    """
    store  = _load()
    now    = datetime.utcnow()
    before = len(store)
    store  = {
        t: m for t, m in store.items()
        if not m.get("used")
        and datetime.fromisoformat(m["expires_at"]) > now
    }
    _save(store)
    return before - len(store)
