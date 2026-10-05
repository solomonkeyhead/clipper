"""Source identity, stage caching, URL detection, and heatmap parsing."""

from __future__ import annotations

import pytest

from clipper.ingest.download import is_url, normalize_heatmap
from clipper.utils.cache import StageCache, content_hash, slugify, source_id_for_file


class TestIsUrl:
    @pytest.mark.parametrize("value", [
        "https://www.youtube.com/watch?v=abc",
        "http://example.com/v.mp4",
        "https://example.com",
    ])
    def test_urls(self, value):
        assert is_url(value)

    @pytest.mark.parametrize("value", [
        r"C:\videos\clip.mp4",
        r"C:/videos/clip.mp4",
        "clip.mp4",
        r".\relative\clip.mp4",
        r"\\server\share\clip.mp4",
        "",
    ])
    def test_windows_paths_are_not_urls(self, value):
        """A drive letter parses as a URL scheme, so this needs care."""
        assert not is_url(value)

    def test_a_scheme_without_a_host_is_not_a_url(self):
        assert not is_url("file:///C:/videos/clip.mp4")


class TestSourceId:
    def test_is_stable_across_calls(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"x" * 5000)
        assert source_id_for_file(f) == source_id_for_file(f)

    def test_differs_by_content(self, tmp_path):
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        a.write_bytes(b"x" * 5000)
        b.write_bytes(b"y" * 5000)
        assert source_id_for_file(a) != source_id_for_file(b)

    def test_differs_by_size(self, tmp_path):
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        a.write_bytes(b"x" * 5000)
        b.write_bytes(b"x" * 6000)
        assert source_id_for_file(a) != source_id_for_file(b)

    def test_does_not_depend_on_the_filename(self, tmp_path):
        a, b = tmp_path / "one.mp4", tmp_path / "two.mp4"
        a.write_bytes(b"x" * 5000)
        b.write_bytes(b"x" * 5000)
        assert source_id_for_file(a) == source_id_for_file(b)

    def test_detects_a_change_in_the_tail(self, tmp_path):
        """Only the edges are hashed, so the tail must genuinely be included."""
        size = 4 * 1024 * 1024
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        a.write_bytes(b"x" * size)
        b.write_bytes(b"x" * (size - 100) + b"y" * 100)
        assert source_id_for_file(a) != source_id_for_file(b)

    def test_is_a_short_hex_string(self, tmp_path):
        f = tmp_path / "a.bin"
        f.write_bytes(b"x" * 100)
        sid = source_id_for_file(f)
        assert len(sid) == 16
        assert all(c in "0123456789abcdef" for c in sid)

    def test_handles_a_file_smaller_than_one_chunk(self, tmp_path):
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"abc")
        assert source_id_for_file(f)


class TestContentHash:
    def test_is_order_sensitive_across_arguments(self):
        assert content_hash("a", "b") != content_hash("b", "a")

    def test_is_stable_for_equal_inputs(self):
        assert content_hash({"k": 1}, 2) == content_hash({"k": 1}, 2)

    def test_ignores_dict_key_order(self):
        assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})

    def test_prompt_version_changes_the_key(self):
        """Bumping a prompt must invalidate cached LLM scores."""
        assert content_hash("gemini", "v1", "text") != content_hash("gemini", "v2", "text")


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello There, World!") == "hello-there-world"

    def test_collapses_separators(self):
        assert "--" not in slugify("a -- b  ,  c")

    def test_truncates(self):
        assert len(slugify("word " * 40, max_length=20)) <= 20

    def test_never_ends_with_a_separator(self):
        assert not slugify("hello!!!", max_length=7).endswith("-")

    def test_empty_input_gets_a_fallback(self):
        assert slugify("!!!") == "clip"

    def test_unicode_is_kept_when_alphanumeric(self):
        assert slugify("café naïve") == "café-naïve"


