"""Found campaigns filed by niche (studio/finder.sort_niches, D106)."""

from __future__ import annotations

import json

from clipper.studio import db, finder


def add(key: str, name: str, niche: str = "") -> None:
    with db.connect() as con:
        con.execute("INSERT INTO found_campaigns (key, source, name, owner, rate, platforms, budget, deadline, "
                    "link, fit, why, brief, found_at, niche) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, "whop", name, "", "", "[]", "", "", "", "no", "", name, db.now(), niche))


def test_unsorted_campaigns_get_one_niche_each(data_root, monkeypatch):
    add("a", "Jack Neel Podcast")
    add("b", "FlyQuest PTL Clipping")
    add("c", "Already sorted", niche="Music")
    asked = []

    def fake_ask(backends, system, user, schema, cache, prompt_key):
        asked.append(user)
        return json.dumps([{"key": "a", "niche": "Podcasts"}, {"key": "b", "niche": "Esports!"}]), None

    import clipper.runner as runner
    import clipper.transcribe.correct as correct

    monkeypatch.setattr(correct, "_ask", fake_ask)
    monkeypatch.setattr(runner, "_correction_backends", lambda config, override: [])
    published = []
    assert finder.sort_niches(lambda *e: published.append(e)) == 2
    by_key = {f["key"]: f["niche"] for f in finder.found()}
    assert by_key == {"a": "Podcasts", "b": "Other", "c": "Music"}  # an unknown answer is Other
    assert "Already sorted" not in asked[0] and published == [("found.changed", {})]
    assert finder.sort_niches() == 0  # nothing left to sort


def test_a_judged_campaigns_niche_is_one_of_the_list():
    from clipper.watch.judge import NICHES, Verdict

    assert Verdict().niche == "Other" and "Podcasts" in NICHES
