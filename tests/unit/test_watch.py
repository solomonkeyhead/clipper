"""The campaign judge: its verdicts and the links it keeps.

Nothing here touches the network: the judge answers through the mock backend.
"""

from __future__ import annotations

import json

import pytest

from clipper.llm.base import MockBackend
from clipper.watch.judge import Verdict, campaign_key, judge_text, safe_link


class TestSafeLink:
    @pytest.mark.parametrize("url", [
        "https://vyro.com/campaigns/x", "https://app.vyro.com/c/1",
        "https://whop.com/discover/x", "https://contentrewards.com/discover"])
    def test_campaign_sites_are_kept(self, url):
        assert safe_link(url) == url

    @pytest.mark.parametrize("url", [
        "http://vyro.com/campaigns/x", "https://vyro.com.evil.example/x",
        "https://notvyro.com/x", "javascript:alert(1)", ""])
    def test_anything_else_is_dropped(self, url):
        assert safe_link(url) == ""


def verdict(**kw) -> Verdict:
    base = dict(is_new_campaign=True, source="vyro", name="FX: Adults | S2", owner="FX",
                rate="$2,000 / 1M views", rate_per_1k_usd=2.0, platforms=["tiktok"],
                fit="yes", why="TV comedy, your niche", rights="licensed")
    return Verdict(**{**base, **kw})


class TestJudge:
    def test_link_outside_campaign_sites_is_removed(self, data_root):
        answer = verdict(link="https://phish.example/login").model_dump()
        v = judge_text("Whop post", "t", "profile", MockBackend(responses=[json.dumps(answer)]))
        assert v is not None and v.link == ""

    def test_unusable_answer_is_none(self, data_root):
        assert judge_text("Whop post", "t", "profile", MockBackend(responses=["not json"])) is None


def test_one_campaign_has_one_key_however_it_is_written():
    assert campaign_key(verdict(name="FX: Adults | S2 ", owner="fx")) == campaign_key(verdict())
