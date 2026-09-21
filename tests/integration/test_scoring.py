"""The full scoring pipeline on real synthesised speech, with the mock backend.

Deterministic end to end: no network, no API key, and the mock's scores are a
function of the candidate text, so the whole chain is reproducible.
"""

from __future__ import annotations

import pytest

from clipper import pipeline
from clipper.config import Config
from clipper.models import Scored, Signals

from ..conftest import needs_ffmpeg
from ..fixtures import speech

pytestmark = [pytest.mark.slow, needs_ffmpeg]

needs_sapi = pytest.mark.skipif(not speech.sapi_available(), reason="Windows SAPI unavailable")


@pytest.fixture(scope="module")
def cfg() -> Config:
    base = Config.load()
    return base.model_copy(update={
        "transcription": base.transcription.model_copy(update={"model": "small"}),
        "llm": base.llm.model_copy(update={"backend": "mock", "batch_size": 4}),
    })


@pytest.fixture
def dense(media_cache, data_root):
    return speech.speech_video(list(speech.DENSE_SCRIPT), name="dense")


@needs_sapi
class TestScorePipeline:
    def test_runs_end_to_end(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")

        assert outcome.candidates.candidates
        assert outcome.scored.scored
        assert len(outcome.scored.scored) == len(outcome.candidates.candidates)

    def test_available_signals_exclude_the_heatmap_for_a_local_file(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        assert "heatmap" not in outcome.available_signals
        assert {"llm", "audio", "text"} <= set(outcome.available_signals)

    def test_weights_are_renormalised_over_the_available_signals(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        assert sum(outcome.scored.weights_used.values()) == pytest.approx(1.0)
        assert "heatmap" not in outcome.scored.weights_used

    def test_artifacts_are_written_and_reloadable(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        work = data_root / "work" / outcome.info.source_id
        for name, model in (("signals.json", Signals), ("scored.json", Scored)):
            assert (work / name).is_file()
            assert model.load(work / name)

    def test_is_deterministic(self, dense, cfg, data_root):
        first = pipeline.score(str(dense.video), cfg, backend_override="mock",
                               force={"signals"})
        second = pipeline.score(str(dense.video), cfg, backend_override="mock",
                                force={"signals"})
        assert [s.composite for s in first.scored.scored] == \
               [s.composite for s in second.scored.scored]

    def test_a_second_run_makes_no_llm_calls(self, dense, cfg, data_root):
        pipeline.score(str(dense.video), cfg, backend_override="mock", force={"signals"})
        again = pipeline.score(str(dense.video), cfg, backend_override="mock",
                               force={"signals"})
        assert again.llm_usage is not None
        assert again.llm_usage.calls == 0, "the disk cache should cover every candidate"

    def test_composites_are_ordered_and_in_range(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        composites = [s.composite for s in outcome.scored.scored]
        assert composites == sorted(composites, reverse=True)
        assert all(0.0 <= c <= 1.0 for c in composites)

    def test_every_scored_candidate_keeps_its_raw_llm_total(self, dense, cfg, data_root):
        """Selection gates on this absolutely, so it must survive the pipeline."""
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        live = [s for s in outcome.scored.scored if not s.dropped]
        assert live
        assert all(0.0 <= s.raw["llm"] <= 10.0 for s in live)


@needs_sapi
class TestSelectionOnRealSpeech:
    def test_selected_clips_satisfy_every_constraint(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        selection = pipeline.choose(outcome, cfg)

        picked = [p.candidate for p in selection.picks]
        for i, a in enumerate(picked):
            for b in picked[i + 1:]:
                assert not a.overlaps(b)
                gap = max(a.start - b.end, b.start - a.end)
                assert gap >= cfg.candidates.min_gap_seconds

    def test_every_pick_clears_the_absolute_quality_bar(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        selection = pipeline.choose(outcome, cfg)
        for pick in selection.picks:
            assert pick.scored.raw["llm"] >= cfg.selection.min_llm_total

    def test_a_bar_above_every_score_yields_nothing_rather_than_filler(
        self, dense, cfg, data_root
    ):
        """The behaviour BUILD_BRIEF.md section 1 asks for.

        The bar is set from the observed maximum rather than a fixed number, so
        the test asserts the *behaviour* and not a property of this fixture.
        """
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        live = [s for s in outcome.scored.scored if not s.dropped and "llm" in s.raw]
        assert live, "fixture must produce at least one LLM-scored candidate"

        bar = min(10.0, max(s.raw["llm"] for s in live) + 0.01)
        if bar <= max(s.raw["llm"] for s in live):
            pytest.skip("a candidate scored a perfect 10; no bar can exceed it")

        strict = cfg.model_copy(update={
            "selection": cfg.selection.model_copy(update={"min_llm_total": bar})
        })
        selection = pipeline.choose(outcome, strict)
        assert selection.count == 0
        assert "quality bar" in selection.stopped_because

    def test_raising_the_bar_shifts_rejections_from_placement_to_quality(
        self, dense, cfg, data_root
    ):
        """Works even when a candidate scores a perfect 10.

        This fixture is 75 seconds long, so only one 20-55s clip can ever be
        placed without overlapping whatever the quality bar is -- pick counts
        cannot show the gate working here. What *can* be shown is that the gate
        changes why candidates are turned away. (The count behaviour itself is
        covered exhaustively by the unit tests in test_pick.py.)
        """
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")

        def quality_rejections(bar: float) -> int:
            strict = cfg.model_copy(update={
                "selection": cfg.selection.model_copy(update={"min_llm_total": bar})
            })
            reasons = pipeline.choose(outcome, strict).rejections.values()
            return sum(1 for r in reasons if "below the absolute minimum" in r)

        assert quality_rejections(0.0) == 0
        assert quality_rejections(10.0) > quality_rejections(5.0) > 0

    def test_every_rejection_carries_a_reason(self, dense, cfg, data_root):
        outcome = pipeline.score(str(dense.video), cfg, backend_override="mock")
        selection = pipeline.choose(outcome, cfg)
        picked = {p.scored.candidate_id for p in selection.picks}
        for entry in outcome.scored.scored:
            if entry.candidate_id not in picked:
                assert selection.rejections.get(entry.candidate_id)


@needs_sapi
class TestSponsorHandling:
    def test_an_ad_read_does_not_become_a_clip(self, media_cache, data_root, cfg):
        """Caught by the keyword pre-filter, the LLM flag, or both."""
        fixture = speech.speech_video(
            list(speech.DENSE_SCRIPT[:6]) + list(speech.SPONSOR_SCRIPT)
            + list(speech.DENSE_SCRIPT[6:]),
            name="withad",
        )
        outcome = pipeline.score(str(fixture.video), cfg, backend_override="mock")
        selection = pipeline.choose(outcome, cfg)
        for pick in selection.picks:
            lowered = pick.candidate.text.lower()
            assert "promo code" not in lowered
            assert "sponsored by" not in lowered
