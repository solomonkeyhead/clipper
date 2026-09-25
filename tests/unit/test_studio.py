"""Reading copied TikTok Studio analytics pages (real samples in fixtures/)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from clipper.cli import app
from clipper.learn import log as perf
from clipper.tiktok import studio

PAGES = Path(__file__).parents[1] / "fixtures" / "tiktok_studio"
ESCALATED = "That escalated quickly. Watch Adults season 2 on FX | Hulu @adultsfx  #AdultsFX #fxpartner"
FRIENDSHIP = "Friendship comes first, always. Watch Adults season 2 on FX | Hulu @adultsfx  #AdultsFX #fxpartner"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


class TestParse:
    def test_a_page_with_data(self):
        s = studio.parse(page("with_data.txt"))
        assert s.caption == ESCALATED
        assert (s.views, s.likes, s.comments, s.shares, s.saves) == (70, 1, 0, 0, 0)
        assert s.avg_watch_s == pytest.approx(5.23)
        assert s.watched_full_pct == pytest.approx(1.4)
        assert s.new_followers == 0 and s.drop_off_s == 1
        assert not s.processing

    def test_a_page_still_processing(self):
        s = studio.parse(page("processing.txt"))
        assert s.caption == FRIENDSHIP, "the sidebar's caption list is not the post"
        assert s.views == 0 and s.processing and s.drop_off_s is None

    def test_anything_else_is_ignored(self):
        assert not studio.looks_like_studio("my bank password is hunter2")
        assert studio.parse("Posted on 9/23/2026\n1\n2") is None


class TestFill:
    def test_numbers_land_in_the_row_with_that_caption(self):
        rows = [{"caption": FRIENDSHIP}, {"caption": ESCALATED, "likes": "1", "views_latest": "70"}]
        index, changed = studio.fill(studio.parse(page("with_data.txt")), rows)
        assert index == 1
        assert rows[1]["avg_watch_s"] == "5.23" and rows[1]["watched_full_pct"] == "1.4"
        assert rows[1]["drop_off_s"] == "1" and rows[1]["saves"] == "0"
        assert "studio_at" in rows[1] and "caption" not in changed

    def test_no_views_means_no_watch_time_recorded(self):
        """'0s watched' with nobody watching is not a result."""
        rows = [{"caption": FRIENDSHIP}]
        studio.fill(studio.parse(page("processing.txt")), rows)
        assert "avg_watch_s" not in rows[0] and "watched_full_pct" not in rows[0]

    def test_counts_shared_with_the_api_never_go_down(self):
        rows = [{"caption": ESCALATED, "likes": "9", "views_latest": "400"}]
        studio.fill(studio.parse(page("with_data.txt")), rows)
        assert rows[0]["likes"] == "9" and rows[0]["views_latest"] == "400"

    def test_an_unknown_caption_changes_nothing(self):
        rows = [{"caption": "Something else"}]
        assert studio.fill(studio.parse(page("with_data.txt")), rows) == (None, [])
        assert rows == [{"caption": "Something else"}]


class TestCommand:
    def test_collect_from_a_saved_page_updates_the_spreadsheet(self, data_root):
        perf.add_clips([perf.NewClip("s1", "002_7m00s", "c24", "FX", "Adults ep208", "f.mp4",
                                     64.2, caption=ESCALATED)])
        result = CliRunner().invoke(app, ["tiktok", "collect", "--file",
                                          str(PAGES / "with_data.txt")])
        assert result.exit_code == 0, result.output
        assert "most left at 1s" in result.output
        (row,) = perf.read()
        assert row["avg_watch_s"] == "5.23" and row["drop_off_s"] == "1"
