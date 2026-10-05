"""Sign in with no app of the user's own (D151): Clipper -> the connect service -> the platform -> back."""

from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from clipper.broker import app as broker
from clipper.studio import server


@pytest.fixture
def service(monkeypatch):
    mine = {"PUBLIC_URL": "https://connect.test", "BROKER_SIGNING_KEY": "k" * 32, "TIKTOK_CLIENT_KEY": "tk",
            "TIKTOK_CLIENT_SECRET": "ts"}              # the service's settings, apart from this process's environment
    monkeypatch.setattr(broker, "_env", lambda name: mine.get(name, ""))
    monkeypatch.setattr(broker, "_post", lambda url, data: {"access_token": "A", "refresh_token": "R", "open_id": "o1",
                                                             "expires_in": 86400, "refresh_expires_in": 31536000,
                                                             "got": data.get("code", "")})
    return TestClient(broker.create_app(), follow_redirects=False)


def test_the_login_goes_through_the_service_and_comes_back_to_this_clipper(data_root, service, monkeypatch):
    monkeypatch.setenv("CLIPPER_BROKER_URL", "https://connect.test")
    for name in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)        # this user has no app of their own
    from clipper.tiktok import api

    monkeypatch.setattr(api, "_send", lambda request: {"data": {"user": {"display_name": "Sam"}}})

    def browser(link):                                  # the person's browser: service -> platform -> service -> Clipper
        start = service.get(link.replace("https://connect.test", ""))
        assert start.status_code == 307 and "tiktok.com" in start.headers["location"] and "client_key=tk" in start.headers["location"]
        state = start.headers["location"].split("state=")[1].split("&")[0]
        page = service.get(f"/callback/tiktok?code=abc&state={state}").text
        payload = page.split("value='")[1].split("'></form>")[0].replace("&quot;", '"')
        local = TestClient(server.create_app())
        threading.Timer(0.1, lambda: local.post("/api/accounts/broker/return", data={"payload": payload})).start()

    got = api.login(timeout=10, open_browser=browser)
    assert got["display_name"] == "Sam" and api.token_files()


def test_only_a_login_this_clipper_started_is_accepted(data_root):
    client = TestClient(server.create_app())
    forged = client.post("/api/accounts/broker/return", data={"payload": json.dumps({"nonce": "nope", "tokens": {"access_token": "x"}})})
    assert "wasn't started here" in forged.text


def test_the_service_hands_a_login_only_back_to_the_users_own_computer(service):
    assert service.get("/login/tiktok?nonce=n&return=https://evil.test/x").status_code == 400
    assert service.get("/login/tiktok?nonce=n&return=http://127.0.0.1:8765/api/accounts/broker/return").status_code == 307
    assert service.get("/callback/tiktok?code=c&state=forged.sig").status_code == 400


def test_a_refresh_goes_through_the_service(service):
    assert service.post("/refresh/tiktok", data={"refresh_token": "R"}).json()["access_token"] == "A"
    assert service.post("/refresh/instagram", data={"refresh_token": "R"}).status_code == 404
