"""Campaign compliance rules and the manifest/report outputs."""

from __future__ import annotations

import csv
import json

import pytest

from clipper.campaign import compliance
from clipper.campaign.manifest import (
    MANIFEST_COLUMNS,
    ClipRecord,
    write_outputs,
    write_rejection_reason,
)
from clipper.config import CampaignConfig
from clipper.learn import log as perf
from clipper.models import ClipPlan, LayoutPlan, MediaInfo, QACheck, QAReport, SourceInfo


def campaign(**overrides) -> CampaignConfig:
    base = dict(
        name="test",
        source_authorization="Whop campaign 'Test', official content bank",
        duration={"min_seconds": 15, "max_seconds": 60},
    )
    base.update(overrides)
    return CampaignConfig.model_validate(base)


def plan(**overrides) -> ClipPlan:
    base = dict(
        clip_id="001", candidate_id="c0", rank=1, start=10.0, end=40.0,
        text="We raised prices and lost four percent of customers.",
        hook_text="Why raising prices worked",
        suggested_caption="The ones who left were the loudest.",
        hashtags=["#pricing"],
        layout=LayoutPlan(kind="blurred_fit"),
    )
    base.update(overrides)
    return ClipPlan(**base)


def record(**overrides) -> ClipRecord:
    from pathlib import Path

    base = dict(
        plan=plan(),
        file=Path("001_clip.mp4"),
        qa=QAReport(clip_id="001", file="001_clip.mp4",
                    checks=[QACheck(name="duration", status="pass")]),
        compliance=compliance.ComplianceReport(clip_id="001"),
        components={"llm": 0.9, "audio": 0.5, "text": 0.7},
        raw={"llm": 7.8},
        llm_a_total=7.6,
        llm_b_total=8.0,
        rendered_duration=30.0,
    )
    base.update(overrides)
    return ClipRecord(**base)


def source() -> SourceInfo:
    return SourceInfo(
        source_id="abc123",
        media=MediaInfo(path="x.mp4", duration=3600.0, width=1920, height=1080,
                        fps=30.0, has_audio=True),
        title="A Long Interview",
    )


class TestForbiddenTerms:
    def test_no_terms_configured_passes(self):
        result = compliance._check_forbidden_terms("anything at all", campaign())
        assert result.passed

    def test_a_forbidden_term_fails(self):
        result = compliance._check_forbidden_terms(
            "we talked about crypto for an hour", campaign(forbidden_terms=["crypto"]))
        assert not result.passed
        assert "crypto" in result.detail

    def test_matching_is_whole_word(self):
        """Substring matching would make 'ai' fire on 'said', 'again', 'chair'."""
        result = compliance._check_forbidden_terms(
            "she said again from the chair", campaign(forbidden_terms=["ai"]))
        assert result.passed

    def test_the_whole_word_still_matches(self):
        result = compliance._check_forbidden_terms(
            "we use AI for this", campaign(forbidden_terms=["ai"]))
        assert not result.passed

    def test_matching_is_case_insensitive(self):
        result = compliance._check_forbidden_terms(
            "CRYPTO is the topic", campaign(forbidden_terms=["crypto"]))
        assert not result.passed

    def test_a_multi_word_term(self):
        result = compliance._check_forbidden_terms(
            "let us discuss price targets today",
            campaign(forbidden_terms=["price targets"]))
        assert not result.passed


class TestBrandMentions:
    def test_not_required(self):
        assert compliance._check_brand_mentions("anything", campaign()).passed

    def test_required_and_present(self):
        result = compliance._check_brand_mentions(
            "I use Acme every day",
            campaign(brand_mentions={"required": True, "terms": ["Acme"]}))
        assert result.passed

    def test_required_and_absent_fails(self):
        result = compliance._check_brand_mentions(
            "I use nothing at all",
            campaign(brand_mentions={"required": True, "terms": ["Acme"]}))
        assert not result.passed

    def test_requiring_a_mention_with_no_terms_is_a_config_error(self):
        with pytest.raises(ValueError, match="no terms"):
            campaign(brand_mentions={"required": True, "terms": []})


