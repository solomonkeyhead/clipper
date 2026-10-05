"""One-click Instagram sign-in (D150): the consent page's code comes back to the local redirect."""

from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

from clipper.instagram import api


def test_the_login_returns_through_the_redirect_and_stores_the_account(data_root, monkeypatch):
    monkeypatch.setenv(api.ENV_ID, "app")
    monkeypatch.setenv(api.ENV_SECRET, "secret")
    monkeypatch.setattr(api, "PORT", 3499)
    monkeypatch.setattr(api, "REDIRECT_URI", "http://localhost:3499/callback/")
    seen = {}

    def consent(url):                      # the browser: approve, and land on the redirect with the code
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        seen["url"] = url
        threading.Timer(0.2, lambda: urllib.request.urlopen(
            f"http://127.0.0.1:3499/callback/?code=abc%23_&state={query['state']}").read()).start()

    class Reply:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return json.dumps(self.body).encode()

    monkeypatch.setattr(api.urllib.request, "urlopen",
                        lambda req, timeout=30: Reply({"data": [{"access_token": "short", "user_id": 1}]})
                        if getattr(req, "full_url", req) == api.CODE_URL else urllib.request.build_opener().open(req, timeout=timeout))
    monkeypatch.setattr(api, "_get", lambda url, params=None: (
        {"access_token": "long"} if "exchange" in str(params) or "ig_exchange_token" in str(params)
        else {"username": "marc"} if url.endswith("/me") else {"access_token": "refreshed"}))
    got = api.login_browser(timeout=10, open_browser=consent)
    assert got["display_name"] == "marc" and "client_id=app" in seen["url"] and "instagram_business_manage_insights" in seen["url"]
    assert api.username() == "marc"
