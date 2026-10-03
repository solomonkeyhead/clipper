"""What's working: changes compared fairly (learn/compare.py, D105)."""

from __future__ import annotations

from datetime import datetime, timedelta

from clipper.learn.compare import MIN_EACH, PostFacts, Side, compare, hooks, verdict, views_at

T0 = datetime(2026, 9, 20, 12, 0)


def post(url, views_by_hour: dict[int, float], *, platform="instagram", skip=None, hook="", **traits) -> PostFacts:
    history = tuple((T0 + timedelta(hours=h), v, skip) for h, v in sorted(views_by_hour.items()))
    return PostFacts(url=url, platform=platform, posted_at=T0, history=history, traits=traits, hook=hook,
                     campaign="plm")


class TestViewsAtTwoDays:
    def test_the_last_sync_by_the_mark_not_today(self):
        assert views_at(post("a", {6: 100, 40: 300, 47: 350, 200: 9000})) == 350

    def test_a_sync_just_after_the_mark_stands_in(self):
        assert views_at(post("a", {6: 100, 55: 400})) == 400

    def test_too_young_or_no_sync_near_the_mark(self):
        assert views_at(post("a", {6: 100, 20: 150})) is None
        assert views_at(post("a", {6: 100, 300: 500})) is None  # nothing near 48 h


class TestVerdict:
    def test_needs_enough_on_both_sides(self):
        _, word = verdict(Side(posts=MIN_EACH, median_views=100, skip_rate=None), Side(posts=1, median_views=50, skip_rate=None))
        assert word.startswith("Not enough yet") and "without" in word and "with " not in word

    def test_close_medians_are_about_the_same(self):
        assert verdict(Side(3, 110, None), Side(3, 100, None))[1] == "About the same"
        assert verdict(Side(3, 300, None), Side(3, 100, None)) == (3.0, "3.0 times the views")
        assert verdict(Side(3, 50, None), Side(3, 100, None))[1] == "50% fewer views"


def test_each_change_split_by_platform_with_medians_and_skip_rates():
    posts = [post(f"y{i}", {48: 300 + i}, skip=20.0, cover=True) for i in range(3)]
    posts += [post(f"n{i}", {48: 100 + i}, skip=40.0, cover=False) for i in range(3)]
    posts += [post("t1", {48: 0}, platform="tiktok", cover=True)]
    found = {c.key: c for c in compare(posts)}
    ig, tt = found["cover"].rows[0], found["cover"].rows[1]
    assert (ig.platform, ig.yes.median_views, ig.no.median_views, ig.verdict) == ("instagram", 301, 101, "3.0 times the views")
    assert ig.yes.skip_rate == 20.0 and ig.no.skip_rate == 40.0
    assert tt.platform == "tiktok" and tt.verdict.startswith("Not enough yet")
    assert found["payoff"].rows == []  # no post has that trait


def test_hooks_ranked_best_first():
    posts = [post("a", {48: 50}, hook="line one"), post("b", {48: 500}, hook="line two"),
             post("c", {48: 700}, hook="line two"), post("d", {10: 9}, hook="line three")]
    found = hooks(posts)
    assert [(h.hook, h.posts, h.median_views) for h in found] == [("line two", 2, 600), ("line one", 1, 50)]
