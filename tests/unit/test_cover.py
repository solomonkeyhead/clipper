"""A clip's best still put first, as its cover (render/cover.py, D104)."""

from __future__ import annotations

import shutil

from clipper.config import Config
from clipper.ingest.probe import probe
from clipper.render.cover import FRAMES, Frame, best, caption_times, hook_showing, pick, put_first
from tests.conftest import needs_ffmpeg
from tests.fixtures.synthetic import vertical_video

ASS = """[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:03.00,Hook,,0,0,0,,WAIT FOR IT
Dialogue: 0,0:00:01.00,0:00:02.50,Caption,,0,0,0,,so then he
Dialogue: 0,0:00:04.00,0:00:05.00,Caption,,0,0,0,,said no
"""


def test_only_caption_lines_count_as_speaking():
    assert caption_times(ASS) == [(1.0, 2.5), (4.0, 5.0)]


def test_the_hook_isnt_drawn_twice():
    assert hook_showing(ASS, 0.75) and not hook_showing(ASS, 3.5)


def test_a_dark_frame_loses_to_a_lit_one():
    dark = Frame(t=1.0, sharp=900, light=0.15, colour=30, face=0.7, speaking=False)
    lit = Frame(t=2.0, sharp=600, light=0.9, colour=30, face=0.6, speaking=False)
    assert best([dark, lit]).t == 2.0


def test_a_clear_face_beats_a_sharper_empty_frame():
    found = [Frame(t=1.0, sharp=900, light=0.9, colour=40, face=0.0, speaking=False),
             Frame(t=2.0, sharp=500, light=0.8, colour=30, face=0.9, speaking=True),
             Frame(t=3.0, sharp=50, light=0.8, colour=30, face=0.9, speaking=False)]  # a blurred face
    assert best(found).t == 2.0
    assert best([]) is None


def test_between_lines_wins_a_tie():
    a = Frame(t=1.0, sharp=500, light=0.8, colour=30, face=0.8, speaking=True)
    b = Frame(t=2.0, sharp=500, light=0.8, colour=30, face=0.8, speaking=False)
    assert best([a, b]).t == 2.0


@needs_ffmpeg
def test_the_cover_is_held_two_frames_then_the_clip_plays(tmp_path, media_cache):
    clip = tmp_path / "clip.mp4"
    shutil.copy(vertical_video(duration=6.0, width=540, height=960), clip)
    config = Config()
    config = config.model_copy(update={"render": config.render.model_copy(update={"encoder": "libx264"})})
    at = pick(clip, 6.0)
    assert at is not None and 0.5 <= at < 5.5
    before = probe(clip).duration
    put_first(clip, at, hook="wait for it", config=config, work_dir=tmp_path, fps=30)
    after = probe(clip)
    assert abs(after.duration - (before + FRAMES / 30)) < 0.05 and after.has_audio
    assert not (tmp_path / "clip.covered.mp4").exists()  # replaced in place


def test_a_failed_cover_leaves_the_clip_as_it_was(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not a video")
    assert put_first(clip, 1.0, hook="", config=Config(), work_dir=tmp_path, fps=30) == clip
    assert clip.read_bytes() == b"not a video"
