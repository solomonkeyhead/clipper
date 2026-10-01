"""Your X posts and their numbers, via X's official API (D83).

X's API is pay-per-use since February 2026: $0.005 per post read and $0.010
per user lookup, paid from credits bought at console.x.com; there is no free
tier for new developers. A post read twice in one UTC day is charged once, and
a request that returns nothing costs nothing. So Clipper:

- asks for new posts only, since the last one it saw (`since_id`), every
  `SYNC_HOURS` -- free when there are none -- the first time at most
  `FIRST_LOOK` posts back;
- reads views again only for posts younger than `REFRESH_DAYS`, a hundred per
  request, which X charges once a day each.

Posting one clip a day costs about $2 a month (14 live posts x $0.005 x 30). It reads with the app's
Bearer Token (X_BEARER_TOKEN in .env): everything it needs -- impressions,
likes, replies, reposts, bookmarks -- is in each post's public metrics, so no
sign-in is involved. One file per account under data/x/accounts.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from ..paths import data_root, ensure

API = "https://api.x.com/2"
ENV_TOKEN = "X_BEARER_TOKEN"
SYNC_HOURS = 1
FIRST_LOOK = 20
REFRESH_DAYS = 14  # campaigns pay on a post's first days of views
PRICE_PER_POST = 0.005


class XError(RuntimeError):
    """No token, the account doesn't exist, out of credits, or X returned an error."""


@dataclass
class Post:
    """One post, shaped like instagram.api.Reel so the same sync fills the log."""

    id: str
    caption: str
    created: float
    url: str
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    avg_watch_s: float | None = None
    skip_rate_pct: float | None = None


def accounts_dir() -> Path:
    return data_root() / "x" / "accounts"


def account_files() -> list[Path]:
    folder = accounts_dir()
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _save(account: dict) -> Path:
    path = ensure(accounts_dir()) / f"{account['username'].lower()}.json"
    path.write_text(json.dumps(account, indent=1), encoding="utf-8")
    return path


def remove(username: str) -> bool:
    path = accounts_dir() / f"{username.lower().lstrip('@')}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def token() -> str:
    found = os.environ.get(ENV_TOKEN, "").strip()
    if not found:
        raise XError("add your X app's Bearer Token on the Accounts page first")
    return found


def _get(path: str, params: dict) -> dict:
    try:
        r = httpx.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token()}"}, timeout=30)
    except httpx.HTTPError as exc:
        raise XError(f"couldn't reach X: {exc}") from exc
    if r.status_code == 401:
        raise XError("X refused the Bearer Token: copy it again from your app's Keys and tokens")
    if r.status_code == 402 or ("credits" in r.text.lower() and r.status_code >= 400):
        raise XError("your X API credits have run out: add some on the X developer console")
    if r.status_code == 429:
        raise XError("X says too many requests; the next sync tries again")
    if r.status_code >= 400:
        try:
            body = r.json()
            message = body.get("detail") or body.get("title") or r.text[:200]
        except ValueError:
            message = r.text[:200]
        raise XError(message)
    return r.json()


def connect(username: str) -> dict:
    """Look the account up (one paid user read) and keep it."""
    username = username.strip().lstrip("@")
    if not re.fullmatch(r"\w{1,15}", username):
        raise XError("that isn't an X username (letters, numbers and _, up to 15)")
    data = _get(f"/users/by/username/{username}", {}).get("data")
    if not data:
        raise XError(f"X has no account @{username}")
    account = {"username": data["username"], "user_id": data["id"], "name": data.get("name", ""),
               "since_id": "", "last_sync": 0.0, "connected_at": time.time()}
    _save(account)
    return account


def due(account: dict, now: float | None = None) -> bool:
    return (now or time.time()) - float(account.get("last_sync") or 0) >= SYNC_HOURS * 3600


def _post(item: dict, username: str) -> Post:
    m = item.get("public_metrics") or {}
    created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).timestamp() \
        if item.get("created_at") else 0.0
    # X adds a t.co link for the attached video; it isn't part of the caption.
    caption = re.sub(r"\s*https://t\.co/\w+", "", item.get("text") or "").strip()
    return Post(id=item["id"], caption=caption, created=created,
                url=f"https://x.com/{username}/status/{item['id']}",
                views=int(m.get("impression_count", 0)), likes=int(m.get("like_count", 0)),
                comments=int(m.get("reply_count", 0)),
                shares=int(m.get("retweet_count", 0)) + int(m.get("quote_count", 0)),
                saves=int(m.get("bookmark_count", 0)))


FIELDS = {"tweet.fields": "created_at,public_metrics"}


def new_posts(account: dict) -> list[Post]:
    """Posts since the last sync (replies and reposts left out), newest first."""
    params = {**FIELDS, "exclude": "replies,retweets", "max_results": 100}
    if account.get("since_id"):
        params["since_id"] = account["since_id"]
    else:
        params["max_results"] = FIRST_LOOK
    out: list[Post] = []
    while True:
        got = _get(f"/users/{account['user_id']}/tweets", params)
        out += [_post(item, account["username"]) for item in got.get("data") or []]
        token_next = (got.get("meta") or {}).get("next_token")
        if not token_next or not account.get("since_id"):
            return out
        params["pagination_token"] = token_next


def refresh(ids: list[str], username: str) -> list[Post]:
    """Current numbers for posts already in the log, a hundred per request."""
    out: list[Post] = []
    for start in range(0, len(ids), 100):
        got = _get("/tweets", {**FIELDS, "ids": ",".join(ids[start:start + 100])})
        out += [_post(item, username) for item in got.get("data") or []]
    return out


def sync(path: Path, rows: list[dict[str, str]], *, now: float | None = None, force: bool = False) -> int:
    """Fill `rows` from one account, if it's due. Returns how many posts were read (and paid for)."""
    from ..instagram import sync as post_sync

    now = time.time() if now is None else now
    account = read(path)
    if not force and not due(account, now):
        return 0
    fresh = new_posts(account)
    seen = {p.id for p in fresh}
    cutoff = now - REFRESH_DAYS * 86400
    known = [r["video_id"] for r in rows if r.get("platform") == "x" and r.get("video_id")
             and r["video_id"] not in seen and _recent(r, cutoff)]
    posts = fresh + refresh(known, account["username"])
    post_sync.apply(posts, rows, account=account["username"], now=now, platform="x")
    if fresh:
        account["since_id"] = max(fresh, key=lambda p: int(p.id)).id
    account["last_sync"] = now
    account["posts_read"] = int(account.get("posts_read") or 0) + len(posts)
    _save(account)
    return len(posts)


def _recent(row: dict, cutoff: float) -> bool:
    try:
        return datetime.strptime(row.get("posted_at") or "", "%Y-%m-%d %H:%M").timestamp() >= cutoff
    except ValueError:
        return True  # no date yet: read it once more
