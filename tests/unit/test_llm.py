"""LLM backends, response parsing, caching and the rubric signal.

No network: everything runs against `MockBackend` or scripted responses.
"""

from __future__ import annotations

import json

import pytest

from clipper.config import LLMConfig, RubricWeights
from clipper.llm.base import (
    LLMBackend,
    LLMConfigError,
    LLMError,
    LLMRequest,
    MockBackend,
    RateLimiter,
    create,
)
from clipper.llm.cache import LLMCache
from clipper.llm.prompts import PROMPT_A, PROMPT_B, PROMPT_VERSION, build_user_message
from clipper.models import Candidate, RubricScores
from clipper.signals.llm import (
    RubricItem,
    _hard_drop_reason,
    _parse_items,
    combine_totals,
    score_candidates,
)


def item(index: int = 0, **overrides) -> dict:
    base = dict(
        index=index, hook_strength=7, standalone_clarity=7, payoff=7,
        emotional_intensity=6, quotability=6, ending_completeness=7,
        needs_prior_context=False, is_sponsor_or_ad=False, policy_risk="none",
        hook_text="A punchy hook", suggested_caption="A caption",
        hashtags=["#a", "#b", "#c"],
    )
    base.update(overrides)
    return base


def rubric(**overrides) -> RubricScores:
    base = dict(hook_strength=6, standalone_clarity=6, payoff=6,
                emotional_intensity=6, quotability=6, ending_completeness=6)
    base.update(overrides)
    return RubricScores(**base)


def candidates(n: int = 3) -> list[Candidate]:
    return [
        Candidate(candidate_id=f"c{i:03d}", start=i * 100.0, end=i * 100.0 + 30.0,
                  sentence_indices=(i, i + 1),
                  text=f"Candidate {i}: why nobody talks about the real mistake here.")
        for i in range(n)
    ]


class TestParsing:
    def test_plain_array(self):
        parsed = _parse_items(json.dumps([item(0), item(1)]))
        assert len(parsed) == 2
        assert parsed[1].index == 1

    def test_markdown_fenced(self):
        text = "```json\n" + json.dumps([item(0)]) + "\n```"
        assert len(_parse_items(text)) == 1

    def test_prose_around_the_array(self):
        text = "Here are the scores:\n" + json.dumps([item(0)]) + "\nHope that helps!"
        assert len(_parse_items(text)) == 1

    def test_wrapped_in_an_object(self):
        assert len(_parse_items(json.dumps({"clips": [item(0), item(1)]}))) == 2

    def test_a_bare_object_is_treated_as_one_item(self):
        """Regression: a rubric item has a list-valued `hashtags` key.

        The wrapper-unwrapping heuristic used to grab the first list it found,
        turning a single valid item into a list of hashtag strings that then
        parsed as nothing -- so every cache read missed while reporting a hit.
        """
        parsed = _parse_items(json.dumps(item(0)))
        assert parsed is not None
        assert len(parsed) == 1
        assert parsed[0].hook_strength == 7

    def test_a_single_cached_item_round_trips(self):
        """What `_score_with_variant` writes must be what it can read back."""
        scores = rubric(hook_strength=9)
        stored = json.dumps([{"index": 0, **scores.model_dump()}])
        parsed = _parse_items(stored)
        assert parsed and parsed[0].to_scores().hook_strength == 9

    def test_invalid_items_are_dropped_not_fatal(self):
        text = json.dumps([item(0), {"index": 1, "hook_strength": 99}, item(2)])
        parsed = _parse_items(text)
        assert len(parsed) == 2

    def test_missing_index_is_filled_by_position(self):
        raw = item(0)
        del raw["index"]
        assert _parse_items(json.dumps([raw]))[0].index == 0

    @pytest.mark.parametrize("text", ["", "not json", "{]", "null", "[]", "[1, 2, 3]"])
    def test_unparseable_returns_none(self, text):
        assert _parse_items(text) is None

    def test_out_of_range_scores_are_rejected(self):
        assert _parse_items(json.dumps([item(0, hook_strength=11)])) is None

    def test_unknown_policy_risk_falls_back_to_none(self):
        scores = _parse_items(json.dumps([item(0, policy_risk="catastrophic")]))[0].to_scores()
        assert scores.policy_risk == "none"