class TestHashtagsAndCredit:
    def test_required_hashtags_present(self):
        p = plan(hashtags=["#pricing", "#example"])
        assert compliance._check_hashtags(p, campaign(required_hashtags=["#example"])).passed

    def test_missing_hashtag_fails(self):
        result = compliance._check_hashtags(plan(), campaign(required_hashtags=["#example"]))
        assert not result.passed
        assert "#example" in result.detail

    def test_credit_in_the_caption(self):
        p = plan(suggested_caption="Great clip. Source: @creator")
        assert compliance._check_credit(p, campaign(required_credit_text="Source: @creator")).passed

    def test_missing_credit_fails(self):
        result = compliance._check_credit(plan(), campaign(required_credit_text="Source: @creator"))
        assert not result.passed

    def test_a_burned_credit_does_not_need_to_be_in_the_caption(self):
        result = compliance._check_credit(plan(), campaign(
            required_credit_text="Source: @creator", burn_credit_in_video=True))
        assert result.passed
        assert "burned" in result.detail


class TestApplyCampaignCaption:
    def test_required_hashtags_are_prepended(self):
        updated = compliance.apply_campaign_caption(
            plan(), campaign(required_hashtags=["#example"]))
        assert updated.hashtags[0] == "#example"
        assert "#pricing" in updated.hashtags

    def test_a_missing_hash_prefix_is_added(self):
        updated = compliance.apply_campaign_caption(
            plan(), campaign(required_hashtags=["example"]))
        assert "#example" in updated.hashtags

    def test_duplicates_are_removed_case_insensitively(self):
        updated = compliance.apply_campaign_caption(
            plan(hashtags=["#Pricing"]), campaign(required_hashtags=["#pricing"]))
        assert len(updated.hashtags) == 1

    def test_the_credit_is_appended_to_the_caption(self):
        updated = compliance.apply_campaign_caption(
            plan(), campaign(required_credit_text="Source: @creator"))
        assert "Source: @creator" in updated.suggested_caption

    def test_the_credit_is_not_appended_twice(self):
        c = campaign(required_credit_text="Source: @creator")
        once = compliance.apply_campaign_caption(plan(), c)
        twice = compliance.apply_campaign_caption(once, c)
        assert twice.suggested_caption.count("Source: @creator") == 1

    def test_a_burned_credit_is_not_added_to_the_caption(self):
        updated = compliance.apply_campaign_caption(plan(), campaign(
            required_credit_text="Source: @creator", burn_credit_in_video=True))
        assert "Source: @creator" not in updated.suggested_caption

    def test_applying_then_checking_passes(self):
        """`apply` must satisfy what `check` requires, or runs fail spuriously."""
        c = campaign(required_hashtags=["#example"], required_credit_text="Source: @creator")
        updated = compliance.apply_campaign_caption(plan(), c)
        report = compliance.check_clip(updated, c, duration=30.0)
        assert report.passed, report.summary()


class TestComplianceReport:
    def test_a_clean_clip_passes_every_rule(self):
        report = compliance.check_clip(plan(), campaign(), duration=30.0)
        assert report.passed
        assert len(report.rules) == 5 + len(campaign().platform_targets)  # + each platform's text (D81)

    def test_a_duration_violation_fails(self):
        report = compliance.check_clip(plan(), campaign(), duration=5.0)
        assert not report.passed
        assert any(r.name == "duration" for r in report.failures)

    def test_the_summary_names_the_failures(self):
        report = compliance.check_clip(plan(), campaign(), duration=5.0)
        assert "5.0s" in report.summary()


