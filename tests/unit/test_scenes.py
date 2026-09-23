"""Scene boundaries for scripted TV, and clips that stay inside one scene.

The bugs these exist for, reported on real FX clips: a clip opening or closing
on a flash of the neighbouring scene, and a clip ending mid-exchange. Scene
edges must land on real camera cuts and never split a line of dialogue.
"""

from __future__ import annotations

from clipper.candidates.scenes import CutScan, build_scenes
from clipper.candidates.windows import _within_scenes
from clipper.config import CandidatesConfig
from clipper.models import Candidate, Scene, Scenes, Sentence
from clipper.render.layouts import FaceObservation, choose_layout


def line(i: int, start: float, end: float, gap: float = 0.3) -> Sentence:
    return Sentence(index=i, start=start, end=end, text=f"Line {i}.",
                    word_indices=(i, i + 1), gap_before=gap)


def talk(spans: list[tuple[float, float]]) -> list[Sentence]:
    out, prev = [], 0.0
    for i, (a, b) in enumerate(spans):
        out.append(line(i, a, b, gap=a - prev))
        prev = b
    return out


def cuts_with(special: dict[float, float]) -> CutScan:
    """Special cuts among many ordinary ones far from the dialogue, with enough
    very dissimilar ones there that they fill the most dissimilar 5%."""
    likeness = {100.0 + i * 0.5: 0.95 for i in range(100)}
    likeness.update({160.0 + i: 0.1 for i in range(6)})
    likeness.update(special)
    return CutScan(cuts=sorted(likeness), black=[], duration=200.0, likeness=likeness)


class TestBuildScenes:
    def test_llm_boundary_snaps_to_the_camera_cut_before_the_line(self):
        sentences = talk([(1, 5), (6, 10), (13, 17), (18, 22)])
        scan = CutScan(cuts=[4.0, 12.0, 15.0], black=[], duration=25.0,
                       likeness={4.0: 0.95, 12.0: 0.4, 15.0: 0.9})
        scenes = build_scenes("s", sentences, [(0, "a"), (2, "b")], scan)
        assert [(s.start, s.end) for s in scenes.scenes] == [(0.0, 12.0), (12.0, 25.0)]
        assert scenes.scenes[1].first_sentence == 2

    def test_boundary_without_a_cut_is_dropped(self):
        sentences = talk([(1, 5), (6, 10), (13, 17)])
        scan = CutScan(cuts=[2.0], black=[], duration=20.0, likeness={2.0: 0.9})
        scenes = build_scenes("s", sentences, [(0, "a"), (2, "b")], scan)
        assert len(scenes.scenes) == 1

    def test_prefers_a_cut_between_lines_over_one_inside_a_line(self):
        # A cutaway at 14.0 lands mid-line; the cut at 12.0 falls in the pause.
        sentences = talk([(1, 5), (6, 10), (12.5, 17), (18, 22)])
        scan = cuts_with({12.0: 0.8, 14.0: 0.7})
        scenes = build_scenes("s", sentences, [(0, "a"), (2, "b")], scan)
        assert scenes.scenes[1].start == 12.0

    def test_llm_boundary_inside_a_running_exchange_is_dropped(self):
        # "Deuces, big bros." / "Paul Beaker, I'm so sorry." 0.06s apart, same room.
        sentences = talk([(1, 5), (6, 10), (10.06, 14), (15, 22)])
        scan = cuts_with({10.03: 0.9})
        scenes = build_scenes("s", sentences, [(0, "a"), (2, "b")], scan)
        assert len(scenes.scenes) == 1

    def test_a_fade_to_black_is_always_a_boundary(self):
        sentences = talk([(1, 5), (6, 10), (13, 17), (18, 22)])
        scan = CutScan(cuts=[], black=[(10.5, 12.5)], duration=25.0)
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert [(s.start, s.end) for s in scenes.scenes] == [(0.0, 10.5), (12.5, 25.0)]

    def test_dissimilar_cut_in_a_long_silence_splits_without_the_llm(self):
        # The LLM missed the move to the kitchen; the look and the pause did not.
        sentences = talk([(1, 5), (6, 10), (14, 18), (19, 23)])
        likeness = {float(c): 0.95 for c in range(1, 25)}
        likeness[12.0] = 0.3
        scan = CutScan(cuts=sorted(likeness), black=[], duration=25.0, likeness=likeness)
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert [s.start for s in scenes.scenes] == [0.0, 12.0]

    def test_a_cut_unlike_anything_nearby_splits_in_a_short_pause(self):
        sentences = talk([(1, 5), (6, 10), (10.4, 14), (15, 22)])
        scan = cuts_with({10.2: 0.5})
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert 10.2 in [s.start for s in scenes.scenes]

    def test_a_line_running_across_even_a_distinct_cut_keeps_one_scene(self):
        # A one-second office flash under "What did you say? You said the C
        # word in a work meeting.", asked by the friends at home.
        sentences = talk([(0.0, 3.1), (3.4, 8), (9, 12)])
        scan = cuts_with({1.0: 0.5})
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert scenes.scenes[0].start == 0.0 and scenes.scenes[0].first_sentence == 0

    def test_lines_back_to_back_across_a_distinct_cut_keep_one_scene(self):
        # "What did you say?" ends 0.08s before "You said the C word..."
        sentences = talk([(0.0, 0.76), (0.84, 3.1), (3.4, 8), (9, 12)])
        scan = cuts_with({0.97: 0.5})
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert scenes.scenes[0].start == 0.0 and scenes.scenes[0].last_sentence >= 1

    def test_an_end_card_after_the_last_line_is_cut_off(self):
        sentences = talk([(1, 5), (6, 10), (11, 15.3)])
        scan = cuts_with({15.6: 0.67})
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert scenes.scenes[0].end == 15.6

    def test_boundaries_close_together_merge(self):
        sentences = talk([(1, 5), (6, 10), (14, 18), (19, 23)])
        likeness = {float(c): 0.95 for c in range(1, 25)}
        likeness[11.0] = 0.3
        likeness[13.0] = 0.2
        scan = CutScan(cuts=sorted(likeness), black=[], duration=25.0, likeness=likeness)
        scenes = build_scenes("s", sentences, [(0, "a")], scan)
        assert [s.start for s in scenes.scenes] == [0.0, 13.0]


