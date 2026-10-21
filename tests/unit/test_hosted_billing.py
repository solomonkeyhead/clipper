"""Paddle billing and team seats in the gateway (D234). No request reaches Paddle: httpx.request is replaced."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time

import httpx
import pytest

from clipper.hosted import billing, catalog, data_dir, users

from .test_hosted_accounts import (
    PASSWORD,
    gw,  # noqa: F401  (a fixture, used by c)
    signup,
)

SECRET = "pdl_ntfset_test_secret"
IDS = itertools.count()
PRICES = {"STARTER_MONTHLY": "pri_sm", "STARTER_YEARLY": "pri_sy", "PRO_MONTHLY": "pri_pm", "PRO_YEARLY": "pri_py",
          "AGENCY_MONTHLY": "pri_am", "AGENCY_YEARLY": "pri_ay", "PACK_CAMPAIGN": "pri_kc",
          "PACK_MINUTES": "pri_km", "PACK_SHORTS": "pri_ks"}


@pytest.fixture
def paddle(monkeypatch):
    monkeypatch.setenv("PADDLE_ENV", "sandbox")
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_sdbx_apikey_test")
    monkeypatch.setenv("PADDLE_CLIENT_TOKEN", "test_clienttoken")
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", SECRET)
    for name, pid in PRICES.items():
        monkeypatch.setenv(f"PADDLE_PRICE_{name}", pid)
    calls = []

    def fake(method, url, *, json=None, **_):
        calls.append((method, url, json))
        data = {"urls": {"general": {"overview": "https://customer-portal.paddle.com/cpl_x"}}}
        return httpx.Response(200, json={"data": data}, request=httpx.Request(method, url))

    monkeypatch.setattr(billing.httpx, "request", fake)
    return calls


@pytest.fixture
def c(gw):  # noqa: F811
    return gw


def send(c, event, *, secret=SECRET, ts=None):
    body = json.dumps(event).encode()
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return c.post("/paddle/webhook", content=body, headers={"paddle-signature": f"ts={ts};h1={sig}",
                                                            "content-type": "application/json"})


def sub_event(kind, uid, *, eid=None, status="active", price="pri_sm", updated="2026-10-20T10:00:00.123456Z",
              start="2026-10-20T10:00:00.5Z", end="2026-11-20T10:00:00.5Z", scheduled=None, tag=True):
    custom = {"user_id": str(uid), "tag": billing.user_tag(uid) if tag else "forged"}
    period = {"starts_at": start, "ends_at": end} if status not in ("canceled", "paused") else None
    return {"event_id": eid or f"evt_{kind}_{next(IDS)}", "event_type": kind,
            "occurred_at": updated, "data": {
                "id": "sub_1", "status": status, "customer_id": "ctm_1", "updated_at": updated,
                "items": [{"price": {"id": price}, "quantity": 1}], "current_billing_period": period,
                "scheduled_change": scheduled, "custom_data": custom}}


def me():
    return users.by_email("someone@example.com")


def plan_file(uid):
    return json.loads((data_dir(uid) / "plan.json").read_text())


def test_signature_valid_invalid_stale_and_rotated():
    body, now = b'{"a": 1}', time.time()
    ts = str(int(now))
    good = hmac.new(SECRET.encode(), ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    assert billing.verify(f"ts={ts};h1={good}", body, SECRET, now)
    assert billing.verify(f"ts={ts};h1={'0' * 64};h1={good}", body, SECRET, now)   # during a secret rotation
    assert not billing.verify(f"ts={ts};h1={good}", body + b" ", SECRET, now)
    assert not billing.verify(f"ts={ts};h1={good}", body, "another secret", now)
    assert not billing.verify(f"ts={ts};h1={good}", body, SECRET, now + 301)       # stale: replayed later
    assert not billing.verify(f"h1={good}", body, SECRET, now)
    assert not billing.verify("", body, SECRET, now) and not billing.verify(f"ts={ts};h1={good}", body, "", now)


def test_webhook_refuses_a_bad_signature(c, paddle):
    signup(c)
    event = sub_event("subscription.created", me()["id"])
    assert send(c, event, secret="wrong").status_code == 401
    assert send(c, event, ts=int(time.time()) - 600).status_code == 401
    assert me()["plan"] == "free"


def test_subscription_created_sets_the_plan_once(c, paddle):
    signup(c)
    uid = me()["id"]
    event = sub_event("subscription.created", uid, eid="evt_1")
    assert send(c, event).json() == {"ok": True}
    user = me()
    assert (user["plan"], user["status"], user["paddle_subscription_id"], user["paddle_customer_id"]) == \
        ("starter", "active", "sub_1", "ctm_1")
    assert (user["period_start"], user["period_end"]) == ("2026-10-20T10:00:00Z", "2026-11-20T10:00:00Z")
    plan = plan_file(uid)
    assert plan["plan"] == "starter" and plan["limits"]["minutes"] == 300 and plan["limits"]["watermark_after"] is None
    # Paddle's retry of the same event is skipped, even if the account changed in between.
    users.update(uid, plan="pro")
    assert send(c, event).json() == {"ok": True, "seen": True}
    assert me()["plan"] == "pro"


def test_forged_custom_data_or_unknown_ids_change_nothing(c, paddle):
    signup(c)
    uid = me()["id"]
    assert send(c, sub_event("subscription.created", uid, tag=False)).status_code == 200
    assert me()["plan"] == "free" and me()["paddle_subscription_id"] is None
    assert send(c, sub_event("subscription.created", 9999)).status_code == 200
    assert send(c, sub_event("subscription.created", uid, price="pri_unknown")).status_code == 200
    assert me()["plan"] == "free"


def test_plan_change_cancel_scheduled_then_canceled(c, paddle):
    signup(c)
    uid = me()["id"]
    send(c, sub_event("subscription.created", uid))
    send(c, sub_event("subscription.updated", uid, price="pri_pm", updated="2026-10-21T00:00:00Z"))
    assert me()["plan"] == "pro" and plan_file(uid)["limits"]["minutes"] == 600
    # An older state arriving late doesn't undo the newer one.
    send(c, sub_event("subscription.updated", uid, price="pri_sm", updated="2026-10-20T12:00:00Z"))
    assert me()["plan"] == "pro"
    # The user cancels: paid limits stay until the period ends.
    send(c, sub_event("subscription.updated", uid, price="pri_pm", updated="2026-10-22T00:00:00Z",
                       scheduled={"action": "cancel", "effective_at": "2026-11-20T10:00:00Z"}))
    assert (me()["plan"], me()["status"]) == ("pro", "canceled") and plan_file(uid)["status"] == "canceled"
    # Paddle's canceled event at the period's end: back to Free with a new month.
    send(c, sub_event("subscription.canceled", uid, status="canceled", price="pri_pm",
                       updated="2026-11-20T10:00:01Z"))
    user = me()
    assert (user["plan"], user["status"], user["paddle_subscription_id"]) == ("free", "active", None)
    assert user["paddle_customer_id"] == "ctm_1"
    assert plan_file(uid)["plan"] == "free" and plan_file(uid)["limits"]["minutes"] == 60


def test_a_canceled_plan_ends_at_its_period_even_without_paddles_event(c, paddle):
    signup(c)
    user = users.update(me()["id"], plan="pro", status="canceled", paddle_subscription_id="sub_1",
                        period_start="2026-10-01T00:00:00Z", period_end="2026-11-01T00:00:00Z")
    assert catalog.roll(user, now=catalog.parse("2026-10-31T00:00:00Z").timestamp())["plan"] == "pro"
    after = catalog.roll(user, now=catalog.parse("2026-11-01T00:00:01Z").timestamp())
    assert (after["plan"], after["status"], after["paddle_subscription_id"]) == ("free", "active", None)


def test_past_due_keeps_access_for_a_grace_then_free(c, paddle):
    signup(c)
    uid = me()["id"]
    send(c, sub_event("subscription.created", uid))
    send(c, sub_event("subscription.past_due", uid, status="past_due", updated="2026-11-20T11:00:00Z"))
    user = me()
    assert (user["plan"], user["status"]) == ("starter", "past_due") and user["grace_until"]
    assert plan_file(uid)["status"] == "past_due" and plan_file(uid)["limits"]["minutes"] == 300
    assert "payment didn't go through" in c.get("/billing").text
    later = catalog.parse(user["grace_until"]).timestamp() + 1
    assert catalog.roll(user, now=later - 3600)["plan"] == "starter"
    assert catalog.roll(user, now=later)["plan"] == "free"
    # Paid after all: active again, the grace forgotten.
    send(c, sub_event("subscription.updated", uid, updated="2026-11-21T00:00:00Z"))
    assert (me()["plan"], me()["status"], me()["grace_until"]) == ("starter", "active", None)


def test_yearly_plan_counts_its_allowance_by_month(c, paddle):
    signup(c)
    uid = me()["id"]
    send(c, sub_event("subscription.created", uid, price="pri_sy", start="2026-01-31T09:00:00Z",
                       end="2027-01-31T09:00:00Z"))
    user = me()
    assert catalog.allowance_period(user, catalog.parse("2026-03-15T00:00:00Z").timestamp()) == \
        ("2026-02-28T09:00:00Z", "2026-03-31T09:00:00Z")
    assert catalog.allowance_period(user, catalog.parse("2027-01-30T00:00:00Z").timestamp()) == \
        ("2026-12-31T09:00:00Z", "2027-01-31T09:00:00Z")
    month = catalog.allowance_period(user)
    assert plan_file(uid)["period_start"] == month[0]


def txn_event(uid, *prices, tag=True):
    return {"event_id": f"evt_txn_{next(IDS)}", "event_type": "transaction.completed", "data": {
        "id": "txn_1", "status": "completed", "customer_id": "ctm_9", "subscription_id": None,
        "custom_data": {"user_id": str(uid), "tag": billing.user_tag(uid) if tag else "x"},
        "items": [{"price": {"id": p}, "quantity": 1} for p in prices]}}


def test_packs_add_credits_and_unknown_prices_are_ignored(c, paddle):
    signup(c)
    uid = me()["id"]
    assert send(c, txn_event(uid, "pri_kc")).status_code == 200
    plan = plan_file(uid)
    assert plan["plan"] == "free" and plan["credits"] == {"minutes": 120, "shorts": 0}
    assert plan["limits"]["watermark_after"] is None and plan["limits"]["retention_days"] == 30
    send(c, txn_event(uid, "pri_km", "pri_ks"))
    assert plan_file(uid)["credits"] == {"minutes": 420, "shorts": 5}
    assert plan_file(uid)["ai_budget_usd"] == 5.5 + 2.5 + 6.0 + 21.0
    assert me()["paddle_customer_id"] == "ctm_9"
    assert send(c, txn_event(uid, "pri_nope")).status_code == 200
    assert send(c, txn_event(uid, "pri_sm")).status_code == 200   # a plan's payment: the subscription has it
    assert send(c, txn_event(uid, "pri_ks", tag=False)).status_code == 200   # found by customer id instead
    assert plan_file(uid)["credits"] == {"minutes": 420, "shorts": 10}
    users.update(uid, paddle_customer_id=None)
    assert send(c, txn_event(uid, "pri_ks", tag=False)).status_code == 200   # nobody: logged, 200
    assert plan_file(uid)["credits"] == {"minutes": 420, "shorts": 10}


def test_a_full_refund_is_noted_and_ends_the_subscription(c, paddle):
    signup(c)
    uid = me()["id"]
    send(c, sub_event("subscription.created", uid))
    pending = {"event_id": "evt_adj_1", "event_type": "adjustment.created", "data": {
        "id": "adj_1", "action": "refund", "status": "pending_approval", "type": "full", "transaction_id": "txn_1",
        "subscription_id": "sub_1", "customer_id": "ctm_1"}}
    send(c, pending)
    assert me()["refunded_at"] is None and not paddle
    approved = {**pending, "event_id": "evt_adj_2", "event_type": "adjustment.updated",
                "data": {**pending["data"], "status": "approved"}}
    send(c, approved)
    assert me()["refunded_at"]
    assert paddle == [("POST", "https://sandbox-api.paddle.com/subscriptions/sub_1/cancel",
                       {"effective_from": "immediately"})]
    c.cookies.clear()
    signup(c, email="boss@example.com")
    assert "Refund given" in c.get("/admin").text


def test_billing_page_without_paddle_settings(c, monkeypatch):
    monkeypatch.delenv("PADDLE_API_KEY", raising=False)
    signup(c)
    page = c.get("/billing").text
    assert "Payments aren't set up yet" in page and "paddle.js" not in page and "data-price" not in page


def test_billing_page_with_paddle(c, paddle):
    signup(c)
    uid = me()["id"]
    page = c.get("/billing").text
    assert "https://cdn.paddle.com/paddle/v2/paddle.js" in page and 'Paddle.Environment.set("sandbox")' in page
    assert '"test_clienttoken"' in page and "someone@example.com" in page and billing.user_tag(uid) in page
    for pid in ("pri_sm", "pri_sy", "pri_am", "pri_kc", "pri_km", "pri_ks"):
        assert f'data-price="{pid}"' in page
    assert 'href="/refunds"' in page and "Manage or cancel" not in page   # no Paddle customer yet
    # A subscriber changes plan on the subscription they have and gets the portal.
    send(c, sub_event("subscription.created", uid))
    page = c.get("/billing").text
    assert 'data-price="pri_pm"' not in page and 'action="/billing/change"' in page and "Manage or cancel" in page
    assert c.post("/billing/change", data={"plan": "pro", "cycle": "yearly"}).status_code == 200
    assert paddle[-1] == ("PATCH", "https://sandbox-api.paddle.com/subscriptions/sub_1",
                          {"items": [{"price_id": "pri_py", "quantity": 1}],
                           "proration_billing_mode": "prorated_immediately"})
    r = c.post("/billing/portal")
    assert r.status_code == 303 and r.headers["location"] == "https://customer-portal.paddle.com/cpl_x"
    assert paddle[-1] == ("POST", "https://sandbox-api.paddle.com/customers/ctm_1/portal-sessions",
                          {"subscription_ids": ["sub_1"]})


def test_production_uses_the_live_api(monkeypatch):
    monkeypatch.setenv("PADDLE_ENV", "production")
    assert billing.api_base() == "https://api.paddle.com"


def test_deleting_an_account_cancels_its_subscription(c, paddle):
    signup(c)
    send(c, sub_event("subscription.created", me()["id"]))
    assert c.post("/billing/delete", data={"password": PASSWORD}).status_code == 200
    assert paddle == [("POST", "https://sandbox-api.paddle.com/subscriptions/sub_1/cancel",
                       {"effective_from": "immediately"})]


def test_cancel_failing_is_logged_not_raised(paddle, monkeypatch):
    def refuse(method, url, **_):
        return httpx.Response(404, request=httpx.Request(method, url))

    monkeypatch.setattr(billing.httpx, "request", refuse)
    assert billing.cancel("sub_x") is False


def test_team_seats_invite_join_share_the_studio_and_remove(c, paddle):
    signup(c, email="owner@example.com")
    owner = users.by_email("owner@example.com")
    assert "Team" not in c.get("/billing").text            # Free has one seat
    assert c.post("/billing/team/invite", data={"email": "mate@example.com"}).status_code == 400
    catalog.refresh(users.update(owner["id"], plan="agency"))
    r = c.post("/billing/team/invite", data={"email": "Mate@Example.com"})
    assert r.status_code == 200 and "4 more" in r.text
    link = r.text.split("<code>")[1].split("</code>")[0]
    path = link.split("testserver", 1)[1].replace("&amp;", "&")
    owner_cookies = dict(c.cookies)

    # The invitee opens the link signed out, signs up, and joins.
    c.cookies.clear()
    r = c.get(path)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert signup(c, email="mate@example.com", next=path).headers["location"] == path
    assert "Join" in c.get(path).text
    token = path.split("token=")[1]
    assert c.post("/billing/join", data={"token": token}).status_code == 303
    mate = users.by_email("mate@example.com")
    assert c.get("/api/status").json()["data"] == str(data_dir(owner["id"]))   # the owner's studio
    page = c.get("/billing").text
    assert "owner@example.com" in page and "Delete my account" not in page and "data-price" not in page
    assert c.get("/billing/export").status_code == 403
    assert c.post("/billing/delete", data={"password": PASSWORD}).status_code == 403
    assert c.post("/billing/portal").status_code == 403
    assert c.post("/billing/join", data={"token": token}).status_code == 400   # used once
    mate_cookies = dict(c.cookies)

    # Someone else can't use a link meant for another address.
    c.cookies.clear()
    c.cookies.update(owner_cookies)
    r = c.post("/billing/team/invite", data={"email": "third@example.com"})
    token3 = r.text.split("token=")[1].split("</code>")[0]
    c.cookies.clear()
    signup(c, email="stranger@example.com")
    assert "another email" in c.post("/billing/join", data={"token": token3}).text

    # The owner removes the member: their own studio again.
    c.cookies.clear()
    c.cookies.update(owner_cookies)
    member = next(m for m in users.members(owner["id"]) if m["user_id"] == mate["id"])
    assert c.post("/billing/team/remove", data={"member": str(member["id"])}).status_code == 303
    c.cookies.clear()
    c.cookies.update(mate_cookies)
    assert c.get("/api/status").json()["data"] == str(data_dir(mate["id"]))
    assert "Delete my account" in c.get("/billing").text


def test_seats_end_when_the_owners_plan_drops(c, paddle):
    signup(c, email="owner@example.com")
    owner = users.update(users.by_email("owner@example.com")["id"], plan="agency")
    signup_token = users.invite(owner["id"], "mate@example.com")
    c.cookies.clear()
    signup(c, email="mate@example.com")
    mate = users.by_email("mate@example.com")
    users.accept_invite(signup_token, mate)
    assert billing.studio_user(mate)["id"] == owner["id"]
    users.update(owner["id"], plan="pro")
    assert billing.studio_user(mate)["id"] == mate["id"]
    users.update(owner["id"], plan="agency")
    users.mark_deleted(owner["id"])
    assert billing.studio_user(mate)["id"] == mate["id"] and users.members(owner["id"]) == []
