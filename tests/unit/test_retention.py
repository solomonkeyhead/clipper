"""The retention changes (docs/DECISIONS.md D52), each against its rule.

From the first real posts -- all three with views lost most viewers at 0:01 --
and the short-form retention research that followed: open on dialogue and
full-screen, a factual hook for the first 3 seconds, 20-45s clips, end a beat
after the last line, and log each clip's opening so posts can be compared.
"""

from __future__ import annotations

import pytest

from clipper.campaign.manifest import opening_label
from clipper.config import CampaignConfig, Config, SafeArea
from clipper.learn.analyze import Joined, compare
from clipper.models import CropKeyframe, LayoutPlan, LayoutSegment, Word
from clipper.render.captions import build_ass, get_style
from clipper.render.layouts import OPENING_MAX_ASPECT, FaceObservation, choose_layout
from clipper.render.shots import plan_per_shot
from clipper.runner import campaign_config, trim_silent_edges
from clipper.signals.combine import length_penalty

W, H = 1920, 1080


def face(t: float, x: float, size: float = 160) -> FaceObservation:
    return FaceObservation(t=t, x=x, y=420, width=size, height=size)


def two_far_apart(t: float) -> list[FaceObservation]:
    # One large face on the left (the most prominent), one smaller on the right.
    return [face(t, 360, 220), face(t, 1560, 150)]


class TestWideShotOfTwo:
    """Chad Powers Ep 6: two leads on a bench in a wide shot, faces 4.7% of the
    width in every sample, fell back to the whole frame as a 16:9 strip."""

    def test_small_faces_on_screen_throughout_are_framed(self):
        faces = [[face(t / 5, 760, 90), face(t / 5, 1160, 90)] for t in range(40)]
        plan = choose_layout(faces, src_w=W, src_h=H, out_w=1080, out_h=1920,
                             min_face_ratio=0.3, keep_everyone=True)
        assert plan.kind != "blurred_fit"
        x = plan.keyframes[0].x
        assert x <= 760 - 45 and x + plan.crop_width >= 1160 + 45

    def test_a_small_face_seen_now_and_then_is_still_background(self):
        faces = [[face(t / 5, 760, 90)] if t % 4 == 0 else [] for t in range(40)]
        plan = choose_layout(faces, src_w=W, src_h=H, out_w=1080, out_h=1920,
                             min_face_ratio=0.3, keep_everyone=True)
        assert "no face found" in plan.reason  # framed as faceless, not around it


class TestFacelessScriptedShots:
    """A dark crowd under blue light, profiles in a kiss: no face found. The
    whole frame there was a murky 16:9 strip over a third of the screen."""

    def test_the_centre_fills_the_screen_up_to_4_5(self):
        plan = choose_layout([[] for _ in range(30)], src_w=W, src_h=H, out_w=1080,
                             out_h=1920, min_face_ratio=0.3, keep_everyone=True)
        assert plan.kind == "fit_crop"
        assert plan.crop_width / plan.crop_height <= OPENING_MAX_ASPECT + 0.01
        assert plan.keyframes[0].x + plan.crop_width / 2 == pytest.approx(W / 2, abs=2)


class TestCloseUps:
    """Dark close-ups in the Chad Powers romance scenes (faces 30% of the frame
    width) were widened to keep the whole head and came out letterboxed."""

    def test_a_close_up_fills_the_screen_centred_on_the_face(self):
        faces = [[face(t / 5, 1130, 576)] for t in range(30)]
        plan = choose_layout(faces, src_w=W, src_h=H, out_w=1080, out_h=1920,
                             min_face_ratio=0.3, keep_everyone=True)
        assert plan.kind == "follow_crop"
        assert plan.keyframes[0].x + plan.crop_width / 2 == pytest.approx(1130, abs=2)

    def test_two_people_still_widen_to_keep_both(self):
        faces = [[face(t / 5, 600, 400), face(t / 5, 1300, 400)] for t in range(30)]
        plan = choose_layout(faces, src_w=W, src_h=H, out_w=1080, out_h=1920,
                             min_face_ratio=0.3, keep_everyone=True)
        assert plan.kind in ("fit_crop", "blurred_fit")