class TestStageCache:
    def test_a_fresh_artifact_is_reusable(self, tmp_path):
        (tmp_path / "transcript.json").write_text("{}", encoding="utf-8")
        assert StageCache(tmp_path).is_fresh("transcribe", "transcript.json")

    def test_a_missing_artifact_is_not_fresh(self, tmp_path):
        assert not StageCache(tmp_path).is_fresh("transcribe", "transcript.json")

    def test_an_empty_artifact_is_not_fresh(self, tmp_path):
        """A zero-byte file is a crashed write, not a cache hit."""
        (tmp_path / "transcript.json").write_text("", encoding="utf-8")
        assert not StageCache(tmp_path).is_fresh("transcribe", "transcript.json")

    def test_forcing_a_stage_invalidates_it(self, tmp_path):
        (tmp_path / "transcript.json").write_text("{}", encoding="utf-8")
        cache = StageCache(tmp_path, forced=["transcribe"])
        assert not cache.is_fresh("transcribe", "transcript.json")

    def test_forcing_a_stage_invalidates_everything_downstream(self, tmp_path):
        """A stale downstream artifact is worse than no artifact."""
        cache = StageCache(tmp_path, forced=["transcribe"])
        assert "segment" in cache.forced
        assert "candidates" in cache.forced
        assert "render" in cache.forced

    def test_forcing_does_not_invalidate_upstream(self, tmp_path):
        cache = StageCache(tmp_path, forced=["render"])
        assert "ingest" not in cache.forced
        assert "transcribe" not in cache.forced

    def test_a_stage_made_under_other_settings_is_redone(self, tmp_path):
        """D139: a campaign switched to scripted reused the old scores and made the same clips."""
        from clipper.config import Config
        from clipper.pipeline import stage_keys

        (tmp_path / "signals.json").write_text("{}")
        cache = StageCache(tmp_path)
        assert not cache.is_fresh("signals", "signals.json", "k1")   # made before keys: redo once
        cache.keep("signals.json", "k1")
        assert cache.is_fresh("signals", "signals.json", "k1")
        assert not cache.is_fresh("signals", "signals.json", "k2")
        plain = Config()
        scripted = plain.model_copy(update={"llm": plain.llm.model_copy(update={"drop_needs_prior_context": False}),
                                            "candidates": plain.candidates.model_copy(update={"scene_aware": True})})
        a, b = stage_keys(plain, None), stage_keys(scripted, None)
        assert a["candidates"] != b["candidates"] and a["signals"] != b["signals"]
        assert stage_keys(plain, None) == a

    def test_force_all(self, tmp_path):
        assert StageCache(tmp_path, forced=["all"]).forced == set(StageCache.ORDER)

    def test_unknown_stage_names_the_valid_ones(self, tmp_path):
        with pytest.raises(ValueError, match="transcribe"):
            StageCache(tmp_path, forced=["transcirbe"])

    def test_blank_entries_are_ignored(self, tmp_path):
        assert StageCache(tmp_path, forced=["", "  "]).forced == set()


class TestHeatmapParsing:
    """yt-dlp's heatmap is scraped, not an API, so parsing is defensive."""

    def test_parses_a_well_formed_heatmap(self):
        raw = [
            {"start_time": 0.0, "end_time": 10.0, "value": 0.5},
            {"start_time": 10.0, "end_time": 20.0, "value": 0.9},
        ]
        result = normalize_heatmap(raw)
        assert result is not None
        assert len(result) == 2
        assert result[1]["value"] == 0.9

    def test_sorts_by_start_time(self):
        raw = [
            {"start_time": 10.0, "end_time": 20.0, "value": 0.9},
            {"start_time": 0.0, "end_time": 10.0, "value": 0.5},
        ]
        result = normalize_heatmap(raw)
        assert [s["start_time"] for s in result] == [0.0, 10.0]

    def test_coerces_numeric_strings(self):
        raw = [{"start_time": "0", "end_time": "10", "value": "0.5"}]
        assert normalize_heatmap(raw)[0]["value"] == 0.5

    def test_skips_malformed_entries_rather_than_crashing(self):
        raw = [
            {"start_time": 0.0, "end_time": 10.0, "value": 0.5},
            {"nonsense": True},
            "not a dict",
            {"start_time": 20.0, "end_time": 30.0, "value": 0.7},
        ]
        assert len(normalize_heatmap(raw)) == 2

    def test_skips_zero_and_negative_length_segments(self):
        raw = [{"start_time": 10.0, "end_time": 10.0, "value": 0.5},
               {"start_time": 30.0, "end_time": 20.0, "value": 0.5}]
        assert normalize_heatmap(raw) is None

    @pytest.mark.parametrize("raw", [None, [], {}, "heatmap", 0])
    def test_absent_or_wrong_shape_yields_none(self, raw):
        """A shape change upstream must degrade the signal, not break ingest."""
        assert normalize_heatmap(raw) is None

    def test_all_entries_malformed_yields_none(self):
        assert normalize_heatmap([{"a": 1}, {"b": 2}]) is None
