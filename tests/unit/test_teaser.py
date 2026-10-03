"""A clip opening on its payoff (render/teaser.py, D97), rendered for real."""

from __future__ import annotations

import shutil

from clipper.config import CampaignConfig, Config
from clipper.ingest.probe import probe
from clipper.render.teaser import prepend
from tests.conftest import needs_ffmpeg
from tests.fixtures.synthetic import vertical_video


@needs_ffmpeg
def test_the_payoff_plays_first_then_the_whole_clip(tmp_path, media_cache):
    clip = tmp_path / "clip.mp4"
    shutil.copy(vertical_video(duration=10.0, width=540, height=960), clip)
    config = Config()
    config = config.model_copy(update={"render": config.render.model_copy(update={"encoder": "libx264"})})
    prepend(clip, 6.0, 8.0, hook="you won't believe this", config=config, work_dir=tmp_path)
    after = probe(clip)
    assert abs(after.duration - 12.0) < 0.2 and after.has_audio
    assert (tmp_path / "clip.teaser.ass").exists()
    assert not (tmp_path / "clip.teased.mp4").exists()  # replaced in place


def test_a_failed_teaser_leaves_the_clip_as_it_was(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not a video")
    assert prepend(clip, 1.0, 2.0, hook="", config=Config(), work_dir=tmp_path) == clip
    assert clip.read_bytes() == b"not a video"


def test_briefs_that_forbid_re_edits_get_no_teaser():
    from clipper.campaign.edits import teaser_allowed

    def campaign(**extra):
        return CampaignConfig.model_validate({"name": "c", "source_authorization": "Vyro campaign FX: Adults S2",
                                              "scripted": True, **extra})

    assert teaser_allowed(campaign())
    assert not teaser_allowed(campaign(brief_rules="No re-edits of the dialogue."))
    assert not teaser_allowed(campaign(brief_rules="Post the clips as-is, unaltered."))
    assert not teaser_allowed(campaign(edits={"re_edit": False}))


class TestColdOpenRebuilt:
    """D107: open loops, signalled jumps, unscripted only, short setups, loops."""

    def test_never_on_scripted_footage(self):
        from clipper.campaign.edits import cold_open_allowed

        base = {"name": "c", "source_authorization": "Vyro campaign FX: Adults S2"}
        assert not cold_open_allowed(CampaignConfig.model_validate({**base, "scripted": True}))
        assert cold_open_allowed(CampaignConfig.model_validate({**base, "scripted": False}))
        assert not cold_open_allowed(CampaignConfig.model_validate(
            {**base, "scripted": False, "brief_rules": "No re-edits."}))

    def test_the_teaser_stops_before_the_line_lands(self):
        from clipper.render.teaser import open_loop

        words = [(10.0, 10.3, "It"), (10.3, 10.6, "looks"), (10.6, 10.8, "like"), (10.8, 10.9, "a"),
                 (11.0, 11.4, "giant"), (11.5, 12.0, "toddler.")]
        a, b = open_loop(10.0, 12.0, words)
        assert a == 10.0 - 0.12 and 10.9 < b < 11.0  # "It looks like a" ... then the cut
        assert open_loop(10.0, 11.0, words[:2]) is None  # too short to hold anything back
        assert open_loop(10.0, 12.0, None) == (10.0 - 0.12, 12.0)

    def test_no_held_back_word_shows_in_the_captions(self):
        from clipper.render.teaser import open_loop, page_starts

        ass = "\n".join(f"Dialogue: 0,0:00:{a},0:00:{b},Caption,,0,0,0,,{text}" for a, b, text in [
            ("10.02", "10.42", r"{\c&H00}Boy,{\r} and you come out there"),
            ("10.42", "11.40", r"Boy, {\c&H00}and{\r} you come out there"),
            ("11.40", "11.62", r"{\c&H00}with{\r} $30 worth of $5 s**t."),
            ("11.62", "13.40", r"with {\c&H00}$30{\r} worth of $5 s**t.")])
        assert page_starts(ass) == [10.02, 11.40]
        words = [(10.02, 10.36, "Boy,"), (10.42, 10.54, "and"), (10.54, 10.74, "you"), (10.74, 10.94, "come"),
                 (10.94, 11.3, "out"), (11.3, 11.4, "there"), (11.4, 11.62, "with"), (11.62, 12.22, "$30"),
                 (12.22, 12.54, "worth"), (12.54, 12.7, "of"), (12.7, 13.06, "$5"), (13.06, 13.4, "shit.")]
        assert open_loop(10.02, 13.4, words)[1] > 11.6  # by words alone, "with" is shown...
        assert open_loop(10.02, 13.4, words, page_starts(ass))[1] == 11.4  # ...but its page holds the punchline

    def test_no_hook_over_captions_moved_to_the_top(self):
        from clipper.render.teaser import captions_on_top

        ass = "\n".join([r"Dialogue: 0,0:00:01.00,0:00:02.00,Caption,,0,0,0,,so then",
                         r"Dialogue: 0,0:00:10.00,0:00:11.00,Caption,,0,0,300,,{\an8}boy you came"])
        assert captions_on_top(ass, 9.9, 11.4) and not captions_on_top(ass, 0.0, 3.0)

    def test_a_late_payoff_ends_the_clip_and_setups_stay_short(self):
        from clipper.runner import _end_on_payoff, _teaser

        c = CampaignConfig.model_validate({"name": "c", "source_authorization": "Vyro campaign FX: Adults S2",
                                           "duration": {"min_seconds": 15, "max_seconds": 60}})
        assert _end_on_payoff((40.0, 42.0), 0.0, 50.0, c) == 42.4   # late: the clip loops into its teaser
        assert _end_on_payoff((10.0, 12.0), 0.0, 50.0, c) == 50.0   # early: the clip runs on
        assert _end_on_payoff(None, 0.0, 50.0, c) == 50.0
        assert _teaser((46.0, 48.0), 0.0, 55.0, c) is None          # 46 s of setup is too long
        assert _teaser((20.0, 22.0), 0.0, 55.0, c) == (20.0, 22.0)


@needs_ffmpeg
def test_an_open_loop_teaser_with_a_bloom(tmp_path, media_cache):
    clip = tmp_path / "clip.mp4"
    shutil.copy(vertical_video(duration=10.0, width=540, height=960), clip)
    config = Config()
    config = config.model_copy(update={"render": config.render.model_copy(update={"encoder": "libx264"})})
    words = [(6.0, 6.4, "you"), (6.4, 6.8, "won't"), (6.8, 7.2, "believe"), (7.3, 7.6, "what"), (7.7, 8.0, "happened")]
    prepend(clip, 6.0, 8.0, hook="wait", config=config, work_dir=tmp_path, words=words)
    after = probe(clip)
    # "you won't believe" plus its lead-in, then the whole clip.
    assert abs(after.duration - (10.0 + (7.25 - 5.88))) < 0.15 and after.has_audio