class TestOpeningFraming:
    def test_the_opening_never_zooms_out_past_4_5(self):
        faces = [two_far_apart(t / 5) for t in range(15)]
        plan = choose_layout(faces, src_w=W, src_h=H, out_w=1080, out_h=1920,
                             min_face_ratio=0.02, keep_everyone=True, opening=True)
        assert plan.kind in ("follow_crop", "fit_crop")
        assert plan.crop_width / plan.crop_height <= OPENING_MAX_ASPECT + 0.01
        x = plan.keyframes[0].x
        assert x <= 360 - 110 and x + plan.crop_width >= 360 + 110, \
            "the prominent person is fully in the opening frame"

    def test_later_shots_still_keep_everyone(self):
        faces = [two_far_apart(t / 5) for t in range(15)]
        plan = choose_layout(faces, src_w=W, src_h=H, out_w=1080, out_h=1920,
                             min_face_ratio=0.02, keep_everyone=True)
        x = plan.keyframes[0].x if plan.keyframes else 0
        width = plan.crop_width or W
        assert x <= 1560 - 75 and x + width >= 1560 + 75

    def test_a_long_wide_first_shot_is_tight_only_for_its_opening(self):
        times = [t / 5 for t in range(50)]  # one 10-second shot
        plan = plan_per_shot([two_far_apart(t) for t in times], times, [], duration=10.0,
                             src_w=W, src_h=H, out_w=1080, out_h=1920, min_face_ratio=0.02,
                             keep_everyone=True, opening_seconds=3.0)
        assert plan.kind == "per_shot"
        opening, rest = plan.segments
        assert (opening.start, opening.end, rest.end) == (0.0, 3.0, 10.0)
        assert opening.layout.crop_width < (rest.layout.crop_width or W)

    def test_off_by_default(self):
        times = [t / 5 for t in range(50)]
        plan = plan_per_shot([two_far_apart(t) for t in times], times, [], duration=10.0,
                             src_w=W, src_h=H, out_w=1080, out_h=1920, min_face_ratio=0.02,
                             keep_everyone=True)
        assert plan.kind != "per_shot"


def words(*spans) -> list[Word]:
    return [Word(start=a, end=b, text=" w") for a, b in spans]


class TestSilentEdges:
    def test_a_long_silent_opening_is_cut_to_half_a_second(self):
        assert trim_silent_edges(10.0, 40.0, words((14.8, 15.2), (16, 38.5)),
                                 lead=0.5, tail=1.5) == (pytest.approx(14.3), 40.0)

    def test_a_long_silent_ending_is_cut_to_the_reaction_beat(self):
        assert trim_silent_edges(10.0, 44.0, words((10.2, 11), (12, 38.5)),
                                 lead=0.5, tail=1.5) == (10.0, pytest.approx(40.0))

    def test_none_leaves_an_edge_alone(self):
        assert trim_silent_edges(10.0, 44.0, words((14.8, 15.2), (16, 38.5)),
                                 lead=None, tail=None) == (10.0, 44.0)


class TestLength:
    def test_over_the_target_costs_a_little_per_second(self):
        assert length_penalty(40.0, 45.0) == (1.0, "")
        factor, why = length_penalty(65.0, 45.0)
        assert factor == pytest.approx(0.9) and "over the 45s target" in why
        assert length_penalty(200.0, 45.0)[0] == pytest.approx(0.75), "capped at -25%"


class TestScriptedSettings:
    def test_a_scripted_campaign_gets_the_retention_settings(self):
        campaign = CampaignConfig.model_validate({
            "name": "fx", "source_authorization": "Vyro campaign FX: Adults S2",
            "duration": {"min_seconds": 30, "max_seconds": 120}, "scripted": True})
        cfg = campaign_config(Config(), campaign)
        assert cfg.candidates.max_seconds == 90 and cfg.candidates.target_seconds == (30, 45)
        assert cfg.candidates.prefer_target_length
        assert cfg.refine.max_lead_in == 0.5 and cfg.refine.max_tail == 1.5
        assert cfg.refine.post_roll == 1.0 and cfg.refine.tail_guard > 0
        assert cfg.render.opening_full_screen_seconds == 3.0
        assert cfg.render.hook_text_seconds >= 3.0

    def test_other_campaigns_are_unchanged(self):
        campaign = CampaignConfig.model_validate({
            "name": "pod", "source_authorization": "My own podcast footage",
            "duration": {"min_seconds": 20, "max_seconds": 60}})
        cfg = campaign_config(Config(), campaign)
        assert not cfg.candidates.prefer_target_length
        assert cfg.refine.max_lead_in is None
        assert cfg.render.opening_full_screen_seconds == 0.0


