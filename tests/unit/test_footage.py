"""Sorting footage by campaign (studio/footage.py): what's recorded, names, neighbours."""

from __future__ import annotations

import json
from types import SimpleNamespace

from clipper.studio import db, footage

CAMPAIGNS = {"fx-adults-s2": SimpleNamespace(title="FX Adults S2"),
             "chad-powers-s2": SimpleNamespace(title="Chad Powers S2"),
             "please-like-me": SimpleNamespace(title="Please Like Me"),
             "cc-85south": SimpleNamespace(title="85 South Show (growth)")}


def src(path: str) -> dict:
    return {"name": path.rsplit("/", 1)[-1], "path": path, "size_mb": 1.0, "modified": "", "folder": "Downloads"}


class TestNames:
    keys = footage.keys(CAMPAIGNS)

    def test_a_distinctive_word_or_the_initials_name_a_campaign(self):
        assert footage.by_name("ADULTS 205 Extended Scene #2-V3.mp4", self.keys) == "fx-adults-s2"
        assert footage.by_name("ChadPowers_204_pix_rec709.mov", self.keys) == "chad-powers-s2"
        assert footage.by_name("PLM_s01_ep3_ComingOut.mp4", self.keys) == "please-like-me"

    def test_common_words_and_bare_numbers_name_nothing(self):
        assert footage.by_name("201.mov", self.keys) is None
        assert footage.by_name("the show please like this.mp4", self.keys) is None

    def test_clipper_downloads_are_clips_not_footage(self):
        assert footage.is_clip_download("the chemistry is insane - chad-powers-s2.mp4", set(CAMPAIGNS))
        assert not footage.is_clip_download("ChadPowers_204.mov", set(CAMPAIGNS))


class TestSort:
    def test_recorded_beats_the_name_and_neighbours_follow(self, data_root, tmp_path):
        d = str(tmp_path).replace("\\", "/")
        footage.remember([f"{d}/201.mov"], "fx-adults-s2", "clipped")
        footage.remember([f"{d}/ChadPowers_1.mov"], "fx-adults-s2", "added")  # the user filed it so
        rows = {r["name"]: r for r in footage.sort(
            [src(f"{d}/201.mov"), src(f"{d}/204.mov"), src(f"{d}/1.mp4"), src(f"{d}/ChadPowers_1.mov"),
             src(f"{d}/clip - fx-adults-s2.mp4")], CAMPAIGNS)}
        assert (rows["201.mov"]["campaign"], rows["201.mov"]["sorted_by"]) == ("fx-adults-s2", "clipped")
        assert (rows["204.mov"]["campaign"], rows["204.mov"]["sorted_by"]) == ("fx-adults-s2", "like")
        assert rows["ChadPowers_1.mov"]["campaign"] == "fx-adults-s2"
        assert rows["1.mp4"]["campaign"] is None and "clip - fx-adults-s2.mp4" not in rows

    def test_clipping_outranks_adding(self, data_root):
        footage.remember(["C:/v/a.mp4"], "fx-adults-s2", "clipped")
        footage.remember(["C:/v/a.mp4"], "please-like-me", "added")
        (campaign, how), = footage.known().values()
        assert (campaign, how) == ("fx-adults-s2", "clipped")

    def test_clips_already_made_say_where_their_source_belongs(self, data_root, tmp_path):
        work = data_root / "work" / "abc"
        work.mkdir(parents=True)
        video = tmp_path / "208.mov"
        (work / "info.json").write_text(json.dumps({"media": {"path": str(video)}}), encoding="utf-8")
        with db.connect() as con:
            con.execute("INSERT INTO clips (campaign, source_id, clip_id, title, file, status, created_at) "
                        "VALUES ('fx-adults-s2', 'abc', 'c1', 't', 'f.mp4', 'ready', '2026-09-30')")
        assert footage.known()[str(video.resolve())] == ("fx-adults-s2", "clipped")