class TestCombineTotals:
    WEIGHTS = RubricWeights().as_dict()

    def test_agreement_gives_the_mean(self):
        total = combine_totals(rubric(), rubric(), self.WEIGHTS, 0.25)
        assert total == pytest.approx(6.0)

    def test_disagreement_is_penalised(self):
        agree = combine_totals(rubric(hook_strength=6), rubric(hook_strength=6),
                               self.WEIGHTS, 0.25)
        differ = combine_totals(rubric(hook_strength=10), rubric(hook_strength=2),
                                self.WEIGHTS, 0.25)
        assert differ < agree

    def test_a_zero_penalty_gives_the_plain_mean(self):
        total = combine_totals(rubric(hook_strength=10), rubric(hook_strength=0),
                               self.WEIGHTS, 0.0)
        expected = (rubric(hook_strength=10).total(self.WEIGHTS)
                    + rubric(hook_strength=0).total(self.WEIGHTS)) / 2
        assert total == pytest.approx(expected, abs=1e-4)

    def test_one_opinion_is_returned_unchanged(self):
        assert combine_totals(rubric(), None, self.WEIGHTS, 0.25) == pytest.approx(6.0)

    def test_no_opinions_is_zero(self):
        assert combine_totals(None, None, self.WEIGHTS, 0.25) == 0.0

    def test_never_negative(self):
        total = combine_totals(rubric(**dict.fromkeys(
            ["hook_strength", "standalone_clarity", "payoff",
             "emotional_intensity", "quotability", "ending_completeness"], 10)),
            rubric(**dict.fromkeys(
                ["hook_strength", "standalone_clarity", "payoff",
                 "emotional_intensity", "quotability", "ending_completeness"], 0)),
            self.WEIGHTS, 1.0)
        assert total >= 0.0

    def test_stays_on_the_zero_to_ten_scale(self):
        """Selection gates on this absolutely, so the scale must be stable."""
        high = dict.fromkeys(
            ["hook_strength", "standalone_clarity", "payoff",
             "emotional_intensity", "quotability", "ending_completeness"], 10)
        assert combine_totals(rubric(**high), rubric(**high), self.WEIGHTS, 0.25) \
            == pytest.approx(10.0)


class TestHardDrops:
    def test_a_sponsor_flag_from_either_prompt_drops(self):
        assert "sponsor" in _hard_drop_reason(
            rubric(is_sponsor_or_ad=True), rubric(), single_opinion=False)

    def test_high_policy_risk_drops(self):
        assert "policy" in _hard_drop_reason(
            rubric(policy_risk="high"), rubric(), single_opinion=False)

    def test_needs_context_requires_both_prompts_to_agree(self):
        one_says = _hard_drop_reason(rubric(needs_prior_context=True), rubric(),
                                     single_opinion=False)
        both_say = _hard_drop_reason(rubric(needs_prior_context=True),
                                     rubric(needs_prior_context=True),
                                     single_opinion=False)
        assert one_says == ""
        assert "both prompts agree" in both_say

    def test_with_one_opinion_that_single_verdict_is_used(self):
        """The brief leaves this undefined; resolved in signals/llm.py."""
        assert "single opinion" in _hard_drop_reason(
            rubric(needs_prior_context=True), None, single_opinion=True)

    def test_healthy_scores_are_not_dropped(self):
        assert _hard_drop_reason(rubric(), rubric(), single_opinion=False) == ""

    def test_no_opinions_is_not_a_drop(self):
        assert _hard_drop_reason(None, None, single_opinion=False) == ""


class TestRateLimiter:
    def test_a_high_limit_does_not_block(self):
        limiter = RateLimiter(100_000)
        assert limiter.acquire() == 0.0
        assert limiter.acquire() < 0.01

    def test_the_interval_matches_the_configured_rate(self):
        assert RateLimiter(60).min_interval == pytest.approx(1.0)
        assert RateLimiter(10).min_interval == pytest.approx(6.0)


class TestBackendRetries:
    def test_a_transient_rate_limit_is_retried(self):
        backend = MockBackend(fail_times=2, max_retries=5)
        backend._backoff = lambda attempt: 0.0  # type: ignore[method-assign]
        response = backend.complete(LLMRequest(system="s", user="[0] (30s)\nhello"))
        assert response.text

    def test_giving_up_raises_with_context(self):
        backend = MockBackend(fail_times=99, max_retries=2)
        backend._backoff = lambda attempt: 0.0  # type: ignore[method-assign]
        with pytest.raises(LLMError, match=r"failed after 3 attempts"):
            backend.complete(LLMRequest(system="s", user="x"))
        assert backend.usage.failed == 1

    def test_a_config_error_is_not_retried(self):
        class BadBackend(LLMBackend):
            name = "bad"
            attempts = 0

            def _complete(self, request):
                type(self).attempts += 1
                raise LLMConfigError("no key")

        backend = BadBackend(requests_per_minute=100_000)
        with pytest.raises(LLMConfigError):
            backend.complete(LLMRequest(system="s", user="u"))
        assert BadBackend.attempts == 1

    def test_backoff_grows_and_is_capped(self):
        delays = [MockBackend._backoff(i) for i in range(12)]
        assert delays[0] < delays[4]
        assert all(d <= 60.0 for d in delays)

    def test_usage_is_accumulated(self):
        backend = MockBackend()
        for _ in range(3):
            backend.complete(LLMRequest(system="s", user="[0] (30s)\nhello"))
        assert backend.usage.calls == 3
        assert backend.usage.prompt_tokens > 0
        assert "3 LLM call" in backend.usage.summary()


