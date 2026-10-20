"""The gateway's accounts, pages and plan.json (D228). No real Clipper starts: a stand-in answers instead."""

from __future__ import annotations

import io
import json
import re
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from clipper.hosted import catalog, data_dir, users
from clipper.hosted.app import create_app
from clipper.hosted.procs import Procs

from .test_hosted_proxy import fake_command

PASSWORD = "a long password"


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("CLIPPER_ADMINS", "Boss@Example.com")
    for name in ("CLIPPER_PUBLIC_URL", "SMTP_URL"):
        monkeypatch.delenv(name, raising=False)
    procs = Procs(fake_command(tmp_path), ready_timeout=15)
    with TestClient(create_app(procs), headers={"origin": "http://testserver"}, follow_redirects=False) as client:
        yield client
    procs.stop_all()


def signup(c, email="Someone@Example.com", password=PASSWORD, **extra):
    return c.post("/signup", data={"email": email, "password": password, "adult": "1", "terms": "1", **extra})


def test_signup_signs_in_and_writes_a_valid_plan(gw):
    r = signup(gw)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"]
    assert "clipper_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    user = users.by_email("someone@example.com")
    assert user and user["age_confirmed"] == 1 and user["terms_version"]
    assert user["password"].startswith("scrypt$16384$8$1$")
    plan = json.loads((data_dir(user["id"]) / "plan.json").read_text())
    assert set(plan) == {"plan", "status", "period_start", "period_end", "limits", "credits", "ai_budget_usd",
                         "billing_url"}
    assert plan["plan"] == "free" and plan["status"] == "active" and plan["billing_url"] == "/billing"
    assert plan["limits"] == {"minutes": 60, "shorts": 1, "shorts_once": True, "redraws_per_short": 5,
                              "retention_days": 7, "accounts_per_platform": 1, "seats": 1, "watermark_after": 3,
                              "queue": "normal"}
    assert plan["credits"] == {"minutes": 0, "shorts": 0} and plan["ai_budget_usd"] == 2.0
    start, end = catalog.parse(plan["period_start"]), catalog.parse(plan["period_end"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT00:00:00Z", plan["period_start"])
    assert 28 <= (end - start).days <= 31
    assert gw.get("/billing").status_code == 200


def test_signup_refuses_bad_input(gw):
    assert signup(gw, password="short").status_code == 400
    assert signup(gw, email="not-an-email").status_code == 400
    r = gw.post("/signup", data={"email": "a@b.co", "password": PASSWORD, "terms": "1"})
    assert r.status_code == 400 and "18" in r.text
    assert signup(gw).status_code == 303
    gw.cookies.clear()
    assert "already an account" in signup(gw, email="SOMEONE@example.com").text


def test_forms_from_another_site_are_refused(gw):
    r = gw.post("/login", data={"email": "a@b.co", "password": PASSWORD}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_login_logout_and_wrong_password(gw):
    signup(gw)
    gw.cookies.clear()
    assert gw.get("/billing").status_code == 303
    assert gw.post("/login", data={"email": "someone@example.com", "password": "wrong password!"}).status_code == 401
    r = gw.post("/login", data={"email": " SOMEONE@example.com ", "password": PASSWORD, "next": "/clips"})
    assert r.status_code == 303 and r.headers["location"] == "/clips"
    assert gw.get("/billing").status_code == 200
    assert gw.post("/login", data={"email": "someone@example.com", "password": PASSWORD,
                                   "next": "//evil.example"}).headers["location"] == "/"
    assert gw.post("/logout").status_code == 303
    assert gw.get("/billing").status_code == 303


def test_login_is_rate_limited(gw):
    signup(gw)
    gw.cookies.clear()
    for _ in range(10):
        assert gw.post("/login", data={"email": "someone@example.com", "password": "nope nope nope"}).status_code == 401
    r = gw.post("/login", data={"email": "someone@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_reset_link_works_once_and_expires(gw):
    signup(gw)
    gw.cookies.clear()
    assert gw.post("/reset", data={"email": "nobody@example.com"}).status_code == 200
    assert "Check your email" in gw.post("/reset", data={"email": "someone@example.com"}).text
    uid = users.by_email("someone@example.com")["id"]
    _, link, _ = gw.app.state.pending_resets[uid]   # no SMTP: the link waits on /admin
    token = link.split("token=")[1]
    assert gw.get(f"/reset?token={token}").status_code == 200
    r = gw.post("/reset", data={"token": token, "password": "a brand new password"})
    assert r.status_code == 303
    assert gw.post("/reset", data={"token": token, "password": "another new password"}).status_code == 400
    gw.cookies.clear()
    assert gw.post("/login", data={"email": "someone@example.com", "password": PASSWORD}).status_code == 401
    assert gw.post("/login", data={"email": "someone@example.com",
                                   "password": "a brand new password"}).status_code == 303
    old = users.new_reset(uid, now=time.time() - 2 * users.RESET_MINUTES * 60)
    assert users.reset_user(old) is None


def test_session_expiry(gw):
    signup(gw)
    uid = users.by_email("someone@example.com")["id"]
    token = users.new_session(uid, now=time.time() - (users.SESSION_DAYS + 1) * 86400)
    assert users.session_user(token) is None
    assert users.session_user(users.new_session(uid))["id"] == uid


def test_signed_out_pages(gw):
    assert gw.get("/").status_code == 200
    assert gw.get("/clips").headers["location"] == "/login?next=/clips"
    assert gw.get("/api/status").status_code == 401
    for name in ("pricing", "terms", "privacy", "dmca", "refunds"):
        assert gw.get(f"/{name}").status_code == 200
    assert gw.get("/healthz").json() == {"ok": True}
    # Under no-referrer a browser posts the page's own forms with "Origin: null", and they were refused.
    assert gw.get("/login").headers["referrer-policy"] == "same-origin"


def test_signed_in_requests_reach_the_users_own_studio(gw):
    signup(gw)
    uid = users.by_email("someone@example.com")["id"]
    got = gw.get("/api/status?x=1").json()
    assert got["path"] == "/api/status?x=1" and got["host"] == "testserver"
    assert got["data"] == str(data_dir(uid))


def test_admin_only_for_admins_and_sets_plans(gw):
    signup(gw)
    assert gw.get("/admin").status_code == 404
    uid = users.by_email("someone@example.com")["id"]
    gw.cookies.clear()
    signup(gw, email="boss@example.com")
    page = gw.get("/admin")
    assert page.status_code == 200 and "someone@example.com" in page.text and "free" in page.text
    assert gw.post("/admin/plan", data={"user": str(uid), "plan": "pro"}).status_code == 303
    plan = json.loads((data_dir(uid) / "plan.json").read_text())
    assert plan["plan"] == "pro" and plan["limits"]["minutes"] == 600 and plan["limits"]["watermark_after"] is None
    assert plan["ai_budget_usd"] == 20.0


def test_packs_add_credits_budget_and_lift_free_limits(gw):
    signup(gw)
    user = users.by_email("someone@example.com")
    users.add_credits(user["id"], minutes=120, source="pack:campaign")
    users.add_credits(user["id"], shorts=5, source="pack:shorts")
    plan = catalog.plan_json(user)
    assert plan["credits"] == {"minutes": 120, "shorts": 5}
    assert plan["ai_budget_usd"] == 2.0 + 2.5 + 4.5
    assert plan["limits"]["watermark_after"] is None and plan["limits"]["retention_days"] == 30


def test_free_period_rolls_over(gw):
    signup(gw)
    user = users.update(users.by_email("someone@example.com")["id"],
                        period_start="2026-01-31T00:00:00Z", period_end="2026-02-28T00:00:00Z")
    rolled = catalog.roll(user, now=catalog.parse("2026-04-02T10:00:00Z").timestamp())
    assert (rolled["period_start"], rolled["period_end"]) == ("2026-03-28T00:00:00Z", "2026-04-28T00:00:00Z")
    paid = users.update(user["id"], paddle_subscription_id="sub_1")
    assert catalog.roll(paid, now=time.time() + 10**8) == paid   # Paddle sets paid periods


def test_export_leaves_caches_out(gw):
    signup(gw)
    data = data_dir(users.by_email("someone@example.com")["id"])
    for rel in ("library/c/clip.mp4", "campaigns/c.yaml", "cache/x.bin", "work/s/transcript.json",
                "create/stock/1.mp4", "create/videos/1/shots/a.mp4", "create/videos/1/voice.mp3"):
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(rel)
    r = gw.get("/billing/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == {"plan.json", "library/c/clip.mp4", "campaigns/c.yaml", "create/videos/1/voice.mp3"}


def test_delete_account_removes_the_folder(gw):
    signup(gw)
    uid = users.by_email("someone@example.com")["id"]
    folder = data_dir(uid).parent
    assert folder.is_dir()
    assert gw.post("/billing/delete", data={"password": "not my password"}).status_code == 401
    assert folder.is_dir()
    r = gw.post("/billing/delete", data={"password": PASSWORD})
    assert r.status_code == 200 and not folder.exists()
    gone = users.get_user(uid)
    assert gone["deleted_at"] and gone["email"] == f"deleted-{uid}"
    assert gw.get("/billing").status_code == 303   # the session ended
    assert gw.post("/login", data={"email": "someone@example.com", "password": PASSWORD}).status_code == 401
    assert signup(gw).status_code == 303   # the address is free again


def test_cookie_is_secure_behind_https(gw):
    r = gw.post("/signup", data={"email": "x@example.com", "password": PASSWORD, "adult": "1", "terms": "1"},
                headers={"x-forwarded-proto": "https"})
    assert "Secure" in r.headers["set-cookie"]
