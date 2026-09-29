"""The retention research's editing rules (docs/DECISIONS.md D59)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from clipper.campaign.edits import forbidden_by, permissions
from clipper.config import CampaignConfig
from clipper.models import Word
from clipper.render.captions import (
    MIN_PAGE_SECONDS,
    chunk_words,
    get_style,
    hook_duration,
    speech_rate,
)
from clipper.render.prepare import Darkness, lift_for
from clipper.render.tighten import Loudness, keep_segments, plan_cuts, remap_words


def campaign(**extra) -> CampaignConfig:
    return CampaignConfig.model_validate({"name": "t", "source_authorization": "test brief",
                                          "duration": {"min_seconds": 7, "max_seconds": 90}, **extra})


def w(start, end, text):
    return Word(start=start, end=end, text=text)


# ---------------------------------------------------------------- permissions

class TestPermissions:
    def test_scripted_scenes_keep_their_own_edit(self):
        p = permissions(campaign(scripted=True))
        assert p.content_type == "scripted" and not p.internal_cuts and p.visual_effects

    def test_podcasts_get_pauses_tightened(self):
        assert permissions(campaign()).internal_cuts

    @pytest.mark.parametrize("wording, off", [
        ("Do not alter or modify any Client intellectual property", {"internal_cuts", "visual_effects", "added_text"}),
        ("Use the clips as-is.", {"internal_cuts", "overlays"}),
        ("No jump cuts please", {"internal_cuts"}),
        ("No text overlays", {"added_text", "captions"}),
        ("Keep the original audio", {"audio_additions"}),
        ("No logos of any kind", {"overlays"}),
        ("No zooms or filters", {"visual_effects"}),
    ])
    def test_brief_wording_forbids(self, wording, off):
        assert off <= set(forbidden_by(wording))
        p = permissions(campaign(brief_rules=wording))
        assert not any(p.allowed[c] for c in off)

    def test_harmless_briefs_forbid_nothing(self):
        assert forbidden_by("Only footage from the official folder may be used. Tag @show.") == {}

    def test_an_explicit_setting_beats_the_wording(self):
        p = permissions(campaign(brief_rules="no jump cuts", edits={"internal_cuts": True}))
        assert p.internal_cuts and p.reasons["internal_cuts"] == "set in the campaign"

    def test_the_hook_switch_still_governs_added_text(self):
        assert not permissions(campaign(hook_overlay=False)).added_text


# ---------------------------------------------------------------- tightening

def talk(*spec):
    """(start, end, text) triples -> words."""
    return [w(a, b, t) for a, b, t in spec]


def silence_with_speech(words, total=40.0, rate=1000):
    """A loudness map: speech level on words, near silence elsewhere."""
    samples = np.full(int(total * rate), 3, dtype=np.int16)
    for word in words:
        samples[int(word.start * rate):int(word.end * rate)] = 8000
    return Loudness(samples, rate)


class TestTighten:
    CLIP = talk((0.0, 0.4, "So"), (0.4, 0.9, "here's"), (0.9, 1.4, "the"), (1.4, 2.0, "thing."),
                (3.2, 3.6, "Nobody"), (3.6, 4.0, "told"), (4.0, 4.5, "me."),
                (5.5, 5.9, "um"), (6.2, 6.6, "I"), (6.6, 7.0, "was"), (7.0, 7.6, "there,"),
                (9.0, 9.4, "and"), (9.4, 10.0, "then"), (10.0, 10.5, "it"), (10.5, 11.0, "hit."),
                (12.6, 13.0, "Wow."))

    def test_dead_air_and_fillers_come_out(self):
        loud = silence_with_speech(self.CLIP)
        cuts = plan_cuts(self.CLIP, 0.0, 13.5, loud)
        reasons = [c.reason for c in cuts]
        assert any("pause" in r for r in reasons) and any("filler" in r for r in reasons)
        for cut in cuts:  # never inside a word, never near one
            assert not any(word.start - 0.039 < cut.end and cut.start < word.end + 0.039
                           for word in self.CLIP if word.text != "um")

    def test_the_pause_before_the_payoff_keeps_its_beat(self):
        loud = silence_with_speech(self.CLIP)
        cuts = plan_cuts(self.CLIP, 0.0, 13.5, loud)
        # "Wow." is the payoff: its 1.6 s lead-in may shrink only to 0.8 s.
        before_payoff = [c for c in cuts if c.start >= 11.0]
        assert all(12.6 - 11.0 - c.length >= 0.8 - 1e-6 for c in before_payoff)

    def test_budgets_hold(self):
        loud = silence_with_speech(self.CLIP)
        cuts = plan_cuts(self.CLIP, 0.0, 13.5, loud)
        assert sum(c.length for c in cuts) <= 13.5 * 0.20 + 1e-6
        starts = sorted(c.start for c in cuts)
        assert all(b - a >= 2.5 for a, b in itertools.pairwise(starts))
        assert min(b - a for a, b in keep_segments(cuts, 0.0, 13.5)) >= 0.6

    def test_a_noisy_gap_is_not_dead_air(self):
        """Laughter or a reaction fills the gap: keep it."""
        loud = silence_with_speech(self.CLIP)
        rate = 1000
        loud_samples = np.full(int(40 * rate), 3, dtype=np.int16)
        for word in self.CLIP:
            loud_samples[int(word.start * rate):int(word.end * rate)] = 8000
        loud_samples[2000:3200] = 7000  # laughter between "thing." and "Nobody"
        cuts = plan_cuts(self.CLIP, 0.0, 13.5, Loudness(loud_samples, rate))
        assert not any(2.0 <= c.start < 3.2 for c in cuts)
        assert loud  # (the quiet version does cut there)

    def test_never_under_the_campaign_minimum(self):
        loud = silence_with_speech(self.CLIP)
        assert plan_cuts(self.CLIP, 0.0, 13.5, loud, min_length=13.2) == []

    def test_words_follow_the_cuts(self):
        loud = silence_with_speech(self.CLIP)
        cuts = plan_cuts(self.CLIP, 0.0, 13.5, loud)
        segments = keep_segments(cuts, 0.0, 13.5)
        moved = remap_words(self.CLIP, segments)
        assert "um" not in [x.text for x in moved]
        assert moved[-1].end <= sum(b - a for a, b in segments) + 1e-6
        assert all(a.start <= b.start for a, b in itertools.pairwise(moved))


# ---------------------------------------------------------------- captions

class TestCaptionPages:
    def test_a_page_never_ends_on_the(self):
        words = [w(i * 0.4, i * 0.4 + 0.35, t) for i, t in enumerate(
            ["I", "went", "to", "the", "store", "and", "bought", "the", "biggest", "cake"])]
        for page in chunk_words(words, get_style("bold_pop")):
            if page is not chunk_words(words, get_style("bold_pop"))[-1]:
                assert page.words[-1].text.lower() not in {"the", "to", "and"}

    def test_fast_speech_gets_short_pages(self):
        fast = [w(i * 0.2, i * 0.2 + 0.18, f"w{i}") for i in range(12)]
        assert speech_rate(fast) > 3.3
        assert max(len(p.words) for p in chunk_words(fast, get_style("bold_pop"))) <= 3

    def test_a_blink_page_merges_into_the_next(self):
        words = [w(0.0, 0.2, "Hey!"), w(0.25, 0.4, "you"), w(0.45, 0.7, "there"),
                 w(2.0, 2.4, "listen"), w(2.4, 2.8, "closely")]
        pages = chunk_words(words, get_style("bold_pop"))
        for page, nxt in itertools.pairwise(pages):
            ends_sentence = page.words[-1].text.endswith(("!", ".", "?"))
            assert ends_sentence or nxt.start - page.start >= MIN_PAGE_SECONDS

    @pytest.mark.parametrize("text, seconds", [
        ("Wait for it", 2.5), ("She didn't know he heard every single word", 3.2),
        ("one two three four five six seven eight nine ten", 3.5), ("", 0.0)])
    def test_the_hook_stays_for_its_reading_time(self, text, seconds):
        assert hook_duration(text, 3.5) == pytest.approx(seconds, abs=0.01)


# ---------------------------------------------------------------- caption placement

class TestCaptionPlacement:
    CAPTION = (300, 986, 780, 1240)   # 2 lines, bottom at y=1240

    def test_a_clear_frame_leaves_the_caption_alone(self):
        from clipper.render.placement import place

        assert place(self.CAPTION, [(400, 300, 700, 700)], bottom_limit=1248,
                     top_limit=300, gap=40) is None

    def test_a_face_low_in_frame_sends_the_caption_to_the_top(self):
        from clipper.render.placement import place

        # A medium shot: face at y 800-1150; no room below the chin, room above.
        assert place(self.CAPTION, [(380, 800, 700, 1150)], bottom_limit=1248,
                     top_limit=300, gap=40) == ("top", 300)

    def test_a_face_above_the_caption_band_edge_goes_below_the_chin(self):
        from clipper.render.placement import place

        where = place((300, 986, 780, 1100), [(380, 700, 700, 1000)],
                      bottom_limit=1248, top_limit=300, gap=40)
        assert where is not None and where[0] == "below" and where[1] <= 1248

    def test_an_extreme_close_up_keeps_the_lower_third(self):
        """Nowhere free: stay over the chin rather than the eyes."""
        from clipper.render.placement import place

        assert place(self.CAPTION, [(180, 400, 850, 1330)], bottom_limit=1248,
                     top_limit=300, gap=40) is None

    def test_faces_map_through_a_letterboxed_crop(self):
        from clipper.models import CropKeyframe, LayoutPlan
        from clipper.render.placement import _to_output

        fit = LayoutPlan(kind="fit_crop", crop_width=1080, crop_height=1080,
                         keyframes=[CropKeyframe(t=0, x=420, y=0)])
        x0, y0, x1, y1 = _to_output(fit, (420, 0, 1500, 1080), 1080, 1920)
        assert (x0, x1) == (0, 1080) and (y0, y1) == (420, 1500)  # 1080px band, centred


# ---------------------------------------------------------------- dark footage

class TestDarkLift:
    def test_only_dark_footage_without_highlights_is_lifted(self):
        assert lift_for(Darkness(mean=0.09, p95=0.33, p99=0.4)) is not None
        assert lift_for(Darkness(mean=0.09, p95=0.7, p99=0.9)) is None   # lit faces
        assert lift_for(Darkness(mean=0.4, p95=0.8, p99=0.95)) is None
        assert lift_for(None) is None
