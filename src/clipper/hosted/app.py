"""The gateway's web app (D228): sign-up, sign-in, billing and admin pages, and the proxy to each user's Clipper.

Gateway-owned paths are the shared contract's list (/login /signup /logout /reset /billing /pricing /terms
/privacy /dmca /refunds /admin /paddle/* /healthz /_gw/*). Everything else, signed in, goes to the user's
own Clipper; signed out, / is the landing page and the rest asks to log in. Pages are plain HTML in
Clipper's colours (DESIGN.md), no framework: a few forms.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import os
import secrets
import shutil
import smtplib
import time
import zipfile
from collections import defaultdict, deque
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from ..utils.logging import get_logger
from . import billing, catalog, data_dir, env, proxy, root, user_dir, users
from .procs import Procs, StartFailed

log = get_logger(__name__)

COOKIE = proxy.SESSION_COOKIE
TERMS_VERSION = "2026-10-20"
SITE = Path(__file__).parent / "site"
OWNED = {"login", "signup", "logout", "reset", "billing", "pricing", "terms", "privacy", "dmca", "refunds",
         "admin", "paddle", "healthz", "_gw"}
#: What "Download my data" leaves out: caches Clipper rebuilds, and footage it downloads again (D228).
EXPORT_SKIP = {("cache",), ("work",), ("models",), ("create", "stock")}
STORED = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".m4a", ".wav", ".aac", ".jpg", ".jpeg", ".png", ".webp", ".zip"}
DISK_WARN = 0.15


# ---------- small helpers

class Limiter:
    """At most `n` tries per key in `window` seconds (login and signup, per IP and per email).

    ponytail: in memory, per gateway process; forgotten on restart. A shared store if the gateway ever
    runs as several processes.
    """

    def __init__(self, n: int = 10, window: float = 600) -> None:
        self.n, self.window = n, window
        self.hits: defaultdict[str, deque] = defaultdict(deque)

    def allow(self, *keys: str) -> bool:
        now = time.monotonic()
        if len(self.hits) > 50_000:   # keys of tries long past
            self.hits = defaultdict(deque, {k: q for k, q in self.hits.items() if q and q[-1] > now - self.window})
        for key in keys:
            q = self.hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.n:
                return False
        for key in keys:
            self.hits[key].append(now)
        return True


def _https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return request.url.scheme == "https" or proto == "https" or env("CLIPPER_PUBLIC_URL").startswith("https://")


def _public_host(request: Request) -> str:
    return (urlsplit(env("CLIPPER_PUBLIC_URL")).netloc or request.headers.get("host", "")).lower()


def _public_url(request: Request) -> str:
    return env("CLIPPER_PUBLIC_URL").rstrip("/") or str(request.base_url).rstrip("/")


def _same_site(request: Request) -> bool:
    """A gateway form post came from the gateway's own pages, not from another site."""
    src = request.headers.get("origin") or request.headers.get("referer") or ""
    return bool(src) and urlsplit(src).netloc.lower() == _public_host(request)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def _form(request: Request, limit: int = 64_000) -> dict[str, str]:
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise HTTPException(413, "too much")
    return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}


def _next(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") and "\\" not in path else "/"


def _admins() -> set[str]:
    return {users.normal_email(e) for e in env("CLIPPER_ADMINS").split(",") if e.strip()}


def _size(path: Path) -> int:
    # ponytail: walks the folder on every /admin view; a cached figure once there are many users.
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):
                total += (Path(dirpath) / f).stat().st_size
    return total


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"


def send_mail(to: str, subject: str, text: str) -> None:
    """Through SMTP_URL (smtp[s]://user:pass@host:port)."""
    url = urlsplit(env("SMTP_URL"))
    msg = EmailMessage()
    msg["From"] = env("CLIPPER_CONTACT_EMAIL") or unquote(url.username or "")
    msg["To"], msg["Subject"] = to, subject
    msg.set_content(text)
    if url.scheme == "smtps":
        server = smtplib.SMTP_SSL(url.hostname or "", url.port or 465, timeout=20)
    else:
        server = smtplib.SMTP(url.hostname or "", url.port or 587, timeout=20)
        server.ehlo()
        if server.has_extn("starttls"):
            server.starttls()
            server.ehlo()
    with server:
        if url.username:
            server.login(unquote(url.username), unquote(url.password or ""))
        server.send_message(msg)


