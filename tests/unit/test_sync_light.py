"""Continuous syncing stays light: snapshots only on change, older reels daily."""

from __future__ import annotations

import time

from clipper.instagram import api as ig_api
from clipper.instagram import sync as ig_sync
from clipper.studio import db


def post(views, likes=1):
    return {"url": "https://x/1", "views_latest": views, "likes": likes}


def test_a_snapshot_is_kept_only_when_the_numbers_change(data_root):
    with db.connect() as con:
        assert db.add_snapshots(con, "2026-10-01 10:00", [post(100)]) == 1
        assert db.add_snapshots(con, "2026-10-01 10:15", [post(100)]) == 0
        assert db.add_snapshots(con, "2026-10-01 10:30", [post(150)]) == 1
        assert db.views_at(con, "2026-10-01 10:20") == {"https://x/1": 100}
        assert [p["views"] for p in db.history(con, "https://x/1")] == [100, 150]


def test_old_repeats_are_pruned_keeping_first_changes_and_latest(data_root):
    with db.connect() as con:
        rows = [("https://x/1", f"2026-10-01 10:{m:02d}", v) for m, v in ((0, 1), (1, 1), (2, 1), (3, 2), (4, 2))]
        con.executemany("INSERT INTO snapshots (url, at, views) VALUES (?, ?, ?)", rows)
        assert db.prune_snapshots(con) == 2
        assert [(p["at"][-2:], p["views"]) for p in db.history(con, "https://x/1")] == [("00", 1), ("03", 2), ("04", 2)]


def test_an_unread_reel_keeps_its_numbers():
    row = {"url": "https://ig/r", "views_latest": "900", "views_24h": "", "likes": "5", "shares": "3"}
    reel = ig_api.Reel(id="1", caption="c", created=time.time() - 3600, url="https://ig/r", likes=7, measured=False)
    ig_sync._update(row, reel, time.time())
    assert (row["views_latest"], row["views_24h"], row["likes"], row["shares"]) == ("900", "", "7", "3")


def test_older_reels_get_insights_once_a_day(monkeypatch):
    now = time.time()
    media = {"data": [
        {"id": "new", "media_product_type": "REELS", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(now - 86400))},
        {"id": "old", "media_product_type": "REELS", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+0000", time.gmtime(now - 40 * 86400))}]}
    asked = []

    def get(url, params):
        if url.endswith("/insights"):
            asked.append(url.split("/")[-2])
            return {"data": [{"name": "views", "values": [{"value": 10}]}]}
        return media

    monkeypatch.setattr(ig_api, "_get", get)
    reels = {r.id: r for r in ig_api.list_reels("t", older=False)}
    assert asked == ["new"] and reels["new"].measured and not reels["old"].measured
    ig_api.list_reels("t", older=True)
    assert asked[1:] == ["new", "old"]
