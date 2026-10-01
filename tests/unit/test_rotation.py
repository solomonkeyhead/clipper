"""The brief's hooks and captions spread across a whole campaign, not restarted per video (D85)."""

from __future__ import annotations

from clipper.campaign import rotation
from clipper.config import CampaignConfig


def campaign() -> CampaignConfig:
    return CampaignConfig.model_validate({"name": "plm", "source_authorization": "Official campaign footage, plm"})


def test_a_batch_walks_through_every_line_least_used_first(data_root):
    lines = ["a", "b", "c"]
    picks = [rotation.pick(campaign(), "hook", lines) for _ in range(7)]
    assert picks == ["a", "b", "c", "a", "b", "c", "a"]
    assert rotation.pick(campaign(), "caption", lines) == "a"  # each kind keeps its own count


def test_lines_already_in_the_library_count():
    clips = [{"title": "a", "caption": "b  #tag"}, {"title": "a", "caption": "b more"}, {"title": "x", "caption": ""}]
    assert rotation.used_in_library("plm", "hook", ["a", "b"], clips) == {"a": 2}
    assert rotation.used_in_library("plm", "caption", ["a", "b"], clips) == {"b": 2}
