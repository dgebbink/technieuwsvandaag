"""
Herbruikbare Bluesky-acties voor de social-groei-taak (follow met spacing).
Los van social_poster.py (dat is voor het posten van artikelen) — dit script
is voor account-groei: volgen van andere accounts, met throttling.

Gebruik:
    venv/bin/python3 socials/bluesky_actions.py follow <handle1> <handle2> ...
"""
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import BLUESKY_HANDLE, BLUESKY_APP_PASSWORD

BLUESKY_HOST = "https://bsky.social"


def login() -> dict:
    resp = requests.post(
        f"{BLUESKY_HOST}/xrpc/com.atproto.server.createSession",
        json={"identifier": BLUESKY_HANDLE, "password": BLUESKY_APP_PASSWORD},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"accessJwt": data["accessJwt"], "did": data["did"]}


def resolve_handle(handle: str) -> str:
    resp = requests.get(
        f"{BLUESKY_HOST}/xrpc/com.atproto.identity.resolveHandle",
        params={"handle": handle},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["did"]


def follow(session: dict, target_did: str) -> str:
    resp = requests.post(
        f"{BLUESKY_HOST}/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        json={
            "repo": session["did"],
            "collection": "app.bsky.graph.follow",
            "record": {
                "subject": target_did,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["uri"]


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "follow":
        print("Gebruik: bluesky_actions.py follow <handle1> <handle2> ...")
        sys.exit(1)

    handles = sys.argv[2:]
    session = login()
    print(f"Ingelogd als {BLUESKY_HANDLE}")

    for i, handle in enumerate(handles):
        try:
            did = resolve_handle(handle)
            uri = follow(session, did)
            print(f"OK follow {handle} ({did}) -> {uri}", flush=True)
        except Exception as exc:
            print(f"FOUT follow {handle}: {exc}", flush=True)

        if i < len(handles) - 1:
            pause = random.randint(120, 300)
            print(f"pauze {pause}s...", flush=True)
            time.sleep(pause)


if __name__ == "__main__":
    main()