class TestBackendRegistry:
    def test_mock_is_always_available(self):
        assert create("mock").name == "mock"

    def test_an_unknown_backend_lists_the_alternatives(self):
        with pytest.raises(LLMConfigError, match="mock"):
            create("telepathy")

    def test_gemini_without_a_key_explains_how_to_get_one(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(LLMConfigError, match=r"aistudio\.google\.com"):
            create("gemini")

    def test_the_mock_is_never_throttled(self):
        """A configured RPM must not make the test suite sleep."""
        backend = create("mock", requests_per_minute=1)
        assert backend.limiter.min_interval < 0.001


class TestPrompts:
    def test_the_two_variants_differ(self):
        assert PROMPT_A.system != PROMPT_B.system

    def test_the_cache_key_carries_the_version(self):
        assert PROMPT_VERSION in PROMPT_A.cache_key
        assert PROMPT_A.cache_key != PROMPT_B.cache_key

    def test_the_user_message_numbers_each_candidate(self):
        message = build_user_message([(0, 30.0, "first"), (1, 45.0, "second")])
        assert "[0]" in message and "[1]" in message
        assert "(30s)" in message and "(45s)" in message

    def test_few_shot_examples_are_labelled_as_calibration(self):
        message = build_user_message(
            [(0, 30.0, "text")],
            examples=[{"hook": "A hook", "text": "body", "views": 120_000}],
        )
        assert "calibration" in message
        assert "120,000 views" in message

    def test_the_hook_rule_forbids_quoting(self):
        """The model echoed the transcript verbatim without this."""
        assert "NOT a copy of the transcript" in PROMPT_A.system


class TestCache:
    def test_round_trips(self, tmp_path):
        cache = LLMCache(tmp_path)
        key = cache.key(backend="mock", model="m", prompt_key="v1:a", payload="text")
        cache.put(key, text="stored", model="m")
        assert cache.get(key).text == "stored"

    def test_a_miss_returns_none(self, tmp_path):
        cache = LLMCache(tmp_path)
        assert cache.get("nope") is None
        assert cache.misses == 1

    def test_keys_depend_on_every_component(self, tmp_path):
        cache = LLMCache(tmp_path)
        base = dict(backend="mock", model="m", prompt_key="v1:a", payload="text")
        key = cache.key(**base)
        for field, value in [("backend", "gemini"), ("model", "other"),
                             ("prompt_key", "v2:a"), ("payload", "different")]:
            assert cache.key(**{**base, field: value}) != key

    def test_a_prompt_version_bump_invalidates(self, tmp_path):
        """Otherwise a changed prompt silently reuses the old prompt's scores."""
        cache = LLMCache(tmp_path)
        old = cache.key(backend="m", model="x", prompt_key="v1:a", payload="t")
        cache.put(old, text="old scores", model="x")
        new = cache.key(backend="m", model="x", prompt_key="v2:a", payload="t")
        assert cache.get(new) is None

    def test_a_corrupt_entry_is_discarded(self, tmp_path):
        cache = LLMCache(tmp_path)
        key = cache.key(backend="m", model="x", prompt_key="p", payload="t")
        cache.put(key, text="ok", model="x")
        cache._path(key).write_text("{ broken", encoding="utf-8")
        assert cache.get(key) is None

    def test_disabling_bypasses_it(self, tmp_path):
        cache = LLMCache(tmp_path, enabled=False)
        key = cache.key(backend="m", model="x", prompt_key="p", payload="t")
        cache.put(key, text="ignored", model="x")
        assert cache.get(key) is None

    def test_clear_removes_entries(self, tmp_path):
        cache = LLMCache(tmp_path)
        for i in range(3):
            cache.put(cache.key(backend="m", model="x", prompt_key="p", payload=str(i)),
                      text="t", model="x")
        assert cache.clear() == 3


class TestScoreCandidates:
    CFG = LLMConfig(batch_size=2)

    def test_scores_every_candidate(self, tmp_path):
        items = candidates(5)
        result = score_candidates(items, MockBackend(), self.CFG, cache=LLMCache(tmp_path))
        assert result.scored_count == 5
        assert all(0.0 <= t <= 10.0 for t in result.totals.values())

    def test_is_deterministic(self, tmp_path):
        items = candidates(4)
        first = score_candidates(items, MockBackend(), self.CFG,
                                 cache=LLMCache(tmp_path / "a"))
        second = score_candidates(items, MockBackend(), self.CFG,
                                  cache=LLMCache(tmp_path / "b"))
        assert first.totals == second.totals

    def test_batching_bounds_the_call_count(self, tmp_path):
        backend = MockBackend()
        cfg = LLMConfig(batch_size=4, use_second_opinion=True)
        score_candidates(candidates(8), backend, cfg, cache=LLMCache(tmp_path))
        # 8 candidates / 4 per batch = 2 calls, times 2 prompts.
        assert backend.usage.calls == 4

    def test_the_second_opinion_can_be_disabled_to_halve_calls(self, tmp_path):
        both = MockBackend()
        score_candidates(candidates(8), both, LLMConfig(batch_size=4),
                         cache=LLMCache(tmp_path / "both"))
        single = MockBackend()
        score_candidates(candidates(8), single,
                         LLMConfig(batch_size=4, use_second_opinion=False),
                         cache=LLMCache(tmp_path / "single"))
        assert single.usage.calls == both.usage.calls / 2

    def test_a_warm_cache_makes_no_calls_at_all(self, tmp_path):
        """Regression: the cache reported hits while still calling the LLM."""
        cache = LLMCache(tmp_path)
        items = candidates(6)
        first = MockBackend()
        score_candidates(items, first, self.CFG, cache=cache)
        assert first.usage.calls > 0

        second = MockBackend()
        result = score_candidates(items, second, self.CFG, cache=cache)
        assert second.usage.calls == 0, "a fully cached run must make no calls"
        assert result.scored_count == len(items)

    def test_a_sponsor_read_is_dropped(self, tmp_path):
        ad = Candidate(candidate_id="ad", start=0.0, end=30.0, sentence_indices=(0, 1),
                       text="This episode is sponsored by Acme, use the promo code SHOW.")
        result = score_candidates([ad], MockBackend(), self.CFG, cache=LLMCache(tmp_path))
        assert "ad" in result.drops
        assert "sponsor" in result.drops["ad"]

    def test_a_backend_failure_leaves_candidates_unscored_not_crashing(self, tmp_path):
        backend = MockBackend(fail_times=99, max_retries=1)
        backend._backoff = lambda attempt: 0.0  # type: ignore[method-assign]
        result = score_candidates(candidates(3), backend, self.CFG, cache=LLMCache(tmp_path))
        assert result.scored_count == 0
        assert len(result.unscored) == 3

    def test_no_candidates(self, tmp_path):
        result = score_candidates([], MockBackend(), self.CFG, cache=LLMCache(tmp_path))
        assert result.scored_count == 0

    def test_the_mock_ranks_a_strong_clip_above_a_rambling_one(self, tmp_path):
        strong = Candidate(candidate_id="strong", start=0, end=30, sentence_indices=(0, 1),
                           text="I lost eleven months to that exact mistake. "
                                "Here is what changed everything.")
        weak = Candidate(candidate_id="weak", start=100, end=130, sentence_indices=(1, 2),
                         text="So um anyway as I said uh we were talking about the thing.")
        result = score_candidates([strong, weak], MockBackend(), self.CFG,
                                  cache=LLMCache(tmp_path))
        assert result.totals.get("strong", 0) > result.totals.get("weak", 0)


class TestRubricItemConversion:
    def test_converts_to_scores(self):
        scores = RubricItem.model_validate(item(0)).to_scores()
        assert isinstance(scores, RubricScores)
        assert scores.hook_strength == 7

    def test_whitespace_is_stripped(self):
        scores = RubricItem.model_validate(
            item(0, hook_text="  spaced  ", hashtags=[" #a ", "", "  "])
        ).to_scores()
        assert scores.hook_text == "spaced"
        assert scores.hashtags == ["#a"]

    def test_the_weighted_total_respects_the_weights(self):
        weights = {"hook_strength": 1.0, "standalone_clarity": 0.0, "payoff": 0.0,
                   "emotional_intensity": 0.0, "quotability": 0.0,
                   "ending_completeness": 0.0}
        assert rubric(hook_strength=9).total(weights) == pytest.approx(9.0)