class TestSafeZone:
    def test_captions_keep_clear_of_the_right_hand_buttons(self):
        ass = build_ass(words((0.1, 0.5)), style=get_style("bold_pop"), width=1080,
                        height=1920, safe_area=SafeArea())
        caption = next(line for line in ass.splitlines() if line.startswith("Style: Caption"))
        margin_l, margin_r, margin_v = (int(v) for v in caption.split(",")[-4:-1])
        assert margin_r > margin_l and margin_v >= 400 * 1080 // 1080 - 1


class TestLogAndCompare:
    def test_opening_labels(self):
        full = LayoutPlan(kind="follow_crop", crop_width=608, crop_height=1080,
                          keyframes=[CropKeyframe(t=0, x=0, y=0)])
        four_five = LayoutPlan(kind="fit_crop", crop_width=864, crop_height=1080)
        wide = LayoutPlan(kind="fit_crop", crop_width=1500, crop_height=1080)
        per_shot = LayoutPlan(kind="per_shot", segments=[
            LayoutSegment(start=0, end=3, layout=full),
            LayoutSegment(start=3, end=9, layout=wide)])
        assert [opening_label(p) for p in (full, four_five, wide, per_shot)] == [
            "full-screen", "fit 4:5", "fit wide", "full-screen"]

    def test_posts_are_compared_by_each_setting(self):
        def post(opening, hook, watched, drop):
            return Joined(row={"opening": opening, "hook": hook, "avg_watch_s": str(watched),
                               "drop_off_s": str(drop), "lead_in_s": "0.3"}, duration=40.0)
        joined = [post("full-screen", "He said it", 20, 5), post("full-screen", "", 16, 3),
                  post("fit wide", "", 4, 1)]
        found = dict(compare(joined))
        framing = {a.value: a for a in found["opening framing"]}
        assert framing["full-screen"].n == 2
        assert framing["full-screen"].watch_through == pytest.approx(0.45)
        assert framing["fit wide"].drop_off == 1
        assert "silent lead-in" not in found, "one side only: nothing to compare"


class TestHookText:
    def test_a_long_hook_breaks_onto_two_balanced_lines(self):
        from clipper.render.captions import hook_lines

        assert hook_lines("RUNNING FOR HOA PRESIDENT") == ["RUNNING FOR HOA PRESIDENT"]
        top, bottom = hook_lines("WHEN THE INTERPRETER REPEATS YOUR PRIVATE THOUGHTS")
        assert abs(len(top) - len(bottom)) <= 8 and max(len(top), len(bottom)) <= 30

    def test_the_hook_is_drawn_with_a_hard_line_break(self):
        ass = build_ass(words((0.1, 0.5)), style=get_style("bold_pop"), width=1080,
                        height=1920, safe_area=SafeArea(), duration=10.0,
                        hook_text="When the interpreter repeats your private thoughts",
                        hook_seconds=3.0)
        hook = next(line for line in ass.splitlines() if ",Hook," in line)
        assert r"\N" in hook

    def test_hook_rules_forbid_inventing_things_in_the_scene(self):
        from clipper.llm.prompts import PROMPT_A, PROMPT_B

        for variant in (PROMPT_A, PROMPT_B):
            assert "3-8 words" in variant.system and "group chat" in variant.system
            assert "never seen the show" in variant.system


class TestOpeningForAnySource:
    def test_a_podcast_group_shot_opens_full_screen_too(self):
        """85 South: four hosts on a couch opened as a small letterbox in 4 of 5
        clips, because only scripted campaigns had the opening rule."""
        times = [t / 5 for t in range(50)]
        couch = [[face(t, x, 120) for x in (300, 700, 1150, 1600)] for t in times]
        plan = plan_per_shot(couch, times, [], duration=10.0, src_w=W, src_h=H,
                             out_w=1080, out_h=1920, min_face_ratio=0.02, opening_seconds=3.0)
        first = plan.segments[0].layout if plan.kind == "per_shot" else plan
        assert first.kind in ("follow_crop", "fit_crop")
        assert first.crop_width / first.crop_height <= OPENING_MAX_ASPECT + 0.01
