"""Your own channel's Shorts reach Stats: Create's clips get log rows, existing uploads are adopted (D144)."""

from __future__ import annotations

from types import SimpleNamespace

from clipper.studio import db, stats


def test_a_built_short_gets_a_row_so_its_post_can_be_found(data_root):
    with db.connect() as con:
        db.upsert_clip(con, {"campaign": "german-professor", "source_id": "create", "clip_id": "create-12",
                             "source_title": "Voice", "title": "Voice", "file": "x.mp4", "hook": "Voice",
                             "caption": "You hear yourself through air.", "duration_s": 40.0, "start_s": 0, "end_s": 40})
    rows: list[dict] = []
    stats.ensure_create_rows(rows)
    assert [(r["campaign"], r["clip_id"], r["caption"]) for r in rows] == [
        ("german-professor", "create-12", "You hear yourself through air.")]
    stats.ensure_create_rows(rows)
    assert len(rows) == 1                                  # not added twice
    rows[0]["caption"] = "old"
    stats.ensure_create_rows(rows)
    assert rows[0]["caption"] == "You hear yourself through air."   # not posted yet: follows the script


def test_shorts_already_on_your_channel_are_adopted_once(data_root):
    short = SimpleNamespace(id="abc", title="Centripetal Force", caption="Why do you feel thrown outward?",
                            url="https://www.youtube.com/shorts/abc")
    rows: list[dict] = []
    stats.adopt_uploads(rows, [short], "german-professor", "german.professor")
    stats.adopt_uploads(rows, [short], "german-professor", "german.professor")
    assert len(rows) == 1 and rows[0]["video_id"] == "abc" and rows[0]["platform"] == "youtube"
    with db.connect() as con:
        got = con.execute("SELECT title, file FROM clips WHERE clip_id='yt-abc'").fetchall()
    assert [(g["title"], g["file"]) for g in got] == [("Centripetal Force", "")]


def test_only_your_own_channels_handle_adopts(data_root):
    from clipper.create import channel

    channel.save(channel.make("physics", "German Professor", "@German.Professor", campaign="german-professor"))
    assert stats.own_campaign("German.Professor") == "german-professor"
    assert stats.own_campaign("solomonkey_clips") == ""
