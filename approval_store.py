"""
Persistent token store for article approval/decline.
Uses a simple JSON file with 4h expiry per token.
"""

import json
import os
import secrets
from datetime import datetime, timedelta

STORE_FILE = "approval_tokens.json"
TTL_HOURS  = 4


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
) -> tuple[str, str]:
    """Creates decline and new_image tokens for a published article.
    Pre:  post_id is a valid WordPress post ID
    Post: returns (decline_token, new_image_token) strings
          tokens stored with 4h expiry
          meta dict (e.g. article_text) stored for reimage use
    """
    store          = _load()
    decline_token  = secrets.token_urlsafe(32)
    new_image_token = secrets.token_urlsafe(32)
    expires_at     = (
        datetime.utcnow() + timedelta(hours=TTL_HOURS)
    ).isoformat()

    for token, action in [
        (decline_token,   "decline"),
        (new_image_token, "new_image"),
    ]:
        store[token] = {
            "action":      action,
            "post_id":     post_id,
            "post_title":  post_title,
            "wp_url":      wp_url,
            "expires_at":  expires_at,
            "used":        False,
            "meta":        meta or {},
            "bluesky_uri": "",
        }

    _save(store)
    return decline_token, new_image_token


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


def update_bluesky_uri(decline_token: str, uri: str) -> None:
    """Updates bluesky_uri on all tokens for the same post_id as decline_token.
    Pre:  decline_token exists in store
    Post: bluesky_uri set on every token sharing that post_id
    """
    store = _load()
    entry = store.get(decline_token)
    if not entry:
        return
    post_id = entry["post_id"]
    for token_data in store.values():
        if token_data.get("post_id") == post_id:
            token_data["bluesky_uri"] = uri
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
