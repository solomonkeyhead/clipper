"""D198: the idea planner sees how the channel's posted videos did, best first, leaving out the ones too new to say."""

from __future__ import annotations

from clipper.create import store, topics


def test_the_track_record_adds_platforms_and_skips_new_posts(monkeypatch):
    monkeypatch.setattr(store, "videos", lambda: [{"id": k, "script": {"title": f"Q{k}"}} for k in (1, 2, 3, 4, 5)])
    rows = [{"clip_id": "create-1", "views_latest": "100", "posted_at": "2026-01-01 10:00"},
            {"clip_id": "create-1", "views_latest": "500", "avg_view_pct": "52.8", "posted_at": "2026-01-01 10:00"},
            {"clip_id": "create-2", "views_latest": "70", "avg_view_pct": "22.7", "posted_at": "2026-01-02 10:00"},
            {"clip_id": "create-3", "views_latest": "300", "posted_at": "2026-01-03 10:00"},
            {"clip_id": "create-4", "views_latest": "9000", "posted_at": "2999-01-01 10:00"},   # just posted
            {"clip_id": "other-9", "views_latest": "8000", "posted_at": "2026-01-01 10:00"}]    # not this channel's
    assert topics.track_record(rows) == ["Q1: 600 views, 53% of it watched on average", "Q3: 300 views",
                                         "Q2: 70 views, 23% of it watched on average"]
    assert topics.track_record(rows[:3]) == []    # two videos say nothing yet