def export_zip(user_id: int) -> Path:
    """The user's data folder as a zip, without caches and re-downloadable footage."""
    data = data_dir(user_id)
    out = root() / "tmp" / f"export-{user_id}-{secrets.token_hex(6)}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(data):
            rel = Path(dirpath).relative_to(data).parts
            dirs[:] = [d for d in dirs if (*rel, d) not in EXPORT_SKIP and d != "__pycache__"
                       and not (rel[:2] == ("create", "videos") and d == "shots")]
            for f in files:
                path = Path(dirpath) / f
                if f.endswith(".tmp"):
                    continue
                z.write(path, Path(*rel, f).as_posix(),
                        zipfile.ZIP_STORED if path.suffix.lower() in STORED else zipfile.ZIP_DEFLATED)
    return out


# ---------- pages

CSS = """
:root{--bg:#0C0E12;--s1:#13161B;--s2:#1B1F26;--s3:#262B33;--line:rgb(255 255 255/.15);--text:#ECEDEF;
--muted:#A6ABB4;--accent:#FFC229;--accent-hover:#FFD15C;--accent-fg:#17110A;--danger:#FF6464;--warn:#FF8F57;
--focus:#ECEDEF;color-scheme:dark}
@media (prefers-color-scheme:light){:root{--bg:#E8EAEE;--s1:#F4F5F7;--s2:#FFFFFF;--s3:#DCDFE4;
--line:rgb(16 19 24/.2);--text:#101318;--muted:#474D57;--accent:#F5B30F;--accent-hover:#E6A700;--danger:#C42B2B;
--warn:#B4470C;--focus:#101318;color-scheme:light}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 "Sofia Sans Variable","Sofia Sans",system-ui,sans-serif;
font-variant-numeric:tabular-nums}
header,main{max-width:640px;margin:0 auto;padding:0 16px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;min-height:64px;flex-wrap:wrap}
.wide header,.wide main{max-width:1100px}
main{padding-top:32px;padding-bottom:64px}
.brand{font-family:"Sofia Sans Condensed Variable","Sofia Sans Condensed",system-ui,sans-serif;font-weight:800;
text-transform:uppercase;font-size:21px;text-decoration:none;letter-spacing:.03em}
h1,h2{font-family:"Sofia Sans Condensed Variable","Sofia Sans Condensed","Sofia Sans",system-ui,sans-serif;margin:0}
h1{font-size:clamp(2.1rem,3.6vw,3.1rem);font-weight:800;text-transform:uppercase;line-height:.92;margin-bottom:32px}
h2{font-size:21px;font-weight:700;margin:0 0 12px}
a{color:inherit}
p{margin:0 0 14px}
.muted{color:var(--muted)}
.card{background:var(--s1);border-radius:6px;padding:20px;margin:0 0 20px}
label{display:block;margin:0 0 16px;font-weight:600;font-size:13px}
input[type=email],input[type=password],input[type=text],select{display:block;width:100%;margin-top:6px;min-height:44px;
padding:0 12px;border-radius:4px;border:1px solid var(--line);background:var(--s2);color:var(--text);font:inherit;font-size:15px}
label.check{display:flex;gap:10px;align-items:flex-start;font-weight:400;font-size:15px}
label.check input{margin:3px 0 0;width:18px;height:18px;flex:none;accent-color:var(--accent)}
button,.button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 18px;
border-radius:4px;border:0;background:var(--s3);color:var(--text);font:inherit;font-weight:700;text-decoration:none;cursor:pointer}
.primary{background:var(--accent);color:var(--accent-fg)} .primary:hover{background:var(--accent-hover)}
.ghost{background:none;padding:0 8px} .danger{color:var(--danger)}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.error{color:var(--danger);font-weight:600} .warn{color:var(--warn);font-weight:600}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.table{overflow-x:auto} table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:13px;color:var(--muted);font-weight:600}
code{font-family:"JetBrains Mono",ui-monospace,monospace;font-size:13px;word-break:break-all}
"""


def page(title: str, body: str, *, user: dict | None = None, status: int = 200, wide: bool = False,
         head: str = "") -> HTMLResponse:
    if user:
        nav = ('<nav class="row"><a class="button ghost" href="/">Studio</a><a class="button ghost" href="/billing">'
               'Plan and account</a><form method="post" action="/logout"><button class="ghost">Log out</button>'
               '</form></nav>')
    else:
        nav = ('<nav class="row"><a class="button ghost" href="/pricing">Pricing</a><a class="button ghost" '
               'href="/login">Log in</a><a class="button" href="/signup">Sign up</a></nav>')
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width,initial-scale=1"><title>{html.escape(title)} · Clipper</title>{head}'
           f'<style>{CSS}</style></head><body class="{"wide" if wide else ""}"><header><a class="brand" href="/">'
           f'Clipper</a>{nav}</header><main>{body}</main></body></html>')
    return HTMLResponse(doc, status_code=status, headers={
        # same-origin, not no-referrer: under no-referrer browsers send "Origin: null" with the page's own
        # forms, and _same_site() refuses them. Other sites still never see a reset link's address.
        "Referrer-Policy": "same-origin", "X-Frame-Options": "DENY", "Cache-Control": "no-store"})


