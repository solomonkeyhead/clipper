"""Importing campaign footage from shared Drive, Dropbox and direct links."""

from __future__ import annotations

import time

import pytest
import yaml

from clipper.studio import imports


@pytest.fixture
def client(data_root, tmp_path, monkeypatch, valid_campaign_dict):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    (campaigns / "test-campaign.yaml").write_text(yaml.safe_dump(valid_campaign_dict), encoding="utf-8")
    monkeypatch.setattr(server, "campaigns_dir", lambda: campaigns)
    return TestClient(server.create_app())


class Page:
    """A fake HTTP response."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self.parts = [body[:3], body[3:], b""]
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return b"".join(self.parts) if n == -1 else self.parts.pop(0)


def test_links_are_recognised():
    drive = imports.inspect("https://drive.google.com/file/d/1AbC_d-9/view?usp=sharing")
    assert drive.kind == "drive-file" and "id=1AbC_d-9" in drive.files[0].url
    assert "confirm=t" in drive.files[0].url
    box = imports.inspect("https://www.dropbox.com/scl/fi/xyz/Episode%201.mp4?rlkey=k&dl=0")
    assert box.kind == "dropbox-file" and box.files[0].name == "Episode 1.mp4"
    assert "dl=1" in box.files[0].url and "rlkey=k" in box.files[0].url
    assert imports.inspect("https://www.dropbox.com/scl/fo/abc/def?rlkey=k&dl=0").zipped
    assert imports.inspect("https://cdn.example.com/eps/ep2.mov").kind == "direct"
    for bad in ("https://wetransfer.com/downloads/x", "https://example.com/page", "ftp://x/y.mp4"):
        with pytest.raises(imports.ImportError_):
            imports.inspect(bad)


def test_a_drive_folder_lists_its_videos(monkeypatch):
    page = ('<div class="flip-entry" id="entry-AAA"><div class="flip-entry-title">Ep 1.mov</div></div>'
            '<div class="flip-entry" id="entry-BBB"><div class="flip-entry-title">notes.pdf</div></div>')
    monkeypatch.setattr(imports, "_open", lambda url: Page(page.encode()))
    found = imports.inspect("https://drive.google.com/drive/folders/FOLDER1?usp=sharing")
    assert [f.name for f in found.files] == ["Ep 1.mov"] and "id=AAA" in found.files[0].url


def wait_for(client, import_id):
    for _ in range(60):
        state = next(i for i in client.get("/api/imports").json() if i["id"] == import_id)
        if state["status"] in ("done", "failed"):
            return state
        time.sleep(0.05)
    return state


def test_an_import_downloads_and_remembers_the_link(client, data_root, monkeypatch):
    monkeypatch.setattr(imports, "_open", lambda url: Page(b"abcdef", {
        "Content-Type": "video/mp4", "Content-Length": "6",
        "Content-Disposition": 'attachment; filename="Chad 201.mp4"'}))
    started = client.post("/api/imports", json={"url": "https://drive.google.com/file/d/XYZ/view"}).json()
    state = wait_for(client, started["id"])
    assert state["status"] == "done", state
    assert (data_root / "downloads" / "Chad 201.mp4").read_bytes() == b"abcdef"
    assert imports.origin("Chad 201")["link"].endswith("/file/d/XYZ/view")


def test_a_private_link_says_so(client, monkeypatch):
    monkeypatch.setattr(imports, "_open", lambda url: Page(b"<html>sign in</html>",
                                                           {"Content-Type": "text/html"}))
    started = client.post("/api/imports", json={"url": "https://drive.google.com/file/d/P/view"}).json()
    state = wait_for(client, started["id"])
    assert state["status"] == "failed" and "private" in state["message"]
