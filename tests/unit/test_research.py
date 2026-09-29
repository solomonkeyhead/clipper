"""The Ask chat, finding campaigns, proof packs and fast post-link capture."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
import yaml

from clipper.research import agent, sources
from clipper.studio import db


@pytest.fixture
def client(data_root, tmp_path, monkeypatch, valid_campaign_dict):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    (campaigns / "test-campaign.yaml").write_text(yaml.safe_dump(
        {**valid_campaign_dict, "required_caption_text": "#ad", "campaign_url": "https://whop.com/c/x"}),
        encoding="utf-8")
    monkeypatch.setattr(server, "campaigns_dir", lambda: campaigns)
    return TestClient(server.create_app())


def add_clip(data_root, **extra) -> int:
    path = data_root / "library" / "test-campaign" / "x.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 1024)
    with db.connect() as con:
        return db.upsert_clip(con, {"campaign": "test-campaign", "source_id": "s", "clip_id": "001",
                                    "title": "a clip", "file": "test-campaign/x.mp4",
                                    "caption": "so good #test #ad", **extra})


# ---------- Ask ----------

def fake_ask(question, history, box, progress=lambda s: None):
    progress("Searching the web: x")
    return agent.Answer(text=f"answer to {question} after {len(history)} turns",
                        sources=[{"title": "A", "url": "https://a.example"}])


def test_a_conversation_keeps_its_history(client, monkeypatch):
    monkeypatch.setattr(agent, "ask", fake_ask)
    first = client.post("/api/research/ask", json={"text": "what is trending?"}).json()
    assert first["title"] == "what is trending?" and len(first["messages"]) == 2
    again = client.post("/api/research/ask", json={"text": "and hooks?", "thread_id": first["id"]}).json()
    assert again["messages"][-1]["content"] == "answer to and hooks? after 2 turns"
    assert again["messages"][-1]["sources"] == [{"title": "A", "url": "https://a.example"}]
    client.delete(f"/api/research/threads/{first['id']}")
    assert client.get("/api/research/threads").json() == []


def test_the_free_plan_has_no_ask(client):
    with db.connect() as con:
        db.set_setting(con, "plan", "free")
    assert client.get("/api/research/threads").status_code == 402
    assert client.get("/api/research/status").json()["can_research"] is False


def test_web_search_needs_a_key_and_numbers_sources_once(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(sources.SourceUnavailable, match="Tavily"):
        sources.search_web("x")
    monkeypatch.setattr(sources, "search_web", lambda q, days=None: [
        sources.WebResult("A", "https://a", "text a"), sources.WebResult("B", "https://b", "text b")])
    run = agent._Run(agent.Toolbox(lambda c: [], lambda: []), lambda s: None)
    run.call("search_web", {"query": "one"})
    second = run.call("search_web", {"query": "two"})
    assert [r["n"] for r in second["results"]] == [1, 2] and len(run.sources) == 2
    assert run.call("propose_hook_lines", {"campaign": "c"})["error"]  # no actions any more


# ---------- finding campaigns ----------

def test_the_watcher_keeps_what_it_finds(client):
    from clipper.studio import finder
    from clipper.watch.judge import Verdict

    finder.record_found(Verdict(is_new_campaign=True, source="vyro", name="New Show",
                                rate_per_1k_usd=2.0, platforms=["tiktok"], fit="yes"), "brief text")
    found = client.get("/api/found").json()
    assert [f["name"] for f in found] == ["New Show"] and "brief" not in found[0]
    client.post(f"/api/found/{found[0]['key']}/dismiss")
    assert client.get("/api/found").json() == []


def test_checking_a_campaign_explains_the_fit(client, monkeypatch):
    from clipper.campaign import editor
    from clipper.campaign.editor import CampaignForm

    monkeypatch.setattr("clipper.pipeline.build_backend", lambda config: object())
    monkeypatch.setattr(editor, "read_brief", lambda text, backend: CampaignForm(
        title="Show", reward_per_1k_usd=2.0, platform_targets=["youtube_shorts"],
        notes="- Account must be 30 days old"))
    result = client.post("/api/campaigns/check", json={"text": "x" * 60}).json()
    assert result["form"]["title"] == "Show"
    fit = result["fit"]
    assert fit["verdict"] == "poor"  # pays for YouTube Shorts, which isn't connected
    texts = [c["text"] for c in fit["checks"]]
    assert any("none of those connected" in t for t in texts)
    assert "Account must be 30 days old" in texts


# ---------- proof packs ----------

def test_a_late_snapshot_and_the_proof_pack(client, data_root):
    clip = add_clip(data_root)
    from clipper.studio import library, server

    library.backfill_evidence(server.load_campaigns())
    got = client.get(f"/api/clips/{clip}").json()
    assert got["proof"]["late"] is True and got["proof"]["saved_at"]
    res = client.get(f"/media/{clip}/proof")
    assert res.status_code == 200 and res.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(res.content))
    assert {"proof.html", "brief.yaml", "clip.mp4", "stats.csv", "evidence.json"} <= set(z.namelist())
    assert "saved later" in z.read("proof.html").decode()
    assert "required_caption_text: '#ad'" in z.read("brief.yaml").decode()


def test_the_posted_caption_is_checked_against_the_saved_rules():
    from clipper.studio.evidence import caption_checks, summary

    rules = {"required_hashtags": ["#test"], "required_caption_text": "#ad",
             "only_required_hashtags": True}
    checks = caption_checks("great clip #test #fyp", rules)
    assert [c["passed"] for c in checks] == [True, False, False]
    assert checks[-1]["detail"] == "#fyp"
    ev = {"saved_at": "2026-09-29 10:00:00", "checks": [{"name": "x", "passed": True}],
          "campaign": {"rules": rules}}
    assert summary(ev, [{"posted_caption": "ok #test #ad"}])["posted_ok"] is True


# ---------- fast link capture ----------

def test_marking_posted_starts_fast_syncs(client, data_root):
    clip = add_clip(data_root)
    client.patch(f"/api/clips/{clip}", json={"status": "posted"})
    assert client.get(f"/api/clips/{clip}").json()["watching"] is True
    client.patch(f"/api/clips/{clip}", json={"status": "ready"})
    assert client.get(f"/api/clips/{clip}").json()["watching"] is False


def test_a_pasted_link_becomes_a_post(client, data_root):
    from clipper.learn import log as perf

    clip = add_clip(data_root)
    bad = client.post(f"/api/clips/{clip}/posts", json={"url": "https://example.com/x"})
    assert bad.status_code == 400
    res = client.post(f"/api/clips/{clip}/posts",
                      json={"url": "https://www.tiktok.com/@me/video/7412345678901234567?lang=en"})
    assert res.json() == {"platform": "tiktok"}
    got = client.get(f"/api/clips/{clip}").json()
    assert got["status"] == "posted" and got["posts"][0]["url"].endswith("/video/7412345678901234567")
    row = next(r for r in perf.read() if r.get("url"))
    assert row["video_id"] == "7412345678901234567"
    again = client.post(f"/api/clips/{clip}/posts",
                        json={"url": "https://www.tiktok.com/@me/video/7412345678901234567"})
    assert again.status_code == 400
    ig = client.post(f"/api/clips/{clip}/posts", json={"url": "https://www.instagram.com/reel/AbC123/"})
    assert ig.json() == {"platform": "instagram"}
    assert len(client.get(f"/api/clips/{clip}").json()["posts"]) == 2


def test_instagram_sync_adopts_a_pasted_link():
    from clipper.instagram import sync
    from clipper.instagram.api import Reel

    rows = [{"caption": "c", "platform": "instagram", "url": "https://www.instagram.com/reel/AbC123",
             "video_id": ""}]
    sync.apply([Reel(id="999", caption="rewritten caption", created=0,
                     url="https://www.instagram.com/reel/AbC123/", views=50)], rows)
    assert len(rows) == 1 and rows[0]["video_id"] == "999" and rows[0]["views_latest"] == "50"
    assert rows[0]["posted_caption"] == "rewritten caption"
    json.dumps(rows)
