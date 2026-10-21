"""Paddle Billing for the hosted site (D234): checkout on /billing, Paddle's webhooks, the customer portal,
cancelling at account deletion, and Agency's team seats.

Paddle is the merchant of record: it charges, pays tax and sends receipts. Clipper only learns what happened
from signed webhooks and keeps the user's plan, period and credits in the gateway's database, then rewrites
plan.json (catalog.refresh). Prices are mapped by their Paddle ids from the env (`PADDLE_PRICE_*`), so the
sandbox and live accounts differ only in the env file. Nothing set: /billing says payments aren't set up yet.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import time
from datetime import UTC, datetime, timedelta

import httpx

from ..utils.logging import get_logger
from . import catalog, env, users

log = get_logger(__name__)

TOLERANCE = 300           # seconds a webhook's signature stays good (replays after that are refused)
GRACE_DAYS = 7            # a past_due plan keeps working this long, then drops to Free
CYCLES = ("monthly", "yearly")
PAID = ("starter", "pro", "agency")


# ---------- settings

def sandbox() -> bool:
    return env("PADDLE_ENV", "sandbox").lower() != "production"


def api_base() -> str:
    return "https://sandbox-api.paddle.com" if sandbox() else "https://api.paddle.com"


def ready() -> bool:
    """Checkout and webhooks need all three; prices are checked one by one."""
    return all(env(k) for k in ("PADDLE_API_KEY", "PADDLE_CLIENT_TOKEN", "PADDLE_WEBHOOK_SECRET"))


def price_id(plan: str, cycle: str) -> str:
    return env(f"PADDLE_PRICE_{plan.upper()}_{cycle.upper()}")


def pack_price(pack: str) -> str:
    return env(f"PADDLE_PRICE_PACK_{pack.upper()}")


def prices() -> dict[str, tuple[str, str, str]]:
    """Paddle price id -> ("plan", plan, cycle) or ("pack", pack, "")."""
    out = {price_id(p, c): ("plan", p, c) for p in PAID for c in CYCLES}
    out |= {pack_price(p): ("pack", p, "") for p in catalog.PACKS}
    out.pop("", None)
    return out


def user_tag(user_id: int | str) -> str:
    """Rides in the checkout's customData next to the user id. The browser sets customData, so without it
    anyone could buy a plan "for" another account and overwrite that account's subscription."""
    key = env("PADDLE_WEBHOOK_SECRET").encode()
    return hmac.new(key, f"clipper-user:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


# ---------- webhooks

def verify(header: str, body: bytes, secret: str | None = None, now: float | None = None) -> bool:
    """Paddle-Signature `ts=<unix>;h1=<hex>`: HMAC-SHA256 of `<ts>:<raw body>` with the destination's
    secret. More than one h1 may come while Paddle rotates a secret; any match will do."""
    secret = env("PADDLE_WEBHOOK_SECRET") if secret is None else secret
    if not secret or not header:
        return False
    ts, sigs = "", []
    for part in header.split(";"):
        key, _, value = part.partition("=")
        if key.strip() == "ts":
            ts = value.strip()
        elif key.strip() == "h1":
            sigs.append(value.strip())
    if not ts.isdigit() or abs((now or time.time()) - int(ts)) > TOLERANCE:
        return False
    mine = hmac.new(secret.encode(), ts.encode() + b":" + body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(mine, s) for s in sigs)


def _when(stamp: str | None) -> float | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else None
    except ValueError:
        return None


def _iso(stamp: str) -> str:
    return catalog.iso(datetime.fromisoformat(stamp.replace("Z", "+00:00")))


def _tagged(custom) -> dict | None:
    if not isinstance(custom, dict):
        return None
    uid, tag = str(custom.get("user_id") or ""), str(custom.get("tag") or "")
    if not uid.isdigit() or not hmac.compare_digest(tag, user_tag(uid)):
        return None
    user = users.get_user(int(uid))
    return user if user and not user["deleted_at"] else None


def _who(data: dict, subscription_id: str | None) -> dict | None:
    """The account an event is about: by subscription id, then the checkout's tagged user id, then the
    Paddle customer id. Deleted accounts never match (a late webhook must not bring a folder back)."""
    return (users.by_paddle(subscription_id=subscription_id) or _tagged(data.get("custom_data"))
            or users.by_paddle(customer_id=data.get("customer_id")))


def _price_ids(data: dict) -> list[tuple[str, int]]:
    out = []
    for item in data.get("items") or []:
        pid = ((item or {}).get("price") or {}).get("id") or (item or {}).get("price_id")
        if pid:
            out.append((pid, int(item.get("quantity") or 1)))
    return out


def handle(event: dict, now: float | None = None) -> list[str]:
    """Apply one verified event. Returns subscription ids to cancel at once (a full refund)."""
    kind, data = event.get("event_type") or "", event.get("data") or {}
    if kind.startswith("subscription."):
        _subscription(data, now)
    elif kind == "transaction.completed":
        _transaction(data)
    elif kind in ("adjustment.created", "adjustment.updated"):
        return _adjustment(data)
    return []


def _subscription(data: dict, now: float | None) -> None:
    sub_id = data.get("id") or ""
    user = _who(data, sub_id)
    if user is None:
        log.warning("paddle: subscription %s is for no account here", sub_id)
        return
    known = prices()
    plans = [known[p][1] for p, _ in _price_ids(data) if known.get(p, ("",))[0] == "plan"]
    if not plans:
        log.warning("paddle: subscription %s has no price Clipper knows: %s", sub_id,
                    [p for p, _ in _price_ids(data)])
        return
    at = _when(data.get("updated_at"))
    if user["paddle_subscription_id"] == sub_id and at and user["paddle_event_at"] and at < user["paddle_event_at"]:
        return   # Paddle doesn't promise order: an older state of this subscription arrived late
    now = now or time.time()
    status = data.get("status")
    fields = {"paddle_customer_id": data.get("customer_id") or user["paddle_customer_id"], "paddle_event_at": at}
    if status in ("canceled", "paused"):
        if user["paddle_subscription_id"] != sub_id:
            return   # a subscription the account no longer uses ended
        start, end = catalog.first_period(now)
        fields |= {"plan": "free", "status": "active", "paddle_subscription_id": None, "period_start": start,
                   "period_end": end, "grace_until": None}
    else:
        if user["paddle_subscription_id"] not in (None, sub_id):
            log.warning("paddle: account %d moves from subscription %s to %s", user["id"],
                        user["paddle_subscription_id"], sub_id)
        fields |= {"plan": plans[0], "paddle_subscription_id": sub_id}
        period = data.get("current_billing_period") or {}
        if period.get("starts_at") and period.get("ends_at"):
            fields |= {"period_start": _iso(period["starts_at"]), "period_end": _iso(period["ends_at"])}
        if status == "past_due":
            keep = user["status"] == "past_due" and user["grace_until"]
            grace = user["grace_until"] if keep else catalog.iso(datetime.fromtimestamp(now, UTC)
                                                                + timedelta(days=GRACE_DAYS))
            fields |= {"status": "past_due", "grace_until": grace}
        else:
            # A cancel the user scheduled: the paid plan runs to the period's end (catalog.roll ends it then,
            # if subscription.canceled is late).
            cancel = ((data.get("scheduled_change") or {}).get("action") == "cancel")
            fields |= {"status": "canceled" if cancel else "active", "grace_until": None}
    catalog.refresh(users.update(user["id"], **fields), now)
    log.info("paddle: account %d now %s (%s)", user["id"], fields.get("plan"), fields.get("status"))


def _transaction(data: dict) -> None:
    known = prices()
    items = _price_ids(data)
    unknown = [p for p, _ in items if p not in known]
    if unknown:
        log.warning("paddle: transaction %s has prices Clipper doesn't know: %s", data.get("id"), unknown)
    packs = [(known[p][1], qty) for p, qty in items if known.get(p, ("",))[0] == "pack"]
    if not packs:
        return   # a plan's payment: the subscription events carry it
    user = _who(data, data.get("subscription_id"))
    if user is None:
        log.warning("paddle: transaction %s is for no account here", data.get("id"))
        return
    for name, qty in packs:
        pack = catalog.PACKS[name]
        for _ in range(max(1, min(qty, 100))):   # one row per pack: catalog counts each pack's AI budget once
            users.add_credits(user["id"], minutes=pack.minutes, shorts=pack.shorts, source=f"pack:{name}")
    if data.get("customer_id") and not user["paddle_customer_id"]:
        user = users.update(user["id"], paddle_customer_id=data["customer_id"])
    catalog.refresh(users.get_user(user["id"]))
    log.info("paddle: account %d bought %s", user["id"], [n for n, _ in packs])


def _adjustment(data: dict) -> list[str]:
    """An approved refund is noted on the account (one refund per account, D226) for support on /admin.
    A full refund of the current subscription also ends it now, so the user isn't billed again.

    ponytail: a refunded pack's credits stay (the refund policy refunds only unused packs, so it is at most one
    pack's worth); store the transaction id with each credit row if that is ever abused.
    """
    if data.get("action") != "refund" or data.get("status") != "approved":
        return []
    sub_id = data.get("subscription_id")
    user = _who(data, sub_id)
    if user is None:
        log.warning("paddle: refund on transaction %s for no account here", data.get("transaction_id"))
        return []
    users.update(user["id"], refunded_at=time.time())
    log.info("paddle: refund given to account %d", user["id"])
    return [sub_id] if data.get("type") == "full" and sub_id and sub_id == user["paddle_subscription_id"] else []


# ---------- Paddle's API

def api(method: str, path: str, body: dict | None = None) -> dict:
    """Raises httpx.HTTPError (a refused request is an HTTPStatusError)."""
    reply = httpx.request(method, api_base() + path, json=body, timeout=15,
                          headers={"Authorization": f"Bearer {env('PADDLE_API_KEY')}"})
    reply.raise_for_status()
    return reply.json().get("data") or {}


def cancel(subscription_id: str, when: str = "immediately") -> bool:
    """Best effort, logged: a failure is for support to finish in Paddle's dashboard."""
    try:
        api("POST", f"/subscriptions/{subscription_id}/cancel", {"effective_from": when})
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("paddle: cancelling %s failed (%s)", subscription_id, type(exc).__name__)
        return False
    log.info("paddle: subscription %s canceled (%s)", subscription_id, when)
    return True


def on_account_deleted(user: dict) -> None:
    """A deleted account stops paying at once (the folder is gone, so there is nothing left to use)."""
    if user.get("paddle_subscription_id") and env("PADDLE_API_KEY"):
        cancel(user["paddle_subscription_id"])


def portal_url(user: dict) -> str:
    """Paddle's customer portal for this customer: cards, invoices, cancel. Links are short-lived."""
    body = {"subscription_ids": [user["paddle_subscription_id"]]} if user["paddle_subscription_id"] else {}
    data = api("POST", f"/customers/{user['paddle_customer_id']}/portal-sessions", body)
    return data["urls"]["general"]["overview"]


def change_plan(user: dict, plan: str, cycle: str) -> None:
    """A subscriber changes plan on the subscription they have (prorated now), never a second checkout.
    Choosing a plan while a cancel is scheduled keeps the subscription."""
    body = {"items": [{"price_id": price_id(plan, cycle), "quantity": 1}],
            "proration_billing_mode": "prorated_immediately"}
    if user["status"] == "canceled":
        body["scheduled_change"] = None
    api("PATCH", f"/subscriptions/{user['paddle_subscription_id']}", body)


# ---------- team seats (Agency)

def seats(user: dict) -> int:
    return catalog.PLANS.get(user["plan"], catalog.PLANS["free"]).seats


def team_owner(user: dict) -> dict | None:
    """The owner whose studio this user works in, while the owner's plan has seats."""
    owner = users.team_owner(user["id"])
    return owner if owner and seats(owner) > 1 else None


def studio_user(user: dict) -> dict:
    """Whose studio (data folder and process) a signed-in user's requests reach."""
    return team_owner(user) or user


# ---------- the page

def _money(x: float) -> str:
    return f"{x:g}" if x == int(x) else f"{x:.2f}"


def _js(value) -> str:
    return json.dumps(value).replace("</", "<\\/")


def head(user: dict, success_url: str) -> str:
    """Paddle.js, only on /billing. The checkout carries the user id (and its tag) and their email."""
    if not ready():
        return ""
    custom = {"user_id": str(user["id"]), "tag": user_tag(user["id"])}
    return ('<script src="https://cdn.paddle.com/paddle/v2/paddle.js"></script><script>'
            + ('Paddle.Environment.set("sandbox");' if sandbox() else "")
            + f'Paddle.Initialize({{token:{_js(env("PADDLE_CLIENT_TOKEN"))}}});'
            'document.addEventListener("click",function(e){var b=e.target.closest("[data-price]");if(!b)return;'
            'Paddle.Checkout.open({items:[{priceId:b.dataset.price,quantity:1}],'
            f'customer:{{email:{_js(user["email"])}}},customData:{_js(custom)},settings:{{successUrl:'
            f'{_js(success_url)},theme:matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}}}});}});'
            '</script>')


SECTION_CSS = ("<style>.cycle{display:flex;gap:16px;margin:0 0 16px}.plans{display:grid;gap:12px;"
               "grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}.plan{background:var(--s2);border-radius:6px;"
               "padding:14px}.plan b{display:block;font-size:17px}.plan p{margin:6px 0 12px;font-size:13px}"
               ".plan span{display:block}body:has(#yearly:checked) .monthly{display:none}"
               "body:not(:has(#yearly:checked)) .yearly{display:none}</style>")


def _plan_button(user: dict, plan: str, cycle: str) -> str:
    pid = price_id(plan, cycle)
    if not pid:
        return f'<p class="muted {cycle}">Not on sale yet.</p>'
    if user["plan"] == plan and user["paddle_subscription_id"]:
        return f'<p class="{cycle}"><b>Your plan</b></p>'
    if user["paddle_subscription_id"]:
        return (f'<form class="{cycle}" method="post" action="/billing/change"><input type="hidden" name="plan" '
                f'value="{plan}"><input type="hidden" name="cycle" value="{cycle}"><button>Change to this'
                '</button></form>')
    return f'<button class="primary {cycle}" type="button" data-price="{html.escape(pid)}">Subscribe</button>'


def section(user: dict, note: str = "") -> str:
    """Plans, packs and the portal on /billing (the owner's view)."""
    if not ready():
        return ('<section class="card" data-billing="off"><h2>Change plan</h2><p class="muted">Payments aren\'t '
                'set up yet. See <a href="/pricing">pricing</a> for what the plans will give.</p></section>')
    cards = []
    for name in PAID:
        p = catalog.PLANS[name]
        cards.append(
            f'<div class="plan"><b>{name.title()}</b><span class="monthly">${p.price:g} a month</span>'
            f'<span class="yearly">${p.price_yearly:g} a month, billed yearly</span><p class="muted">{p.minutes} '
            f'footage minutes, {p.shorts} Shorts, clips kept {p.retention_days} days'
            f'{f", {p.seats} seats" if p.seats > 1 else ""}.</p>'
            f'{_plan_button(user, name, "monthly")}{_plan_button(user, name, "yearly")}</div>')
    packs = "".join(
        f'<div class="plan"><b>{pk.title}</b><span>${_money(pk.price)}, never expires</span><p class="muted">'
        f'{"No subscription needed. No watermark on those clips, kept 30 days." if pk.name == "campaign" else "On top of your plan."}'
        f'</p>' + (f'<button type="button" data-price="{html.escape(pack_price(pk.name))}">Buy</button>'
                   if pack_price(pk.name) else '<p class="muted">Not on sale yet.</p>') + '</div>'
        for pk in catalog.PACKS.values())
    if user["paddle_subscription_id"] and user["status"] == "canceled":
        renew = (f"<p>Your plan is canceled and ends on {catalog.day(user['period_end'])}. Choose a plan to keep "
                 "it.</p>")
    elif user["paddle_subscription_id"] and user["status"] == "past_due":
        renew = ("<p class='warn'>Your last payment didn't go through. Update your card under Manage before "
                 f"{catalog.day(user['grace_until'])}, or the plan goes back to Free.</p>")
    elif user["paddle_subscription_id"]:
        renew = f"<p>Your plan renews on {catalog.day(user['period_end'])}.</p>"
    else:
        renew = ""
    manage = ('<form method="post" action="/billing/portal"><button>Manage or cancel</button></form>'
              if user["paddle_customer_id"] else "")
    msg = f'<p class="warn" role="status">{html.escape(note)}</p>' if note else ""
    return (f'{SECTION_CSS}<section class="card" data-billing="paddle"><h2>Change plan</h2>{msg}{renew}'
            '<fieldset class="cycle" style="border:0;padding:0"><legend class="muted" style="padding:0">Pay'
            '</legend><label class="check"><input type="radio" name="cycle" id="monthly" checked>Monthly</label>'
            '<label class="check"><input type="radio" name="cycle" id="yearly">Yearly (about 20% less)</label>'
            f'</fieldset><div class="plans">{"".join(cards)}</div></section>'
            f'<section class="card"><h2>Packs</h2><div class="plans">{packs}</div></section>'
            '<section class="card"><h2>Payments</h2><p>Paddle handles payments, receipts and tax. Change your card, '
            'see invoices or cancel there. If you cancel, you keep your plan until the end of the period you paid '
            f'for.</p><div class="row">{manage}<a href="/refunds">Refund policy</a></div></section>')


def team_section(user: dict, link: str = "", error: str = "") -> str:
    """Agency's seats: the owner invites by email and removes people. The owner counts as one seat."""
    total = seats(user)
    if total <= 1:
        return ""
    rows = users.members(user["id"])
    items = "".join(
        f'<tr><td>{html.escape(m["invited_email"])}</td><td>{"Joined" if m["accepted_at"] else "Invited"}</td>'
        f'<td><form method="post" action="/billing/team/remove"><input type="hidden" name="member" '
        f'value="{m["id"]}"><button class="ghost danger">Remove</button></form></td></tr>' for m in rows)
    table = (f'<div class="table"><table><tr><th>Email</th><th></th><th></th></tr>{items}</table></div>'
             if rows else "")
    shown = (f'<p role="status">Send this link to them. It works for {users.INVITE_DAYS} days, once:<br>'
             f'<code>{html.escape(link)}</code></p>' if link else "")
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    free = total - 1 - len(rows)
    invite = ('<form method="post" action="/billing/team/invite" class="row" style="align-items:end"><label '
              'style="flex:1;margin:0">Email<input type="email" name="email" required></label><button '
              'class="primary">Invite</button></form>' if free > 0 else "<p class='muted'>Every seat is taken.</p>")
    return (f'<section class="card"><h2>Team</h2><p>{total} seats: you and {total - 1} more. Everyone on the '
            f'team works in this studio. Only you see the plan, payments and account deletion.</p>{err}{shown}'
            f'{table}{invite}</section>')