def window(lo: int, hi: int, sentences: list[Sentence]) -> Candidate:
    return Candidate(candidate_id=f"w{lo}-{hi}", start=sentences[lo].start,
                     end=sentences[hi - 1].end, sentence_indices=(lo, hi), text="")


class TestWithinScenes:
    cfg = CandidatesConfig(min_seconds=5, max_seconds=60, target_seconds=(5, 60),
                           beat_gap=0.7)
    # Scene 0: lines 0-2 (0-20s). Scene 1: lines 3-5 (20-40s). Line 2 follows
    # line 1 with no pause, mid-exchange.
    sentences = (line(0, 1, 5, 1), line(1, 6, 10, 1.0), line(2, 10.2, 18, 0.2),
                 line(3, 22, 26, 4), line(4, 27, 31, 1.0), line(5, 31.1, 38, 0.1))
    scenes = Scenes(source_id="s", scenes=[
        Scene(index=0, start=0.0, end=20.0, first_sentence=0, last_sentence=2),
        Scene(index=1, start=20.0, end=40.0, first_sentence=3, last_sentence=5),
    ])

    def kept(self, lo: int, hi: int) -> list[Candidate]:
        return _within_scenes([window(lo, hi, list(self.sentences))], list(self.sentences),
                              self.scenes, self.cfg)

    def test_window_crossing_scenes_is_dropped(self):
        assert self.kept(1, 5) == []

    def test_whole_scene_snaps_to_its_cuts(self):
        (c,) = self.kept(0, 3)
        assert (c.start, c.end) == (0.0, 20.0)
        assert (c.scene_start, c.scene_end) == (0.0, 20.0)

    def test_window_ending_mid_exchange_is_dropped(self):
        # Line 2 follows line 1 after 0.2s: stopping at line 1 cuts a reply off.
        assert self.kept(0, 2) == []

    def test_window_ending_before_a_reply_is_dropped(self):
        # Line 4 follows line 3 after 1.0s: long enough to start on, not to end on.
        assert _within_scenes([window(3, 4, list(self.sentences))], list(self.sentences),
                              self.scenes, self.cfg) == []

    def test_window_starting_on_a_fragment_is_dropped(self):
        sentences = list(self.sentences)
        sentences[1] = sentences[1].model_copy(update={"text": "a threesome on my bed?"})
        assert _within_scenes([window(1, 3, sentences)], sentences, self.scenes, self.cfg) == []

    def test_window_starting_after_a_pause_is_kept(self):
        (c,) = self.kept(1, 3)
        assert c.start == 6 and c.end == 20.0


class TestKeepEveryone:
    def test_two_people_far_apart_are_both_framed(self):
        faces = [[FaceObservation(t=t, x=300, y=400, width=160, height=160),
                  FaceObservation(t=t, x=1450, y=400, width=160, height=160)]
                 for t in range(10)]
        plan = choose_layout(faces, src_w=1920, src_h=1080, out_w=1080, out_h=1920,
                             min_face_ratio=0.02, keep_everyone=True)
        assert plan.kind != "two_speaker_stack"
        if plan.kind == "blurred_fit":
            return  # the whole frame: nobody is cropped out
        x, w = plan.keyframes[0].x, plan.crop_width
        assert x <= 300 - 80 and x + w >= 1450 + 80, "someone was cropped out"


class TestLikeness:
    def test_returning_from_a_short_insert_is_not_a_change_of_place(self):
        """A long library shot, a 2s insert elsewhere, then the library again."""
        import cv2
        import numpy as np

        from clipper.candidates.scenes import _likeness

        library = np.full((72, 128, 3), (40, 90, 160), np.uint8)
        elsewhere = np.full((72, 128, 3), (160, 60, 30), np.uint8)
        frames = [(t / 4, library if not 40 <= t < 48 else elsewhere) for t in range(80)]
        likeness = _likeness([10.0, 12.0], frames, cv2)
        assert likeness[12.0] > 0.9, "the return matches the shot before the insert"
        assert likeness[10.0] > 0.9, "the library returns within the window"

    def test_moving_somewhere_new_for_good_is(self):
        import cv2
        import numpy as np

        from clipper.candidates.scenes import _likeness

        library = np.full((72, 128, 3), (40, 90, 160), np.uint8)
        elsewhere = np.full((72, 128, 3), (160, 60, 30), np.uint8)
        frames = [(t / 4, library if t < 40 else elsewhere) for t in range(120)]
        likeness = _likeness([5.0, 10.0, 15.0], frames, cv2)
        assert likeness[10.0] < 0.5
