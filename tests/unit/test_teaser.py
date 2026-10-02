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