FALLBACK = {
    "index": ("Clipper", "<h1>Long videos in, clips out</h1><p>Clipper finds the best moments in your long videos "
              "and turns them into vertical clips, ready to post.</p><p class='row'><a class='button primary' "
              "href='/signup'>Start free</a><a class='button' href='/login'>Log in</a></p>"),
    "terms": ("Terms", "<h1>Terms</h1><p>The full terms are being written. Questions: {{contact_email}}.</p>"),
    "privacy": ("Privacy", "<h1>Privacy</h1><p>The full privacy policy is being written. "
                "Questions: {{contact_email}}.</p>"),
    "dmca": ("Copyright", "<h1>Copyright notices</h1><p>To report content that infringes your copyright, "
             "write to {{contact_email}}.</p>"),
    "refunds": ("Refunds", "<h1>Refunds</h1><p>For a refund, write to {{contact_email}}.</p>"),
}


def _pricing_fallback() -> str:
    rows = "".join(
        f"<tr><td>{p.name.title()}</td><td>${p.price:g}</td><td>{p.minutes}</td>"
        f"<td>{p.shorts}{' once' if p.shorts_once else ''}</td><td>{p.retention_days} days</td></tr>"
        for p in catalog.PLANS.values())
    packs = "".join(f"<li>{p.title}: ${p.price:g}</li>" for p in catalog.PACKS.values())
    return ("<h1>Pricing</h1><div class='table'><table><tr><th>Plan</th><th>A month</th><th>Footage minutes</th>"
            f"<th>Shorts</th><th>Clips kept</th></tr>{rows}</table></div><h2 style='margin-top:32px'>Packs</h2>"
            f"<ul>{packs}</ul><p class='muted'>Packs never expire. Every tool is on every plan.</p>")


def site_page(name: str, user: dict | None = None) -> HTMLResponse:
    """`site/<name>.html` (the landing and legal pages) with the operator's details filled in, or a short
    built-in page while that file isn't written."""
    values = {"contact_email": env("CLIPPER_CONTACT_EMAIL"), "public_url": env("CLIPPER_PUBLIC_URL"),
              "operator_name": env("CLIPPER_OPERATOR_NAME")}
    path = SITE / f"{name}.html"
    if name == "pricing" and not path.is_file():   # the landing page carries the prices (D226)
        path = SITE / "index.html"
    if path.is_file():
        text, title = path.read_text(encoding="utf-8"), None
    else:
        title, text = FALLBACK.get(name) or ("Pricing", _pricing_fallback())
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", html.escape(value))
    if title is None:
        return HTMLResponse(text, headers={"Referrer-Policy": "same-origin"})
    return page(title, text, user=user)


def starting(request: Request) -> Response:
    headers = {"Retry-After": "3", "Cache-Control": "no-store"}
    if "text/html" in request.headers.get("accept", ""):
        reply = page("Starting", "<h1>Starting your studio</h1><p>This takes a few seconds. The page opens by "
                     "itself.</p>", status=503, head='<meta http-equiv="refresh" content="3">')
        reply.headers.update(headers)
        return reply
    return JSONResponse({"detail": "Your studio is starting. Try again in a few seconds."}, 503, headers=headers)


def _signup_form(error: str = "", email: str = "", nxt: str = "/") -> str:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return (f'<h1>Sign up</h1>{err}<form class="card" method="post" action="/signup">'
            f'<input type="hidden" name="next" value="{html.escape(nxt)}">'
            f'<label>Email<input type="email" name="email" required autocomplete="email" value="{html.escape(email)}">'
            f'</label><label>Password (at least {users.MIN_PASSWORD} characters)<input type="password" name="password" '
            f'required minlength="{users.MIN_PASSWORD}" autocomplete="new-password"></label>'
            '<label class="check"><input type="checkbox" name="adult" value="1" required><span>I am 18 or older'
            '</span></label><label class="check"><input type="checkbox" name="terms" value="1" required><span>I '
            'accept the <a href="/terms" target="_blank">Terms</a> and the <a href="/privacy" target="_blank">'
            'Privacy policy</a></span></label><button class="primary">Create my account</button></form>'
            '<p class="muted">Have an account? <a href="/login">Log in</a></p>')


def _login_form(error: str = "", email: str = "", nxt: str = "/") -> str:
    err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    return (f'<h1>Log in</h1>{err}<form class="card" method="post" action="/login">'
            f'<input type="hidden" name="next" value="{html.escape(nxt)}">'
            f'<label>Email<input type="email" name="email" required autocomplete="email" value="{html.escape(email)}">'
            '</label><label>Password<input type="password" name="password" required autocomplete="current-password">'
            '</label><div class="row"><button class="primary">Log in</button><a href="/reset">Forgot your password?</a>'
            f'</div></form><p class="muted">No account yet? <a href="/signup?next={html.escape(quote(nxt))}">'
            'Sign up</a></p>')