class TestManifestOutputs:
    @pytest.fixture(autouse=True)
    def _own_data_dir(self, data_root):
        """The performance log lives under the data root; never touch the real one."""
        self.data_root = data_root

    def test_writes_every_file(self, tmp_path):
        outputs = write_outputs([record()], info=source(), campaign=campaign(),
                                out_dir=tmp_path)
        for key in ("manifest_csv", "manifest_json", "performance_log", "report_md"):
            assert outputs[key].is_file(), key

    def test_manifest_columns_match_the_brief(self, tmp_path):
        write_outputs([record()], info=source(), campaign=campaign(), out_dir=tmp_path)
        with (tmp_path / "manifest.csv").open(encoding="utf-8") as handle:
            assert next(csv.reader(handle)) == MANIFEST_COLUMNS

    def test_one_row_per_clip(self, tmp_path):
        write_outputs([record(), record(plan=plan(clip_id="002"))],
                      info=source(), campaign=campaign(), out_dir=tmp_path)
        rows = list(csv.DictReader((tmp_path / "manifest.csv").open(encoding="utf-8")))
        assert len(rows) == 2

    def test_a_missing_signal_becomes_an_empty_cell_not_a_zero(self, tmp_path):
        """A blank heatmap column must not read as 'scored zero' in `learn`."""
        write_outputs([record()], info=source(), campaign=campaign(), out_dir=tmp_path)
        row = next(csv.DictReader((tmp_path / "manifest.csv").open(encoding="utf-8")))
        assert row["heatmap"] == ""
        assert row["audio"] != ""

    def test_each_clip_gets_a_row_in_the_performance_log(self, tmp_path):
        outputs = write_outputs([record(), record(plan=plan(clip_id="002"))],
                                info=source(), campaign=campaign(), out_dir=tmp_path)
        assert outputs["performance_log"] == self.data_root / "performance.xlsx"
        rows = perf.read(outputs["performance_log"])
        assert [r["clip_id"] for r in rows] == ["001", "002"]
        assert all(r["candidate_id"] and r["views_24h"] == "" for r in rows)

    def test_the_log_leads_with_the_caption_as_posted(self, tmp_path):
        """TikTok Studio lists posts by caption, so that is what rows are matched on."""
        outputs = write_outputs([record()], info=source(), campaign=campaign(), out_dir=tmp_path)
        (row,) = perf.read(outputs["performance_log"])
        assert next(iter(row)) == "caption"
        assert row["caption"].startswith("The ones who left were the loudest.")

    def test_a_rerun_keeps_logged_results_and_adds_only_new_clips(self, tmp_path):
        """Overwriting would destroy numbers typed in by hand; the old per-run
        templates went stale instead, still listing clips a re-run had replaced."""
        outputs = write_outputs([record()], info=source(), campaign=campaign(), out_dir=tmp_path)
        rows = perf.read(outputs["performance_log"])
        rows[0]["views_24h"] = "12345"
        perf.write(rows, outputs["performance_log"])
        write_outputs([record(), record(plan=plan(clip_id="002"))], info=source(),
                      campaign=campaign(), out_dir=tmp_path)
        rows = perf.read(outputs["performance_log"])
        assert [(r["clip_id"], r["views_24h"]) for r in rows] == [("001", "12345"), ("002", "")]

    def test_the_json_manifest_records_the_authorization(self, tmp_path):
        c = campaign(source_authorization="Whop campaign 'Alpha', content bank")
        write_outputs([record()], info=source(), campaign=c, out_dir=tmp_path)
        data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
        assert data["source_authorization"] == "Whop campaign 'Alpha', content bank"

    def test_the_report_explains_an_empty_result(self, tmp_path):
        write_outputs([], info=source(), campaign=campaign(), out_dir=tmp_path,
                      selection_note="no candidate met the quality bar")
        text = (tmp_path / "report.md").read_text(encoding="utf-8")
        assert "No clips" in text
        assert "better than returning filler" in text

    def test_the_report_lists_each_clip(self, tmp_path):
        write_outputs([record()], info=source(), campaign=campaign(), out_dir=tmp_path)
        text = (tmp_path / "report.md").read_text(encoding="utf-8")
        assert "Why raising prices worked" in text
        assert "001_clip.mp4" in text

    def test_the_report_lists_rejected_clips(self, tmp_path):
        failed = record(qa=QAReport(clip_id="002", file="002.mp4", checks=[
            QACheck(name="black_frames", status="fail", detail="2.0s of black frames")]))
        write_outputs([record()], info=source(), campaign=campaign(),
                      out_dir=tmp_path, rejected=[failed])
        text = (tmp_path / "report.md").read_text(encoding="utf-8")
        assert "rejected" in text
        assert "black frames" in text

    def test_unicode_survives_the_round_trip(self, tmp_path):
        p = plan(hook_text="café — naïve", suggested_caption="日本語のキャプション")
        write_outputs([record(plan=p)], info=source(), campaign=campaign(),
                      out_dir=tmp_path)
        assert "café" in (tmp_path / "report.md").read_text(encoding="utf-8")
        assert "日本語" in (tmp_path / "manifest.csv").read_text(encoding="utf-8")


