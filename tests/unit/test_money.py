"""View-milestone tasks, payouts, and budget and deadline warnings (D98-D100)."""

from __future__ import annotations

from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from clipper.campaign import milestones
from clipper.studio import db
from clipper.studio.server import campaign_warning

from .test_studio_app import add_clip


class TestMilestones:
    def test_a_brief_sentence_with_an_action_at_a_view_count_is_a_milestone(self):
        found = milestones.find("Wait until your video passes 2,000 views, then screen-record your "
                                "video analytics and submit.\nOnce you hit 10k views, DM us a screenshot.")
        assert [(m.views, m.task[:20]) for m in found] == [(2000, "Wait until your vide"), (10000, "Once you hit 10k vie")]

    @pytest.mark.parametrize("text", [
        "Pays $1 per 1,000 views.",
        "Each post needs 5,000 views to\nqualify; max $1,000 per post.",
        "Minimum 10k views to be eligible.",
        "This show has 2 million views on YouTube.",
    ])
    def test_rates_and_minimums_are_not_tasks(self, text):
        assert milestones.find(text) == []

    def test_a_submit_milestone_is_the_campaigns_submit_threshold(self):
        from types import SimpleNamespace as NS

        brief = "Pin a comment at 500 views.\nWait until your video passes 2,000 views, then submit."
        assert milestones.submit_at(NS(notes="", posting_rules=[]), brief) == 2000
        assert milestones.submit_at(NS(notes="Pin a comment at 500 views.", posting_rules=[])) is None


class TestWarnings:
    TODAY = datetime(2026, 10, 2, 12, 0)

    def test_deadlines(self):
        def warn(deadline):
            return campaign_warning(deadline, None, None, 1.0, self.TODAY)

        assert warn("2026-10-01") == "Ended 2026-10-01"
        assert warn("2026-10-02") == "Ends today"
        assert warn("2026-10-04") == "Ends in 2 days"
        assert warn("2026-10-20") == "" and warn("") == "" and warn("someday") == ""

    def test_budget(self):
        def warn(left, rate=1.0, checked="2026-10-02 09:00"):
            return campaign_warning("", left, checked, rate, self.TODAY)

        assert warn(0) == "Budget used up"
        assert warn(30) == "About $30 of budget left"
        assert warn(30, checked="2026-09-29 09:00") == "About $30 of budget left (3 days ago)"
        assert warn(80) == "" and warn(80, rate=5.0) == "About $80 of budget left"  # 20k views at $5


class TestApi:
    @pytest.fixture
    def client(self, data_root, tmp_path, monkeypatch, valid_campaign_dict):
        folder = tmp_path / "campaigns"
        folder.mkdir()
        brief = {**valid_campaign_dict, "name": "chad-powers-s2", "reward_per_1k_usd": 2.0,
                 "platform_targets": ["tiktok"], "deadline": "2026-10-03",
                 "notes": "Once your post passes 1,000 views, screen-record its analytics and send it."}
        (folder / "chad-powers-s2.yaml").write_text(yaml.safe_dump(brief), encoding="utf-8")
        from clipper.studio import server

        monkeypatch.setattr(server, "campaigns_dir", lambda: folder)
        return TestClient(server.create_app())

    def post(self, views):
        from clipper.learn import log as perf

        perf.write([{"caption": "I started Chad Powers for football #ad", "campaign": "chad-powers-s2",
                     "source_id": "a394", "clip_id": "001_16m12s", "platform": "tiktok",
                     "url": "https://www.tiktok.com/@s/video/9", "views_latest": str(views),
                     "posted_at": "2026-09-20 12:00"}])

    def test_a_post_past_the_milestone_gets_its_task_until_done(self, client, data_root):
        clip_id = add_clip(data_root)
        self.post(800)
        assert client.get(f"/api/clips/{clip_id}").json()["posts"][0]["tasks"] == []
        self.post(1500)
        (task,) = client.get(f"/api/clips/{clip_id}").json()["posts"][0]["tasks"]
        assert task["views"] == 1000 and not task["done"] and "screen-record" in task["task"]
        assert client.get("/api/home").json()["metrics"]["tasks_due"] == 1
        client.post("/api/posts/task", json={"url": "https://www.tiktok.com/@s/video/9?x=1", "views": 1000})
        assert client.get(f"/api/clips/{clip_id}").json()["posts"][0]["tasks"][0]["done"]
        assert client.get("/api/home").json()["metrics"]["tasks_due"] == 0

    def test_payouts_are_recorded_and_give_the_real_rate(self, client, data_root):
        add_clip(data_root)
        self.post(4000)
        res = client.post("/api/payouts", json={"campaign": "chad-powers-s2", "amount": 6, "paid_on": "2026-10-01"})
        assert res.status_code == 200
        (campaign,) = client.get("/api/campaigns").json()
        assert campaign["paid_usd"] == 6.0 and campaign["paid_per_1k"] == 1.5
        assert client.get("/api/home").json()["metrics"]["paid_usd"] == 6.0
        assert client.post("/api/payouts", json={"campaign": "chad-powers-s2", "amount": -1}).status_code == 400
        client.delete(f"/api/payouts/{res.json()['id']}")
        assert client.get("/api/campaigns").json()[0]["paid_usd"] is None

    def test_budget_left_is_saved_with_when_and_warned_about(self, client, data_root):
        assert client.patch("/api/campaigns/chad-powers-s2", json={"budget_left": 20}).status_code == 200
        (campaign,) = client.get("/api/campaigns").json()
        assert campaign["budget_left"] == 20 and campaign["budget_checked_at"]
        assert "budget left" in campaign["warning"]
        assert client.patch("/api/campaigns/chad-powers-s2", json={"budget_left": "lots"}).status_code == 400
        with db.connect() as con:
            assert db.campaign_state(con)["chad-powers-s2"]["budget_left"] == 20
