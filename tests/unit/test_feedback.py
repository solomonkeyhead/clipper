"""Learning from the user's clip ratings."""

from __future__ import annotations

from clipper.learn import feedback
from clipper.learn.feedback import Rated

DEFAULTS = {"hook_strength": 0.30, "standalone_clarity": 0.20, "payoff": 0.20,
            "emotional_intensity": 0.15, "quotability": 0.10, "ending_completeness": 0.05}


def clip(i, rating, hook, **kw):
    rubric = {k: 5.0 for k in DEFAULTS} | {"hook_strength": hook,
                                          "payoff": kw.pop("payoff", 5.0 + (i % 3))}
    return Rated(id=i, campaign=kw.pop("campaign", "c"), title=f"clip {i}", rating=rating,
                 reasons=kw.pop("reasons", []), score=kw.pop("score", hook),
                 rubric=rubric, text=f"words of clip {i}", **kw)


def test_spearman():
    assert feedback.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert feedback.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert feedback.spearman([1, 1, 1], [1, 2, 3]) is None


def test_weights_stay_put_until_there_is_enough_evidence():
    few = [clip(i, 3, 5) for i in range(feedback.MIN_FOR_WEIGHTS - 1)]
    assert feedback.learned_weights(few, DEFAULTS) == (DEFAULTS, len(few))


def test_a_dimension_that_tracks_the_ratings_gains_weight():
    # The hook predicts the rating perfectly; payoff is noise.
    clips = [clip(i, r, hook=r * 2.0) for i, r in enumerate([1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 3, 4])]
    weights, n = feedback.learned_weights(clips, DEFAULTS)
    assert n == 12
    assert weights["hook_strength"] > DEFAULTS["hook_strength"]
    assert abs(sum(weights.values()) - sum(DEFAULTS.values())) < 1e-3


def test_taste_lists_liked_and_disliked_clips_and_reasons():
    clips = [clip(1, 5, 8, reasons=["great_hook"]), clip(2, 1, 3, reasons=["needs_context"]),
             clip(3, 2, 4, reasons=["needs_context", "boring"]), clip(4, None, 6)]
    text = feedback.taste(clips, "c")
    assert "said it was good: \"clip 1\" (great opening)" in text
    assert "marked it not good: \"clip 2\"" in text
    assert "Moments they didn't like" in text and "needs context (2)" in text
    assert feedback.taste(clips[:2], "c") == ""  # too few to say anything


def test_posting_counts_as_liking_unless_rated():
    rows = [{"id": 1, "campaign": "c", "title": "a", "rating": None, "status": "posted", "reasons": "[]"},
            {"id": 2, "campaign": "c", "title": "b", "rating": 1, "status": "posted", "reasons": "[]"},
            {"id": 3, "campaign": "c", "title": "d", "rating": None, "status": "ready", "reasons": "[]"}]
    a, b, d = feedback.from_rows(rows)
    assert (a.rating, a.implicit) == (feedback.POSTED_AS, True)
    assert (b.rating, b.implicit) == (1, False)  # what the user said wins
    assert d.rating is None


def test_report_agreement_and_bands():
    clips = [clip(i, r, hook=4.0 + r, views=float(r * 1000)) for i, r in enumerate([1, 2, 3, 4, 5, 5])]
    report = feedback.report(clips, DEFAULTS)
    assert report.agreement_verdict == "strong" and report.views_verdict == "strong"
    assert report.rated == 6 and report.unrated == 0
    assert [b["clips"] for b in report.bands] == [1, 1, 1, 3]


class TestWhatARatingTeaches:
    """D86: ratings teach about the moment; edit problems and brief fit don't; views do."""

    def rated(self, rating, reasons=(), **extra):
        from clipper.learn.feedback import Rated

        return Rated(id=1, campaign="c", title="t", rating=rating, reasons=list(reasons), score=None,
                     rubric={}, text="", **extra)

    def test_not_good_for_the_edit_alone_says_nothing_against_the_moment(self):
        assert self.rated(1, ["bad_framing"]).moment is None
        assert self.rated(1, ["caption_errors", "great_hook"]).moment == 4.0  # skipped, but a good moment
        assert self.rated(1, ["off_brief"]).moment is None  # about that brief, not taste

    def test_a_bad_moment_with_good_points_is_mixed(self):
        assert self.rated(1).moment == 1.0  # one click, no reasons: taken as said
        assert self.rated(1, ["weak_hook"]).moment == 1.0
        assert self.rated(1, ["boring", "funny"]).moment == 2.0

    def test_views_move_a_posted_clip(self):
        assert self.rated(4, implicit=True, performance=2.0).moment == 5.0
        assert self.rated(4, implicit=True, performance=0.3).moment == 3.0
        assert self.rated(5, performance=1.0).moment == 5.0

    def test_the_taste_block_leaves_out_edit_problems_and_other_briefs(self):
        from clipper.learn.feedback import Rated, taste

        def clip(i, rating, reasons, campaign="c", performance=None):
            return Rated(id=i, campaign=campaign, title=f"clip {i}", rating=rating, reasons=reasons, score=None,
                         rubric={}, text="", performance=performance)

        block = taste([clip(1, 1, ["bad_framing", "great_hook"]), clip(2, 1, ["weak_hook"]),
                       clip(3, 5, ["funny"], performance=2.5), clip(4, 1, ["off_brief"], campaign="other")],
                      campaign="c")
        assert "framing" not in block.lower() and "clip 4" not in block
        assert "2.5x their usual views" in block and "great opening" in block
