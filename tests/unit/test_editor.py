"""Clips edited by hand in the editor (editing.py, D103)."""

from __future__ import annotations

import pytest
import yaml

from clipper.campaign.edits import permissions
from clipper.config import CampaignConfig, Config
from clipper.editing import ClipEdit, Piece, WordFix, apply_fixes, blocked, kept_words, problems
from clipper.ingest.probe import probe
from clipper.models import Word
from clipper.render.prepare import join_segments
from clipper.runner import edited_plan
from tests.conftest import needs_ffmpeg
from tests.fixtures.synthetic import vertical_video
from tests.unit import test_studio_app as studio_app
from tests.unit.test_studio_app import add_clip

campaigns = studio_app.campaigns  # the fixture


def campaign(**extra) -> CampaignConfig:
    return CampaignConfig.model_validate({
        "name": "c", "source_authorization": "Vyro campaign FX: Adults S2", "scripted": True,
        "duration": {"min_seconds": 5, "max_seconds": 60}, **extra})


def words(*spans: tuple[float, float, str]) -> list[Word]:
    return [Word(start=a, end=b, text=t) for a, b, t in spans]


class TestTidy:
    def test_sorted_clamped_and_slips_dropped(self):
        edit = ClipEdit(pieces=[Piece(start=20, end=30), Piece(start=-2, end=8),
                                Piece(start=10, end=10.1)]).tidy(duration=25)
        assert [(p.start, p.end) for p in edit.pieces] == [(0, 8), (20, 25)]

    def test_overlaps_merge_and_zooms_snap_to_a_step(self):
        edit = ClipEdit(pieces=[Piece(start=0, end=5, zoom=1.12), Piece(start=4, end=9, zoom=1.1)]).tidy(60)
        assert [(p.start, p.end, p.zoom) for p in edit.pieces] == [(0, 9, 1.1)]

    def test_a_zoom_change_isnt_a_cut(self):
        edit = ClipEdit(pieces=[Piece(start=0, end=4), Piece(start=4, end=8, zoom=1.2),
                                Piece(start=10, end=12)]).tidy(60)
        assert edit.cuts == 1 and edit.zoomed and edit.length == 10

    def test_the_hook_is_one_line_and_not_too_long(self):
        edit = ClipEdit(pieces=[Piece(start=0, end=5)], hook="  wait   for\nit " + "x" * 200).tidy(60)
        assert edit.hook.startswith("wait for it ") and len(edit.hook) == 120


class TestWhatTheBriefAllows:
    def test_defaults_are_open_to_a_person(self):
        # Clipper doesn't cut scripted scenes by itself, but the brief doesn't forbid it.
        perms = permissions(campaign())
        assert not perms.internal_cuts and "internal_cuts" not in blocked(perms)
        edit = ClipEdit(pieces=[Piece(start=0, end=5), Piece(start=8, end=14, zoom=1.1)], hook="ok")
        assert problems(edit, perms, campaign()) == []

    def test_a_brief_that_says_no_jump_cuts_stops_cuts(self):
        c = campaign(brief_rules="No jump cuts. No zooms.")
        edit = ClipEdit(pieces=[Piece(start=0, end=5), Piece(start=8, end=14, zoom=1.1)])
        found = problems(edit, permissions(c), c)
        assert any("no cuts inside" in p and "jump cuts" in p for p in found)
        assert any(p.startswith("no zooms") for p in found)

    def test_the_campaigns_length_limits(self):
        c = campaign()
        assert any("at least 5s" in p for p in problems(ClipEdit(pieces=[Piece(start=0, end=3)]), permissions(c), c))
        assert any("at most 60s" in p for p in problems(ClipEdit(pieces=[Piece(start=0, end=70)]), permissions(c), c))

    def test_nothing_kept(self):
        c = campaign()
        assert problems(ClipEdit(), permissions(c), c) == ["nothing is kept: set where the clip starts and ends"]


