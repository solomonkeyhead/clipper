"""TikTok Display API sync: login flow, token refresh, paging and log filling.

No request reaches TikTok. The login test drives the real local callback
server with a stand-in browser that makes the redirect TikTok would.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request

import pytest

from clipper.tiktok import api, sync
from clipper.tiktok.api import Video

DAY = 86400.0
NOW = 1_790_000_000.0
CAPTION = "The confidence made it so much worse. Watch Adults season 2 on FX | Hulu @adultsfx  #AdultsFX #fxpartner"


def video(**kw) -> Video:
    base = dict(id="7401", caption=CAPTION, created=NOW - 30 * 3600,
                url="https://www.tiktok.com/@solomonkeyclips/video/7401", duration=33,
                views=412, likes=20, comments=3, shares=1)
    return Video(**{**base, **kw})


def row(**kw) -> dict[str, str]:
    return {"caption": CAPTION, "clip_id": "003_6m37s", **kw}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs")


class TestPkce:
    def test_challenge_is_the_hex_sha256_tiktok_asks_for(self):
        verifier, challenge = api.pkce_pair()
        assert 43 <= len(verifier) <= 128
        assert challenge == hashlib.sha256(verifier.encode()).hexdigest()

    def test_authorize_url_carries_every_required_parameter(self):
        url = api.authorize_url("ck", "st", "ch")
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        assert q == {"client_key": "ck", "response_type": "code", "scope": api.SCOPES,
                     "redirect_uri": "http://localhost:3455/callback/", "state": "st",
                     "code_challenge": "ch", "code_challenge_method": "S256"}


class TestLogin:
    def test_the_redirect_is_caught_and_the_code_exchanged(self, keys, data_root, monkeypatch):
        sent = {}

        def token_request(body):
            sent.update(body)
            return {"access_token": "at", "refresh_token": "rt", "expires_in": 86400,
                    "refresh_expires_in": 31536000, "obtained_at": time.time()}
        monkeypatch.setattr(api, "_token_request", token_request)

        def browser(url):  # what TikTok does after the user approves
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
            back = f"{q['redirect_uri']}?code=abc&state={q['state']}"
            import threading
            threading.Timer(0.3, lambda: urllib.request.urlopen(back, timeout=5).read()).start()

        api.login(timeout=10, open_browser=browser)
        assert sent["code"] == "abc" and sent["grant_type"] == "authorization_code"
        assert len(sent["code_verifier"]) >= 43
        assert json.loads(api.token_path().read_text())["refresh_token"] == "rt"

    def test_missing_app_keys_are_named(self, monkeypatch):
        monkeypatch.delenv("TIKTOK_CLIENT_KEY", raising=False)
        with pytest.raises(api.TikTokError, match="TIKTOK_CLIENT_KEY"):
            api.client()


class TestTokens:
    def store(self, obtained_at):
        api.token_path().parent.mkdir(parents=True, exist_ok=True)
        api.token_path().write_text(json.dumps({
            "access_token": "old", "refresh_token": "rt", "expires_in": 86400,
            "refresh_expires_in": 31536000, "obtained_at": obtained_at}))

    def test_a_fresh_token_is_used_as_is(self, keys, data_root):
        self.store(time.time())
        assert api.access_token() == "old"

    def test_an_expiring_token_is_refreshed_and_the_new_one_kept(self, keys, data_root,
                                                                  monkeypatch):
        self.store(time.time() - 86000)
        monkeypatch.setattr(api, "_token_request", lambda body: {
            "access_token": "new", "refresh_token": "rt2", "expires_in": 86400,
            "refresh_expires_in": 31536000, "obtained_at": time.time()})
        assert api.access_token() == "new"
        assert json.loads(api.token_path().read_text())["refresh_token"] == "rt2"

    def test_not_logged_in_says_what_to_run(self, keys, data_root):
        with pytest.raises(api.TikTokError, match="clipper tiktok login"):
            api.access_token()


class TestListVideos:
    def test_pages_until_has_more_is_false(self, monkeypatch):
        pages = [{"data": {"videos": [{"id": "1", "video_description": "a", "view_count": 5}],
                           "cursor": 111, "has_more": True}, "error": {"code": "ok"}},
                 {"data": {"videos": [{"id": "2", "title": "b", "view_count": 7}],
                           "cursor": 0, "has_more": False}, "error": {"code": "ok"}}]
        bodies = []

        def send(request):
            bodies.append(json.loads(request.data))
            assert "view_count" in request.full_url
            return pages.pop(0)
        monkeypatch.setattr(api, "_send", send)
        videos = api.list_videos("tok")
        assert [(v.id, v.caption, v.views) for v in videos] == [("1", "a", 5), ("2", "b", 7)]
        assert bodies[1]["cursor"] == 111

    def test_an_api_error_is_raised(self, monkeypatch):
        monkeypatch.setattr(api, "_send", lambda r: {"error": {"code": "scope_not_authorized",
                                                               "message": "no video.list"}})
        with pytest.raises(api.TikTokError, match=r"no video.list"):
            api.list_videos("tok")


class TestSync:
    def test_a_video_matches_its_row_by_caption_then_by_id(self):
        rows = [row()]
        sync.apply([video()], rows, now=NOW)
        assert rows[0]["video_id"] == "7401"
        rows[0]["caption"] = "edited later"
        result = sync.apply([video(views=500)], rows, now=NOW)
        assert result.matched and rows[0]["views_latest"] == "500"

    def test_the_24h_snapshot_is_taken_inside_its_window_only(self):
        rows = [row()]
        sync.apply([video(created=NOW - 5 * DAY)], rows, now=NOW)
        assert "views_24h" not in rows[0], "five days old is not a 24-hour number"
        rows = [row()]
        sync.apply([video(created=NOW - 30 * 3600, views=412)], rows, now=NOW)
        sync.apply([video(created=NOW - 30 * 3600, views=900)], rows, now=NOW + 3600)
        assert rows[0]["views_24h"] == "412", "set once, by the first sync in the window"
        assert rows[0]["views_latest"] == "900"

    def test_typed_numbers_are_not_overwritten_and_counts_only_rise(self):
        rows = [row(views_24h="999", likes="50", avg_watch_s="9.8s")]
        sync.apply([video(likes=20)], rows, now=NOW)
        assert rows[0]["views_24h"] == "999" and rows[0]["likes"] == "50"
        assert rows[0]["avg_watch_s"] == "9.8s"
        sync.apply([video(likes=80)], rows, now=NOW)
        assert rows[0]["likes"] == "80"

    def test_unknown_and_ambiguous_videos_are_reported_not_guessed(self):
        rows = [row(clip_id="a"), row(clip_id="b")]
        result = sync.apply([video(), video(id="9", caption="Something else entirely")],
                            rows, now=NOW)
        assert len(result.ambiguous) == 1 and len(result.unmatched) == 1
        assert not any(r.get("video_id") for r in rows)