class TestRejectionReason:
    def test_records_every_failure(self, tmp_path):
        failed = record(qa=QAReport(clip_id="001", file="001.mp4", checks=[
            QACheck(name="black_frames", status="fail", detail="2.0s black",
                    measured=2.0, limit=0.3),
            QACheck(name="duration", status="pass"),
        ]))
        path = write_rejection_reason(failed, tmp_path / "001.reason.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["qa_status"] == "fail"
        assert len(data["qa_failures"]) == 1
        assert data["qa_failures"][0]["measured"] == 2.0

    def test_includes_the_transcript_for_context(self, tmp_path):
        path = write_rejection_reason(record(), tmp_path / "001.reason.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "raised prices" in data["text"]


class TestBriefRequirements:
    """Options added for a real brief (FX "Adults" S2 on Vyro): a required
    tune-in line in the caption, audio that must not be changed, a 30s-2min
    length window, and no edits that could misrepresent a scene."""

    TUNE_IN = "Watch Adults season 2 on FX | Hulu"

    def test_the_required_caption_text_is_added(self):
        out = compliance.apply_campaign_caption(
            plan(suggested_caption="Every friend group has one of these"),
            campaign(required_caption_text=self.TUNE_IN))
        assert self.TUNE_IN in out.suggested_caption
        assert out.suggested_caption.startswith("Every friend group")

    def test_an_unpunctuated_caption_is_ended_before_the_required_text(self):
        out = compliance.apply_campaign_caption(
            plan(suggested_caption="That escalated quickly"),
            campaign(required_caption_text=self.TUNE_IN))
        assert out.suggested_caption.startswith(f"That escalated quickly. {self.TUNE_IN}")

    def test_the_required_caption_text_is_not_duplicated(self):
        out = compliance.apply_campaign_caption(
            plan(suggested_caption=f"Chaos. {self.TUNE_IN}"),
            campaign(required_caption_text=self.TUNE_IN))
        assert out.suggested_caption.count(self.TUNE_IN) == 1

    def test_the_campaign_window_shapes_the_candidates(self):
        """It used to be checked only after rendering: nothing between 55s and
        2 minutes could be made, and short clips were rendered to be rejected."""
        from clipper.config import Config, DurationBounds
        from clipper.runner import campaign_config

        cfg = campaign_config(Config(), campaign(
            duration=DurationBounds(min_seconds=30, max_seconds=120)))
        assert (cfg.candidates.min_seconds, cfg.candidates.max_seconds) == (30, 120)
        low, high = cfg.candidates.target_seconds
        assert 30 <= low <= high <= 120

    def test_the_hook_overlay_can_be_turned_off(self):
        from clipper.config import Config
        from clipper.runner import campaign_config

        assert campaign_config(Config(), campaign(hook_overlay=True)).render.show_hook_text
        assert not campaign_config(Config(), campaign(hook_overlay=False)).render.show_hook_text

    def test_original_audio_skips_loudness_normalisation(self):
        from pathlib import Path

        from clipper.render.graph import RenderSpec, build_audio_filter

        spec = RenderSpec(
            source=Path("s.mov"), output=Path("o.mp4"), start=0, duration=30,
            layout=LayoutPlan(kind="blurred_fit"), ass_path=None, fonts_dir=None,
            width=1080, height=1920, fps=30, encoder="libx264", loudness_lufs=-14,
            true_peak_dbtp=-1.5, crf=20, nvenc_cq=23, x264_preset="veryfast",
            audio_bitrate="192k", audio_rate=48_000, normalize_audio=False,
        )
        assert "loudnorm" not in build_audio_filter(spec)
        assert "loudnorm" in build_audio_filter(
            spec.__class__(**{**spec.__dict__, "normalize_audio": True}))


class TestCaptionRestrictions:
    def test_only_required_hashtags_drops_the_llms_suggestions(self):
        """Brief: no hashtags 'not affiliated with this campaign'. Real output
        carried #comedy, #awkward and #skit."""
        out = compliance.apply_campaign_caption(
            plan(hashtags=["#comedy", "#skit", "#AdultsFX"]),
            campaign(required_hashtags=("#AdultsFX", "#fxpartner"),
                     only_required_hashtags=True))
        assert out.hashtags == ["#AdultsFX", "#fxpartner"]

    def test_suggestions_are_kept_by_default(self):
        out = compliance.apply_campaign_caption(
            plan(hashtags=["#comedy"]), campaign(required_hashtags=("#AdultsFX",)))
        assert out.hashtags == ["#AdultsFX", "#comedy"]

    def test_a_clip_with_no_caption_gets_a_brief_supplied_one(self):
        examples = ("First example", "Second example")
        first = compliance.apply_campaign_caption(
            plan(suggested_caption="", rank=1), campaign(fallback_captions=examples))
        second = compliance.apply_campaign_caption(
            plan(suggested_caption="", rank=2), campaign(fallback_captions=examples))
        assert first.suggested_caption.startswith("First example")
        assert second.suggested_caption.startswith("Second example")

    def test_an_llm_caption_is_not_replaced(self):
        out = compliance.apply_campaign_caption(
            plan(suggested_caption="Never invite these friends to dinner."),
            campaign(fallback_captions=("Example",)))
        assert out.suggested_caption.startswith("Never invite")


class TestHashtagsInCaptionText:
    def test_hashtags_written_into_the_caption_are_removed_too(self):
        out = compliance.apply_campaign_caption(
            plan(suggested_caption="Stolen jewelry leads to an unexpected encounter. "
                                   "#comedy #funny #skits"),
            campaign(required_hashtags=("#AdultsFX",), only_required_hashtags=True))
        assert "#comedy" not in out.suggested_caption
        assert out.suggested_caption.startswith("Stolen jewelry leads")

    def test_they_are_left_alone_when_not_restricted(self):
        out = compliance.apply_campaign_caption(
            plan(suggested_caption="Wild. #comedy"), campaign())
        assert "#comedy" in out.suggested_caption


class TestBriefSuppliedText:
    """Briefs that supply their own captions, on-screen lines and focus
    (Chad Powers S2: romance only, "comedy-only clips will be rejected")."""

    def test_fixed_captions_replace_the_llms(self):
        c = campaign(fallback_captions=("nobody told me Chad Powers season 2 was THIS good",
                                        "I started Chad Powers for football and somehow ended up here"),
                     fixed_captions=True, required_caption_text="#ad")
        first = compliance.apply_campaign_caption(plan(rank=1), c)
        second = compliance.apply_campaign_caption(plan(rank=2), c)
        assert first.suggested_caption.startswith("nobody told me Chad Powers")
        assert second.suggested_caption.startswith("I started Chad Powers")
        assert "The ones who left" not in first.suggested_caption
        assert first.suggested_caption.rstrip().endswith("#ad")

    def test_without_fixed_captions_the_llms_caption_stays(self):
        c = campaign(fallback_captions=("brief caption",))
        assert compliance.apply_campaign_caption(plan(), c).suggested_caption.startswith(
            "The ones who left")

    def test_the_focus_is_added_to_both_prompts_and_keyed_apart(self):
        from clipper.llm.prompts import PROMPT_A, PROMPT_B, with_focus

        focused = with_focus(PROMPT_A, "Only Ricky + Russ chemistry; no comedy-only clips.")
        assert "CAMPAIGN FOCUS" in focused.system and "Ricky + Russ" in focused.system
        assert focused.cache_key != PROMPT_A.cache_key
        assert with_focus(PROMPT_B, "  ") is PROMPT_B, "no focus: the prompt is unchanged"

    def test_the_runner_passes_the_focus_to_scoring(self):
        from clipper.config import Config
        from clipper.runner import campaign_config

        cfg = campaign_config(Config(), campaign(selection_focus="Ricky + Russ only"))
        assert cfg.llm.campaign_focus == "Ricky + Russ only"

    def test_no_stray_full_stop_or_repeated_tag(self):
        c = campaign(fallback_captions=("they knew #chadpowers @chadpowershulu",),
                     fixed_captions=True, required_caption_text="#ad",
                     required_hashtags=["#chadpowers", "#hulu"], only_required_hashtags=True)
        text = compliance.full_caption(compliance.apply_campaign_caption(plan(), c))
        assert text == "they knew #chadpowers @chadpowershulu #ad  #hulu"
