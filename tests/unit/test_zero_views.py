"""D198: an account whose posts all sit at 0 views is named after a sync."""

from __future__ import annotations

from datetime import datetime

from clipper.studio import stats


def _row(k: int, views: str, platform: str = "tiktok", day: int = 1) -> dict[str, str]:
    return {"platform": platform, "account": "clips", "url": f"https://www.tiktok.com/@clips/video/{k}",
            "posted_at": f"2026-10-{day:02d} 12:00", "views_latest": views, "clip_id": str(k)}


def test_a_run_of_old_posts_at_zero_is_named_and_a_fresh_or_seen_one_is_not():
    now = datetime(2026, 10, 11, 12, 0)
    rows = [_row(k, "0", day=1 + k) for k in range(5)] + [_row(9, "0", day=10)]   # the newest is a day old
    got = stats.zero_view_runs(rows, now)
    assert len(got) == 1 and "last 5 posts" in got[0] and got[0].startswith("TikTok @clips")
    rows[4]["views_latest"] = "120"
    assert stats.zero_view_runs(rows, now) == []                  # the newest old post was seen
    blank = [_row(k, "", platform="instagram", day=1 + k) for k in range(6)]
    assert stats.zero_view_runs(blank, now) == []                 # no numbers yet isn't zero