# ---------- the app

def create_app(procs: Procs | None = None) -> FastAPI:
    pending: dict[int, tuple[str, str, float]] = {}   # reset links for /admin when no SMTP: user -> (email, link, until)
    attempts = Limiter()

    def prepare(user_id: int) -> None:
        user = users.get_user(user_id)
        if user:
            catalog.refresh(user)

    procs = procs or Procs(prepare=prepare)
    http = proxy.client()

    async def tend() -> None:
        rolled = swept = 0.0
        sweep: asyncio.Task | None = None
        while True:
            await asyncio.sleep(60)
            try:
                await procs.reap()
                if time.time() - rolled > 3600:   # a month can end while a long job keeps a process up
                    rolled = time.time()
                    for user_id in list(procs.procs):
                        prepare(user_id)
                # What a stopped studio would delete each day (D231, D237), beside the reaping, not in its way.
                if time.time() - swept > 86400 and (sweep is None or sweep.done()):
                    swept = time.time()
                    sweep = asyncio.create_task(procs.daily([u["id"] for u in users.all_users()]))
            except Exception:
                log.exception("gateway: looking after the studios failed")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(tend())
        yield
        task.cancel()
        procs.stop_all()
        await http.aclose()

    app = FastAPI(title="Clipper gateway", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.procs = procs
    app.state.pending_resets = pending
    app.mount("/_gw/site", StaticFiles(directory=SITE, check_dir=False), name="site")

    def current(request: Request) -> dict | None:
        return users.session_user(request.cookies.get(COOKIE, ""))

    def sign_in(request: Request, user_id: int, to: str = "/") -> RedirectResponse:
        """A new session at every login (the old one ends), so a session id from before can't be reused."""
        old = request.cookies.get(COOKIE)
        if old:
            users.end_session(old)
        reply = RedirectResponse(to, status_code=303)
        reply.set_cookie(COOKIE, users.new_session(user_id), max_age=users.SESSION_DAYS * 86400, httponly=True,
                         samesite="lax", secure=_https(request), path="/")
        return reply

    def check_post(request: Request) -> None:
        if not _same_site(request):
            raise HTTPException(403, "This form has to be sent from this site.")

    def signed_in(request: Request) -> dict:
        user = current(request)
        if user is None:
            raise HTTPException(303, headers={"Location": "/login?next=" + quote(request.url.path)})
        return user

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/signup")
    def signup_page(request: Request, next: str = "/") -> Response:
        if current(request):
            return RedirectResponse(_next(next), 303)
        return page("Sign up", _signup_form(nxt=_next(next)))

    @app.post("/signup")
    async def signup(request: Request) -> Response:
        check_post(request)
        form = await _form(request)
        email, nxt = users.normal_email(form.get("email", "")), _next(form.get("next", "/"))
        if not attempts.allow(f"signup-ip:{_ip(request)}", f"signup:{email}"):
            return page("Sign up", _signup_form("Too many tries. Wait ten minutes and try again.", email, nxt),
                        status=429)
        if not form.get("adult") or not form.get("terms"):
            return page("Sign up", _signup_form("Confirm that you are 18 or older and accept the Terms.", email,
                                                nxt), status=400)
        try:
            user = users.create_user(email, form.get("password", ""), age_confirmed=True,
                                     terms_version=TERMS_VERSION, period=catalog.first_period())
        except users.AccountError as exc:
            return page("Sign up", _signup_form(str(exc), email, nxt), status=400)
        catalog.refresh(user)
        log.info("gateway: new account %d", user["id"])
        return sign_in(request, user["id"], nxt)

    @app.get("/login")
    def login_page(request: Request, next: str = "/") -> Response:
        if current(request):
            return RedirectResponse(_next(next), 303)
        return page("Log in", _login_form(nxt=_next(next)))

    @app.post("/login")
    async def login(request: Request) -> Response:
        check_post(request)
        form = await _form(request)
        email, nxt = users.normal_email(form.get("email", "")), _next(form.get("next", "/"))
        if not attempts.allow(f"login-ip:{_ip(request)}", f"login:{email}"):
            return page("Log in", _login_form("Too many tries. Wait ten minutes and try again.", email, nxt),
                        status=429)
        user = users.authenticate(email, form.get("password", ""))
        if user is None:
            return page("Log in", _login_form("That email and password don't match an account.", email, nxt),
                        status=401)
        return sign_in(request, user["id"], nxt)

    @app.post("/logout")
    def logout(request: Request) -> Response:
        check_post(request)
        token = request.cookies.get(COOKIE)
        if token:
            users.end_session(token)
        reply = RedirectResponse("/", status_code=303)
        reply.delete_cookie(COOKIE, path="/")
        return reply

    def reset_form(token: str, error: str = "") -> str:
        err = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        return (f'<h1>New password</h1>{err}<form class="card" method="post" action="/reset">'
                f'<input type="hidden" name="token" value="{html.escape(token)}"><label>New password (at least '
                f'{users.MIN_PASSWORD} characters)<input type="password" name="password" required '
                f'minlength="{users.MIN_PASSWORD}" autocomplete="new-password"></label>'
                '<button class="primary">Save and log in</button></form>')

    @app.get("/reset")
    def reset_page(token: str = "") -> Response:
        if token:
            if users.reset_user(token) is None:
                return page("Reset", "<h1>Link expired</h1><p>This link has expired or was already used. "
                            "<a href='/reset'>Ask for a new one</a>.</p>", status=400)
            return page("New password", reset_form(token))
        return page("Reset", '<h1>Forgot your password</h1><form class="card" method="post" action="/reset">'
                    '<label>Email<input type="email" name="email" required autocomplete="email"></label>'
                    '<button class="primary">Send me a link</button></form>')

    @app.post("/reset")
    async def reset(request: Request) -> Response:
        check_post(request)
        form = await _form(request)
        if form.get("token"):
            try:
                user = users.use_reset(form["token"], form.get("password", ""))
            except users.AccountError as exc:
                return page("New password", reset_form(form["token"], str(exc)), status=400)
            if user is None:
                return page("Reset", "<h1>Link expired</h1><p>This link has expired or was already used. "
                            "<a href='/reset'>Ask for a new one</a>.</p>", status=400)
            pending.pop(user["id"], None)
            return sign_in(request, user["id"])
        email = users.normal_email(form.get("email", ""))
        sent = page("Reset", "<h1>Check your email</h1><p>If there is an account for that address, a link to "
                    "choose a new password is on its way. It works for an hour.</p>")
        if not attempts.allow(f"reset-ip:{_ip(request)}", f"reset:{email}"):
            return sent
        user = users.by_email(email)
        if user is None:
            return sent
        link = f"{_public_url(request)}/reset?token={users.new_reset(user['id'])}"
        if env("SMTP_URL"):
            try:
                await asyncio.to_thread(send_mail, email, "Your Clipper password",
                                        "Someone asked to reset the password of your Clipper account.\n\n"
                                        f"Open this link within an hour to choose a new one:\n{link}\n\n"
                                        "If it wasn't you, ignore this email.")
                return sent
            except (OSError, smtplib.SMTPException) as exc:
                log.warning("gateway: reset email for account %d failed (%s)", user["id"], type(exc).__name__)
        pending[user["id"]] = (email, link, time.time() + users.RESET_MINUTES * 60)
        return sent

    def owner_only(request: Request) -> dict:
        """Billing, export and deletion are the team owner's (a member works in the owner's studio)."""
        user = signed_in(request)
        if billing.team_owner(user):
            raise HTTPException(403, "Only the team's owner can do this.")
        return user

    def member_page(user: dict, owner: dict) -> Response:
        return page("Plan and account", (
            f'<h1>Plan and account</h1><section class="card"><h2>Team</h2><p>You work in the studio of '
            f'{html.escape(owner["email"])}, on their {owner["plan"].title()} plan. They manage the plan and '
            'payments.</p><form method="post" action="/billing/team/leave"><button>Leave the team</button></form>'
            '<p class="muted">After leaving you have your own studio again, and can delete your account there.</p>'
            '</section>'), user=user)

    def billing_page(request: Request, user: dict, *, note: str = "", link: str = "", team_error: str = "",
                     status: int = 200) -> Response:
        owner = billing.team_owner(user)
        if owner:
            return member_page(user, owner)
        user = catalog.refresh(user)   # a canceled or unpaid plan that has run out shows as Free
        if request.query_params.get("paid") and not note:
            note = "Thanks. Your plan or pack shows here within a minute; reload the page to see it."
        doc = catalog.plan_json(user)
        lim, have = doc["limits"], catalog.left(user, doc)
        credit = have["credits"]
        badge = {"past_due": " <span class='warn'>(payment overdue)</span>",
                  "canceled": " <span class='muted'>(canceled)</span>"}.get(doc["status"], "")
        mark = ("No watermark" if lim["watermark_after"] is None
                else f"The first {lim['watermark_after']} clips without a watermark, then a small one")
        extra = (f"<p>Credits left from packs: {credit['minutes']} minutes, {credit['shorts']} Shorts. They never "
                 "expire.</p>" if credit["minutes"] or credit["shorts"] else "")
        body = (f'<h1>Plan and account</h1><section class="card"><h2>{doc["plan"].title()} plan{badge}</h2>'
                f'<p>This month: {catalog.day(doc["period_start"])} to {catalog.day(doc["period_end"])}.</p>'
                f'<p>{lim["minutes"]} footage minutes and {lim["shorts"]} Create Short'
                f'{"s" if lim["shorts"] != 1 else ""}{" (once)" if lim["shorts_once"] else ""}. '
                f'Clips kept {lim["retention_days"]} days. {mark}.</p>'
                f'<p>Used: {have["minutes_used"]} of {lim["minutes"]} minutes, {have["shorts_used"]} of '
                f'{lim["shorts"]} Short{"s" if lim["shorts"] != 1 else ""}'
                f'{"" if lim["shorts_once"] else " this month"}.</p>{extra}</section>'
                f'{billing.section(user, note)}{billing.team_section(user, link, team_error)}'
                '<section class="card"><h2>Your data</h2><p>Everything in your studio as a zip file, without the '
                'caches Clipper can make again.</p><a class="button" href="/billing/export">Download my data</a>'
                '</section><section class="card"><h2>Delete my account</h2><p>This stops your studio and deletes '
                'all your videos, clips and settings for good. It cannot be undone.</p>'
                '<form method="post" action="/billing/delete"><label>Your password<input type="password" '
                'name="password" required autocomplete="current-password"></label>'
                '<button class="danger">Delete my account</button></form></section>')
        return page("Plan and account", body, user=user, status=status,
                    head=billing.head(user, _public_url(request) + "/billing?paid=1"))

    @app.get("/billing")
    def billing_view(request: Request) -> Response:
        return billing_page(request, signed_in(request))

    def subscriber(request: Request) -> dict:
        user = owner_only(request)
        if not billing.ready():
            raise HTTPException(400, "Payments aren't set up yet.")
        return user

    @app.post("/billing/change")
    async def change_plan(request: Request) -> Response:
        check_post(request)
        user = subscriber(request)
        form = await _form(request)
        plan, cycle = form.get("plan", ""), form.get("cycle", "")
        if plan not in billing.PAID or cycle not in billing.CYCLES or not billing.price_id(plan, cycle) \
                or not user["paddle_subscription_id"]:
            raise HTTPException(400, "unknown plan")
        try:
            await asyncio.to_thread(billing.change_plan, user, plan, cycle)
        except httpx.HTTPError as exc:
            log.warning("gateway: account %d: plan change refused (%s)", user["id"], type(exc).__name__)
            return billing_page(request, user, status=502, note="Paddle didn't take the change. If your last "
                                "payment failed, update your card under Manage first, then try again.")
        return billing_page(request, user, note=f"Changing to {plan.title()}. It shows here within a minute.")

    @app.post("/billing/portal")
    async def portal(request: Request) -> Response:
        check_post(request)
        user = subscriber(request)
        if not user["paddle_customer_id"]:
            return RedirectResponse("/billing", 303)
        try:
            url = await asyncio.to_thread(billing.portal_url, user)
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            log.warning("gateway: account %d: portal didn't open (%s)", user["id"], type(exc).__name__)
            return billing_page(request, user, status=502, note="Paddle's page didn't open. Try again in a minute.")
        return RedirectResponse(url, 303)

    @app.post("/billing/team/invite")
    async def team_invite(request: Request) -> Response:
        check_post(request)
        user = owner_only(request)
        form = await _form(request)
        email = users.normal_email(form.get("email", ""))
        if billing.seats(user) - 1 - len(users.members(user["id"])) <= 0:
            return billing_page(request, user, team_error="Every seat is taken.", status=400)
        if not attempts.allow(f"invite:{user['id']}"):
            return billing_page(request, user, team_error="Too many invites. Wait ten minutes.", status=429)
        try:
            token = users.invite(user["id"], email)
        except users.AccountError as exc:
            return billing_page(request, user, team_error=str(exc), status=400)
        link = f"{_public_url(request)}/billing/join?token={token}"
        if env("SMTP_URL"):
            try:
                await asyncio.to_thread(send_mail, email, "You're invited to a Clipper team",
                                        f"{user['email']} invited you to work in their Clipper studio.\n\n"
                                        f"Open this link within {users.INVITE_DAYS} days to join:\n{link}\n\n"
                                        "If you don't know them, ignore this email.")
            except (OSError, smtplib.SMTPException) as exc:
                log.warning("gateway: invite email for account %d failed (%s)", user["id"], type(exc).__name__)
        log.info("gateway: account %d invited a team member", user["id"])
        return billing_page(request, user, link=link)

    @app.post("/billing/team/remove")
    async def team_remove(request: Request) -> Response:
        check_post(request)
        user = owner_only(request)
        form = await _form(request)
        member = form.get("member", "")
        if member.isdigit():
            users.remove_member(user["id"], int(member))
        return RedirectResponse("/billing", 303)

    @app.post("/billing/team/leave")
    def team_leave(request: Request) -> Response:
        check_post(request)
        users.leave_team(signed_in(request)["id"])
        return RedirectResponse("/billing", 303)

    @app.get("/billing/join")
    def join_page(request: Request, token: str = "") -> Response:
        user = current(request)
        if user is None:
            return RedirectResponse("/login?next=" + quote(f"/billing/join?token={token}"), 303)
        invite = users.invite_owner(token) if token else None
        if invite is None:
            return page("Team", "<h1>Invite expired</h1><p>This invite has expired or was already used. Ask "
                        "for a new one.</p>", user=user, status=400)
        owner = users.get_user(invite["owner_id"])
        return page("Team", (
            f'<h1>Join a team</h1><section class="card"><p>{html.escape(owner["email"])} invited '
            f'{html.escape(invite["invited_email"])} to work in their studio. Once you join, Clipper opens their '
            'studio for you instead of your own.</p><form method="post" action="/billing/join"><input type="hidden" '
            f'name="token" value="{html.escape(token)}"><button class="primary">Join</button></form></section>'),
            user=user)

    @app.post("/billing/join")
    async def join(request: Request) -> Response:
        check_post(request)
        user = signed_in(request)
        form = await _form(request)
        try:
            owner = users.accept_invite(form.get("token", ""), user)
        except users.AccountError as exc:
            return page("Team", f"<h1>Not joined</h1><p class='error'>{html.escape(str(exc))}</p>", user=user,
                        status=400)
        log.info("gateway: account %d joined the team of account %d", user["id"], owner["id"])
        return RedirectResponse("/", 303)

    @app.post("/paddle/webhook")
    async def paddle_webhook(request: Request) -> Response:
        """Signed by Paddle (no Origin, no cookie). Retries of an event are skipped by its id; anything Clipper
        can't place (an unknown price or account) is logged and still answered 200, or Paddle retries for days."""
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 2_000_000:
                raise HTTPException(413, "too much")
        if not billing.verify(request.headers.get("paddle-signature", ""), body):
            log.warning("gateway: a Paddle webhook with a bad or old signature was refused")
            return JSONResponse({"detail": "bad signature"}, status_code=401)
        try:
            event = json.loads(body)
            event_id = str(event["event_id"])
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"detail": "not an event"}, status_code=400)
        if not users.first_seen_event(event_id):
            return JSONResponse({"ok": True, "seen": True})
        try:
            cancel = billing.handle(event)
        except Exception:
            users.forget_event(event_id)   # let Paddle's retry through
            log.exception("gateway: Paddle event %s (%s) failed", event_id, event.get("event_type"))
            return JSONResponse({"detail": "failed"}, status_code=500)
        task = BackgroundTask(lambda: [billing.cancel(s) for s in cancel]) if cancel else None
        return JSONResponse({"ok": True}, background=task)

    @app.get("/billing/export")
    async def export(request: Request) -> Response:
        user = owner_only(request)
        path = await asyncio.to_thread(export_zip, user["id"])
        return FileResponse(path, media_type="application/zip", filename="clipper-data.zip",
                            background=BackgroundTask(path.unlink, missing_ok=True))

    @app.post("/billing/delete")
    async def delete_account(request: Request) -> Response:
        check_post(request)
        user = owner_only(request)
        form = await _form(request)
        if not users.check_password(form.get("password", ""), user["password"]):
            return page("Plan and account", "<h1>Not deleted</h1><p class='error'>That password is wrong. "
                        "<a href='/billing'>Back</a></p>", user=user, status=401)
        await procs.stop(user["id"])
        await asyncio.to_thread(billing.on_account_deleted, user)
        folder = user_dir(user["id"])
        await asyncio.to_thread(shutil.rmtree, folder, True)
        if folder.exists():
            log.warning("gateway: account %d deleted but some of its files are still there", user["id"])
        users.mark_deleted(user["id"])
        pending.pop(user["id"], None)
        log.info("gateway: account %d deleted", user["id"])
        reply = page("Deleted", "<h1>Account deleted</h1><p>Your account and everything in it are gone.</p>")
        reply.delete_cookie(COOKIE, path="/")
        return reply

    def admin_user(request: Request) -> dict:
        user = current(request)
        if user is None or user["email"] not in _admins():
            raise HTTPException(404)
        return user

    @app.get("/admin")
    def admin(request: Request) -> Response:
        me = admin_user(request)
        disk = shutil.disk_usage(root() if root().exists() else Path.cwd())
        free = disk.free / disk.total
        warn = f" <span class='warn'>Under {DISK_WARN:.0%} free.</span>" if free < DISK_WARN else ""
        rows = []
        for u in users.all_users():
            seen = datetime.fromtimestamp(u["last_seen"]).strftime("%Y-%m-%d %H:%M") if u["last_seen"] else ""
            folder = user_dir(u["id"])
            options = "".join(f"<option{' selected' if p == u['plan'] else ''}>{p}</option>" for p in catalog.PLANS)
            rows.append(
                f"<tr><td>{u['id']}</td><td>{html.escape(u['email'])}</td><td>{u['plan']}</td><td>{u['status']}</td>"
                f"<td>{seen}</td><td>{_gb(_size(folder)) if folder.exists() else ''}</td>"
                f"<td>{datetime.fromtimestamp(u['refunded_at']).strftime('%Y-%m-%d') if u['refunded_at'] else ''}</td>"
                f"<td>{procs.state(u['id'])}</td><td><form class='row' method='post' action='/admin/plan'>"
                f"<input type='hidden' name='user' value='{u['id']}'><select name='plan' aria-label='Plan'>"
                f"{options}</select>"
                "<button>Set</button></form></td></tr>")
        now = time.time()
        for uid in [k for k, v in pending.items() if v[2] < now]:
            del pending[uid]
        links = "".join(f"<tr><td>{html.escape(e)}</td><td><code>{html.escape(link)}</code></td>"
                        f"<td>{datetime.fromtimestamp(until).strftime('%H:%M')}</td></tr>"
                        for e, link, until in pending.values())
        body = (f"<h1>Admin</h1><p>Disk: {_gb(disk.free)} free of {_gb(disk.total)} ({free:.0%}).{warn}</p>"
                "<h2>Accounts</h2><div class='table'><table><tr><th>Id</th><th>Email</th><th>Plan</th><th>Status</th>"
                "<th>Last seen</th><th>Data</th><th>Refund given</th><th>Studio</th><th>Set plan by hand</th></tr>"
                f"{''.join(rows)}</table></div>"
                "<h2 style='margin-top:32px'>Password reset links not emailed</h2>"
                + (f"<div class='table'><table><tr><th>Email</th><th>Link</th><th>Until</th></tr>{links}</table></div>"
                   if links else "<p class='muted'>None.</p>"))
        return page("Admin", body, user=me, wide=True)

    @app.post("/admin/plan")
    async def admin_plan(request: Request) -> Response:
        check_post(request)
        admin_user(request)
        form = await _form(request)
        plan = form.get("plan", "")
        target = users.get_user(int(form.get("user") or 0))
        if plan not in catalog.PLANS or target is None or target["deleted_at"]:
            raise HTTPException(400, "unknown plan or account")
        catalog.refresh(users.update(target["id"], plan=plan, status="active"))
        log.info("gateway: account %d set to %s by hand", target["id"], plan)
        return RedirectResponse("/admin", status_code=303)

    def site_route(name: str):
        def route(request: Request) -> Response:
            return site_page(name, current(request))
        return route

    for name in ("pricing", "terms", "privacy", "dmca", "refunds"):
        app.add_api_route(f"/{name}", site_route(name), methods=["GET"], include_in_schema=False)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                   include_in_schema=False)
    async def studio(request: Request, path: str) -> Response:
        if path.split("/", 1)[0] in OWNED:
            raise HTTPException(404)
        user = current(request)
        if user is None:
            if path == "":
                return site_page("index")
            if path.startswith("api/") or request.method != "GET":
                return JSONResponse({"detail": "Log in first."}, status_code=401)
            return RedirectResponse("/login?next=" + quote("/" + path), status_code=303)
        uid = billing.studio_user(user)["id"]   # a team member works in the owner's studio (D234)
        if path == "api/events":
            # The page's live stream neither wakes a stopped studio nor keeps one awake: an open tab
            # reconnects forever. 204 tells the browser to stop; the page tries again later.
            p = procs.running(uid)
            if p is None:
                return Response(status_code=204)
            done = lambda: None  # noqa: E731
        else:
            try:
                p = await procs.get(uid)
            except StartFailed as exc:
                log.warning("gateway: account %d: studio didn't start (%s)", uid, exc)
                return starting(request)
            p.inflight += 1

            def done() -> None:
                p.inflight -= 1
                p.last_used = time.time()
        try:
            return await proxy.forward(request, http, p.base, p.token, done=done)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return starting(request)
        except httpx.HTTPError as exc:
            log.warning("gateway: account %d: proxy failed (%s)", uid, type(exc).__name__)
            return JSONResponse({"detail": "Your studio didn't answer. Try again."}, status_code=502)

    return app