class TestWords:
    def test_only_words_in_kept_pieces(self):
        ws = words((0.0, 0.4, "so"), (0.5, 0.9, "um"), (1.0, 1.5, "listen"))
        edit = ClipEdit(pieces=[Piece(start=0, end=0.45), Piece(start=0.95, end=2)])
        assert [w.text for w in kept_words(ws, edit)] == ["so", "listen"]

    def test_fixes_replace_or_hide_the_nearest_word(self):
        ws = words((0.0, 0.4, "Chad"), (0.5, 0.9, "powers"), (1.0, 1.5, "uh"))
        fixed = apply_fixes(ws, [WordFix(at=0.52, text="Powers"), WordFix(at=1.0, text=""),
                                 WordFix(at=5.0, text="ignored")])
        assert [w.text for w in fixed] == ["Chad", "Powers"]
        assert fixed[1].start == 0.5  # times kept

    def test_the_plan_is_the_edits(self):
        ws = words((0.0, 0.4, "so"), (0.5, 0.9, "um"), (1.0, 1.5, "listen"))
        edit = ClipEdit(pieces=[Piece(start=0, end=0.45), Piece(start=0.95, end=6)], hook="wait",
                        fixes=[WordFix(at=1.0, text="LISTEN")])
        plan = edited_plan(edit, ws, config=Config(), campaign=campaign(), rank=1, attempt=1)
        assert plan.text == "so LISTEN" and plan.hook_text == "wait" and plan.hook_shown
        assert plan.edit == edit.model_dump() and plan.start == 0 and plan.end == 6
        assert "1 cut(s)" in " ".join(plan.refine_notes)


@needs_ffmpeg
def test_pieces_join_with_their_own_zooms(tmp_path, media_cache):
    source = vertical_video(duration=10.0, width=540, height=960)
    out = join_segments(source, [(1.0, 3.0), (5.0, 6.5)], tmp_path / "joined.mkv",
                        width=540, height=960, has_audio=True, zooms=[1.0, 1.2])
    joined = probe(out)
    assert abs(joined.duration - 3.5) < 0.15 and joined.has_audio


def test_briefs_that_keep_to_their_own_footage_block_b_roll():
    from clipper.campaign.edits import forbidden_by

    for brief in ("Only use the provided footage.", "No outside footage of any kind", "only use official clips"):
        assert "overlays" in forbidden_by(brief), brief
    assert "overlays" not in forbidden_by("Use the provided captions")


class TestApi:
    @pytest.fixture
    def client(self, data_root, campaigns):
        from fastapi.testclient import TestClient

        from clipper.studio.server import create_app

        return TestClient(create_app())

    def test_a_video_not_yet_read_asks_to_be_prepared(self, client):
        from clipper.paths import downloads_dir

        video = downloads_dir() / "episode.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"\x00" * 4096)
        view = client.get("/api/editor", params={"campaign": "chad-powers-s2", "source": str(video)}).json()
        assert view["prepared"] is False and view["name"] == "episode.mp4" and view["source_id"]

    def test_a_clip_whose_working_files_are_gone(self, client, data_root):
        clip = add_clip(data_root, start_s=10.0, end_s=40.0)
        assert client.get("/api/editor", params={"clip": clip}).status_code == 409

    def test_an_edit_the_brief_forbids_is_refused_with_its_words(self, client, campaigns, monkeypatch):
        from types import SimpleNamespace

        from clipper.ingest import download
        from clipper.studio import editor

        brief = yaml.safe_load((campaigns / "chad-powers-s2.yaml").read_text(encoding="utf-8"))
        brief["brief_rules"] = "No jump cuts."
        (campaigns / "chad-powers-s2.yaml").write_text(yaml.safe_dump(brief), encoding="utf-8")
        monkeypatch.setattr(editor, "prepared", lambda sid: True)
        monkeypatch.setattr(download, "load_info", lambda sid: SimpleNamespace(media=SimpleNamespace(duration=600.0)))
        edit = {"pieces": [{"start": 10, "end": 20}, {"start": 30, "end": 40}]}
        for path in ("/api/editor/preview", "/api/editor/save"):
            refused = client.post(path, json={"source_id": "a394", "campaign": "chad-powers-s2", "edit": edit})
            assert refused.status_code == 400 and "jump cuts" in refused.json()["detail"]
