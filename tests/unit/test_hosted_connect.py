"""Connecting accounts on the hosted site (D239): the gateway serves the connect service on the site itself, so
the login's post-back is same-site, carries the session and reaches the signed-in user's own Clipper.

The user's Clipper is the real studio app, served in a thread; the platforms' token endpoints are faked.
"""

from __future__ import annotations

import html
import json
import time
import urllib.parse
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from clipper import connect
from clipper.broker import app as broker
from clipper.hosted import procs as procs_mod
from clipper.hosted import users
from clipper.hosted.app import create_app
from clipper.hosted.procs import Procs

from .test_hosted_proxy import serve

SITE = "http://testserver"
SERVICE = {"TIKTOK_CLIENT_KEY": "tk", "TIKTOK_CLIENT_SECRET": "ts", "BROKER_SIGNING_KEY": "k" * 32}


@pytest.fixture
def platform(monkeypatch):
    """TikTok's token endpoint: what it was sent, and tokens back."""
    sent = []
    monkeypatch.setattr(broker, "_post", lambda url, data: sent.append(data) or {
        "access_token": "A", "refresh_token": "R", "open_id": "o1", "expires_in": 86400})
    return sent


@pytest.fixture
def site(tmp_path, monkeypatch, data_root, platform):
    """The gateway, with one user's Clipper (the real studio app) behind it, hosted as the gateway starts it."""
    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("CLIPPER_PUBLIC_URL", SITE)
    for name, value in SERVICE.items():
        monkeypatch.setenv(name, value)
    for name in ("PUBLIC_URL", "CLIPPER_BROKER_URL", "CLIPPER_BROKER_LOCAL_URL", "CLIPPER_BROKER_PLATFORMS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in procs_mod.connect_env().items():     # what the gateway hands this user's Clipper
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.setenv("CLIPPER_TOKEN", "user-token")
    from clipper.studio import server

    asked = []

    class OneStudio(Procs):
        def running(self, user_id):
            return studio

        async def get(self, user_id):
            asked.append(user_id)
            return studio

    with serve(server.create_app()) as base:
        studio = SimpleNamespace(base=base, token="user-token", inflight=0, last_used=time.time())
        procs = OneStudio()
        with TestClient(create_app(procs), headers={"origin": SITE}, follow_redirects=False) as client:
            client.asked = asked
            yield client


def signup(c):
    r = c.post("/signup", data={"email": "someone@example.com", "password": "a long password", "adult": "1",
                                "terms": "1"})
    assert r.status_code == 303
    return users.by_email("someone@example.com")["id"]


def through_the_platform(c, link: str) -> tuple[str, str]:
    """The browser: the site's connect service -> TikTok (approve) -> back to the service's hand-off page."""
    assert link.startswith(f"{SITE}/connect/login/tiktok?")
    start = c.get(link.removeprefix(SITE))
    assert start.status_code == 307
    to = urllib.parse.urlsplit(start.headers["location"])
    query = dict(urllib.parse.parse_qsl(to.query))
    assert to.netloc == "www.tiktok.com" and query["redirect_uri"] == f"{SITE}/connect/callback/tiktok"
    page = c.get(f"/connect/callback/tiktok?code=abc&state={query['state']}").text
    action = html.unescape(page.split("action='")[1].split("'")[0])
    payload = html.unescape(page.split("value='")[1].split("'></form>")[0])
    return action, payload


def test_a_hosted_login_comes_back_to_the_signed_in_users_own_clipper(site, platform):
    uid = signup(site)
    got = {}

    def browser(link):
        action, payload = through_the_platform(site, link)
        assert action == f"{SITE}{connect.RETURN}"              # the site's own address: same site, so the cookie goes
        forged = site.post(connect.RETURN, data={"payload": json.dumps({"nonce": "nope", "tokens": {}})})
        assert "wasn't started here" in forged.text             # the nonce still decides
        got["reply"] = site.post(action.removeprefix(SITE), data={"payload": payload})

    tokens = connect.login("tiktok", timeout=10, open_browser=browser)
    assert tokens["access_token"] == "A" and platform[0]["code"] == "abc"
    assert got["reply"].status_code == 200 and "Connected" in got["reply"].text
    assert site.asked and set(site.asked) == {uid}              # the post went to this user's studio


def test_a_post_back_without_the_session_reaches_no_studio(site):
    signup(site)
    site.cookies.clear()
    r = site.post(connect.RETURN, data={"payload": json.dumps({"nonce": "n", "tokens": {"access_token": "x"}})})
    assert r.status_code == 401 and not site.asked


def test_the_sites_service_hands_logins_only_to_the_site_or_this_computer(site):
    def start(back):
        return site.get("/connect/login/tiktok?" + urllib.parse.urlencode({"nonce": "n", "return": back})).status_code

    assert start(f"{SITE}{connect.RETURN}") == 307
    assert start("http://127.0.0.1:8765/api/accounts/broker/return") == 307      # a local Clipper may use it too
    assert start("https://evil.example/api/accounts/broker/return") == 400
    assert start(f"{SITE}/somewhere-else") == 400
    assert start("http://127.0.0.1:1@evil.example/") == 400
    assert site.get("/connect").status_code == 404                               # the gateway's, never proxied


def test_a_refresh_reaches_the_gateways_service_with_no_session(site, monkeypatch, platform):
    with serve(create_app(Procs())) as gateway:
        monkeypatch.setenv("CLIPPER_BROKER_LOCAL_URL", gateway + broker.MOUNT)
        assert connect.refresh("tiktok", "R")["access_token"] == "A"
    assert platform[-1] == {"client_key": "tk", "client_secret": "ts", "grant_type": "refresh_token",
                            "refresh_token": "R"}


def test_users_clippers_get_the_sites_service_and_none_of_its_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path))
    monkeypatch.setenv("CLIPPER_PUBLIC_URL", "https://clip.example/")
    monkeypatch.setenv("PORT", "8081")
    monkeypatch.setenv("CLIPPER_BROKER_URL", "https://outside.example")
    for name, value in SERVICE.items():
        monkeypatch.setenv(name, value)
    for name in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    env = procs_mod.user_env(1)
    assert env["CLIPPER_BROKER_URL"] == "https://clip.example/connect"
    assert env["CLIPPER_BROKER_LOCAL_URL"] == "http://127.0.0.1:8081/connect"
    assert env["CLIPPER_BROKER_PLATFORMS"] == "tiktok"
    assert not {"TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "BROKER_SIGNING_KEY"} & set(env)
    monkeypatch.delenv("TIKTOK_CLIENT_SECRET")
    assert "CLIPPER_BROKER_URL" not in procs_mod.user_env(1)                  # no app on the site: no service


def test_hosted_links_and_platforms(monkeypatch):
    monkeypatch.setenv("CLIPPER_BROKER_URL", "https://clip.example/connect")
    monkeypatch.setenv("CLIPPER_BROKER_PLATFORMS", "tiktok,instagram")
    monkeypatch.setenv("CLIPPER_PUBLIC_URL", "https://clip.example")
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    assert connect.local_return() == "https://clip.example/api/accounts/broker/return"
    assert connect.enabled("tiktok") and connect.enabled("instagram") and not connect.enabled("youtube")


def test_local_mode_is_unchanged(monkeypatch):
    for name in ("CLIPPER_HOSTED", "CLIPPER_HOST", "CLIPPER_BROKER_PLATFORMS", "CLIPPER_BROKER_LOCAL_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLIPPER_PUBLIC_URL", "https://clip.example")      # set or not, the local Clipper ignores it
    monkeypatch.setenv("CLIPPER_BROKER_URL", "https://connect.example/")
    assert connect.local_return() == "http://127.0.0.1:8765/api/accounts/broker/return"
    assert connect.url() == "https://connect.example"
    assert all(connect.enabled(p) for p in connect.PLATFORMS) and connect.enabled()
    seen = {}
    monkeypatch.setattr(connect.httpx, "post", lambda url, **kw: seen.setdefault("url", url) and SimpleNamespace(
        status_code=200, headers={}, json=lambda: {"access_token": "A"}))
    connect.refresh("tiktok", "R")
    assert seen["url"] == "https://connect.example/refresh/tiktok"
