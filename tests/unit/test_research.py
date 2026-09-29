"""The Research section: chat threads, proposed actions, niches, saved items, plans."""

from __future__ import annotations

import pytest
import yaml

from clipper.research import agent, radar, sources
from clipper.studio import db


@pytest.fixture
def client(data_root, tmp_path, monkeypatch, valid_campaign_dict):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    (campaigns / "test-campaign.yaml").write_text(yaml.safe_dump(valid_campaign_dict), encoding="utf-8")
    monkeypatch.setattr(server, "campaigns_dir", lambda: campaigns)
    downloads = tmp_path / "dl"
    downloads.mkdir()
    (downloads / "ep1.mp4").write_bytes(b"x")
    monkeypatch.setattr(server, "source_folders", lambda: [downloads])
    return TestClient(server.create_app())


def fake_ask(question, history, box, *, niche=None, can_act=False, progress=lambda s: None):
    progress("Searching the web: x")
    actions = []
    if can_act:
        run = agent._Run(box, can_act, progress)
        run._propose_hook_lines("test-campaign", ["the chemistry is insane", "wait for it"])
        run._propose_clip_job("test-campaign", "ep1.mp4", 3)
        actions = run.actions
    return agent.Answer(text=f"answer to {question} after {len(history)} turns",
                        sources=[{"title": "A", "url": "https://a.example"}], actions=actions)


def test_a_conversation_keeps_its_history(client, monkeypatch):
    monkeypatch.setattr(agent, "ask", fake_ask)
    first = client.post("/api/research/ask", json={"text": "what is trending?"}).json()
    assert first["title"] == "what is trending?" and len(first["messages"]) == 2
    again = client.post("/api/research/ask", json={"text": "and hooks?", "thread_id": first["id"]}).json()
    assert again["messages"][-1]["content"] == "answer to and hooks? after 2 turns"
    assert [t["id"] for t in client.get("/api/research/threads").json()] == [first["id"]]
    client.delete(f"/api/research/threads/{first['id']}")
    assert client.get("/api/research/threads").json() == []


def test_proposed_actions_run_only_when_confirmed_and_once(client, monkeypatch, data_root):
    from clipper.studio import server

    monkeypatch.setattr(agent, "ask", fake_ask)
    submitted = []
    monkeypatch.setattr("clipper.studio.jobs.JobRunner.submit",
                        lambda self, c, s, top, ranges=None: submitted.append((c.name, top))
                        or type("J", (), {"id": 7})())
    thread = client.post("/api/research/ask", json={"text": "hooks please"}).json()
    message = thread["messages"][-1]
    assert [a["type"] for a in message["actions"]] == ["hook_lines", "clip_job"]
    assert server.load_campaigns()["test-campaign"].hook_texts == ()  # nothing yet

    done = client.post(f"/api/research/messages/{message['id']}/actions/0").json()
    assert "Added 2" in done["done"]
    assert server.load_campaigns()["test-campaign"].hook_texts == ("the chemistry is insane", "wait for it")
    assert client.post(f"/api/research/messages/{message['id']}/actions/0").status_code == 400
    client.post(f"/api/research/messages/{message['id']}/actions/1")
    assert submitted == [("test-campaign", 3)]


def test_plans_gate_research_and_actions(client, monkeypatch):
    monkeypatch.setattr(agent, "ask", fake_ask)
    with db.connect() as con:
        db.set_setting(con, "plan", "research")
    status = client.get("/api/research/status").json()
    assert status["can_research"] and not status["can_act"]
    message = client.post("/api/research/ask", json={"text": "x"}).json()["messages"][-1]
    assert message["actions"] == []  # the chat isn't offered the propose tools
    with db.connect() as con:
        db.set_setting(con, "plan", "free")
    assert client.get("/api/research/threads").status_code == 402
    with pytest.raises(ValueError):
        with db.connect() as con:
            db.set_setting(con, "plan", "platinum")


def test_niches_and_their_briefs(client, monkeypatch):
    monkeypatch.setattr(radar, "refresh", lambda niche, backend, progress=None: {
        "summary": f"{niche['name']} is hot", "topics": [], "hooks": ["h1"], "shorts": [],
        "sources": [], "notes": [], "live": False})
    monkeypatch.setattr("clipper.pipeline.build_backend", lambda config: object())
    niche = client.post("/api/research/niches", json={"name": "TV romance edits",
                                                      "keywords": ["chad powers", " "]}).json()
    assert niche["keywords"] == ["chad powers"] and niche["stale"]
    fresh = client.post(f"/api/research/niches/{niche['id']}/refresh").json()
    assert fresh["brief"]["summary"] == "TV romance edits is hot" and not fresh["stale"]
    assert client.post("/api/research/niches", json={"name": " "}).status_code == 400


def test_saved_items(client):
    item = client.post("/api/research/saved", json={"kind": "hook", "text": "wait for it"}).json()
    assert client.get("/api/research/saved").json()[0]["text"] == "wait for it"
    client.delete(f"/api/research/saved/{item['id']}")
    assert client.get("/api/research/saved").json() == []


def test_sources_need_keys(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(sources.SourceUnavailable, match="Tavily"):
        sources.search_web("x")
    with pytest.raises(sources.SourceUnavailable, match="YouTube"):
        sources.top_shorts("x")
    assert sources._iso_seconds("PT1M5S") == 65


def test_the_web_tool_numbers_sources_once(monkeypatch):
    monkeypatch.setattr(sources, "search_web", lambda q, days=None: [
        sources.WebResult("A", "https://a", "text a"), sources.WebResult("B", "https://b", "text b")])
    run = agent._Run(agent.Toolbox(lambda c: [], lambda: [], lambda: [], lambda: []), False, lambda s: None)
    run.call("search_web", {"query": "one"})
    second = run.call("search_web", {"query": "two"})
    assert [r["n"] for r in second["results"]] == [1, 2] and len(run.sources) == 2
    assert run.call("propose_hook_lines", {"campaign": "c", "lines": ["x"]})["error"]
