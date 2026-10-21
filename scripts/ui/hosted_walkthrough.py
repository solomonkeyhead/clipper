"""The page as the hosted site shows it (D238), clicked through and checked, at 1400 px and 375 px, dark and light.

Run scripts/ui/serve.py <empty data dir> in the background as the gateway starts a user's Clipper:
CLIPPER_HOSTED=1, CLIPPER_TOKEN=<any> and CLIPPER_UI_PORT=8798. Then
python scripts/ui/hosted_walkthrough.py <folder for screenshots> [token]. The gateway's parts are faked here with
Playwright routes: /api/usage (until the server has it), a plan limit's 402, /billing and /logout."""
import glob
import sys

from playwright.sync_api import expect, sync_playwright

OUT = sys.argv[1]
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "e4-test-token"
BASE = "http://127.0.0.1:8798"
USAGE = {"hosted": True, "plan": "free", "status": "active", "period_end": "2026-11-20T12:00:00Z",
         "minutes": {"used": 42, "limit": 60, "credits_left": 0},
         "shorts": {"used": 1, "limit": 1, "credits_left": 0, "once": True},
         "redraws_per_short": 3, "retention_days": 7, "watermark_after": 3, "clips_this_period": 2,
         "ai": {"spent_usd": 0.41, "budget_usd": 5.5}, "billing_url": "/billing", "logout_url": "/logout"}
LIMIT = "Your free Short is used. A plan, or a pack of 5 Shorts, gives you more."


def sideways(page):
    """Nothing wider than the window: the page never scrolls sideways."""
    over = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert over <= 0, f"the page scrolls {over}px sideways at {page.viewport_size['width']}px"


with sync_playwright() as p:
    exe = next(glob.iglob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"), None)
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")
    errors, posts = [], []

    def context(width, height, scheme, rated=None):
        ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme=scheme, locale="en-US",
                            extra_http_headers={"Authorization": f"Bearer {TOKEN}"})   # the gateway's header
        ctx.route("**/api/usage", lambda r: r.fulfill(json=USAGE))
        ctx.route("**/billing", lambda r: r.fulfill(body="<h1>Billing page</h1>", content_type="text/html"))
        ctx.route("**/logout", lambda r: (posts.append(r.request.method),
                                          r.fulfill(body="<h1>Logged out</h1>", content_type="text/html")))
        if rated is not None:   # the learning report with this many rated clips
            def learning(route):
                body = route.fetch().json()
                route.fulfill(json={**body, "rated": rated})
            ctx.route("**/api/learning", learning)
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        return ctx, page

    # --- 1400 px, dark ---
    ctx, page = context(1400, 1000, "dark")
    page.goto(f"{BASE}/")
    side = page.locator("aside")
    # 1: the usage line in the sidebar, its billing link and log out
    expect(side.get_by_text("Free plan")).to_be_visible()
    expect(side.get_by_text("resets Nov 20")).to_be_visible()
    expect(side.get_by_text("42 of 60 min")).to_be_visible()
    expect(side.get_by_text("1 of 1 free Short")).to_be_visible()
    assert side.get_by_role("link", name="Plan and billing").get_attribute("href") == "/billing"
    print("1: usage in the sidebar: Free plan, resets Nov 20, 42 of 60 min, 1 of 1 free Short")
    # 4, 5: Create says Beta; Learning is hidden with fewer than 30 rated clips
    expect(side.get_by_role("link", name="Create Beta")).to_be_visible()
    assert side.get_by_role("link", name="Learning").count() == 0, "Learning shows with under 30 rated clips"
    # 3: no AI pill, no AI key step on the Dashboard
    assert page.get_by_role("link", name="Add an AI key").count() == 0
    assert page.get_by_text("Add your free Gemini key").count() == 0, "the AI key step is on the hosted Dashboard"
    sideways(page)
    page.screenshot(path=f"{OUT}/h1_dashboard_1400_dark.png")
    side.screenshot(path=f"{OUT}/h1b_sidebar_dark.png")
    side.get_by_role("link", name="Plan and billing").click()
    page.wait_for_url("**/billing")
    expect(page.get_by_text("Billing page")).to_be_visible()   # a full page load to the gateway's page
    page.go_back()
    side.get_by_role("button", name="Log out").click()
    page.wait_for_url("**/logout")
    assert posts == ["POST"], f"log out sent {posts}"
    print("1: Plan and billing loads /billing; Log out POSTs /logout")
    page.go_back()

    # 4: Beta beside Create's title
    page.goto(f"{BASE}/create")
    page.wait_for_selector("text=Make a Short")
    expect(page.locator("main").get_by_text("Beta", exact=True)).to_be_visible()
    assert page.get_by_text("Written and drawn by").count() == 0, "the AI strip names the site's keys"
    print("4: Create says Beta in the nav and the title")
    # 2: a plan limit: one toast with See plans, and the button stops
    page.route("**/api/create/topics/*/script", lambda r: r.fulfill(
        status=402, json={"detail": {"code": "plan_limit", "message": LIMIT, "billing_url": "/billing"}}))
    write = page.get_by_role("button", name="Write it: Why does a spoon flip your reflection?")
    write.click()
    toast = page.locator("[data-sonner-toast]", has_text=LIMIT)
    expect(toast).to_have_count(1)
    expect(toast.get_by_role("button", name="See plans")).to_be_visible()
    page.wait_for_timeout(600)
    expect(page.locator("[data-sonner-toast]", has_text="Couldn't write it")).to_have_count(0)   # not twice
    expect(write).to_have_text("Write it")
    assert write.get_attribute("aria-busy") is None and write.is_enabled(), "the button kept spinning after a 402"
    page.mouse.move(5, 500)
    page.screenshot(path=f"{OUT}/h2_plan_limit_1400_dark.png")
    toast.get_by_role("button", name="See plans").click()
    page.wait_for_url("**/billing")
    print("2: a 402 shows one toast with See plans (opens /billing); Write it stops")
    page.go_back()
    page.wait_for_selector("text=Make a Short")
    # 3: channel settings without picture paths
    page.get_by_role("button", name="Channel settings").click()
    expect(page.get_by_text("Board colours")).to_be_visible()
    for gone in ("Character picture", "Reaction pictures (one per line)", "Presenting picture", "Watermark"):
        assert page.get_by_text(gone, exact=True).count() == 0, f"{gone!r} is in the hosted channel settings"
    page.get_by_text("Board colours").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/h3_channel_1400_dark.png")
    print("3: channel settings: no picture paths")
    # 3: Settings without the AI, the libraries, This computer, the System check
    page.goto(f"{BASE}/settings")
    expect(page.get_by_role("heading", name="Clipping")).to_be_visible()
    for gone in ("Which AI", "Gemini key", "Footage and sound libraries for Create", "This computer", "System check"):
        assert page.get_by_text(gone, exact=True).count() == 0, f"{gone!r} is in the hosted Settings"
    heads = [h.lower() for h in page.locator("main h2").all_inner_texts()]
    assert heads == ["clipping", "the app"], heads
    sideways(page)
    page.screenshot(path=f"{OUT}/h3_settings_1400_dark.png", full_page=True)
    print("3: Settings: Clipping and The app only")
    page.goto(f"{BASE}/accounts")
    expect(page.get_by_text("Connecting TikTok isn't available on this site yet.")).to_be_visible()
    assert page.get_by_text("(one-time setup)").count() == 0, "an app-key setup is offered hosted"
    expect(page.get_by_text("Logins are kept in your account's own folder")).to_be_visible()
    page.screenshot(path=f"{OUT}/h3_accounts_1400_dark.png", full_page=True)
    page.goto(f"{BASE}/campaigns?find=1")
    page.wait_for_selector("text=Check a campaign")
    assert page.get_by_text("Hear about new campaigns first.").count() == 0, "the Whop alerts are offered hosted"
    page.goto(f"{BASE}/new")
    page.wait_for_selector("text=Which moments")
    assert page.get_by_text("on this PC").count() == 0
    print("3: Accounts without app keys, no campaign alerts, New clips without this PC")
    # 5: Learning's address says when it opens
    page.goto(f"{BASE}/learning")
    expect(page.get_by_text("Learning opens after 30 rated clips")).to_be_visible()
    print("5: Learning hidden, its page says it opens after 30 rated clips")
    ctx.close()

    # 5: with 30 rated clips it's back, in the nav and the page
    ctx, page = context(1400, 1000, "light", rated=30)
    page.goto(f"{BASE}/learning")
    expect(page.locator("aside").get_by_role("link", name="Learning")).to_be_visible()
    expect(page.get_by_role("heading", name="Learning")).to_be_visible()
    page.screenshot(path=f"{OUT}/h5_learning_30_1400_light.png")
    page.goto(f"{BASE}/")
    expect(page.locator("aside").get_by_text("42 of 60 min")).to_be_visible()
    sideways(page)
    page.screenshot(path=f"{OUT}/h1_dashboard_1400_light.png")
    page.locator("aside").screenshot(path=f"{OUT}/h1b_sidebar_light.png")
    print("5: at 30 rated clips Learning is in the nav again")
    ctx.close()

    # --- 900 px: the sidebar as icons, the usage as one with its words on hover ---
    ctx, page = context(900, 800, "dark")
    page.goto(f"{BASE}/")
    rail = page.locator("aside")
    expect(rail.get_by_role("link", name="Plan and billing")).to_be_visible()
    expect(rail.get_by_role("button", name="Log out")).to_be_visible()
    sideways(page)
    rail.screenshot(path=f"{OUT}/h1c_rail_900_dark.png")
    print("1: narrow window: usage and log out as icons")
    ctx.close()

    # --- 375 px, dark and light: the usage under More ---
    for scheme in ("dark", "light"):
        ctx, page = context(375, 812, scheme)
        page.goto(f"{BASE}/")
        tabs = page.locator("nav[aria-label=Main]").last
        expect(tabs.get_by_role("link", name="Create Beta")).to_be_visible()
        sideways(page)
        tabs.get_by_role("button", name="More").click()
        menu = page.get_by_role("menu")
        expect(menu.get_by_text("42 of 60 min")).to_be_visible()
        expect(menu.get_by_role("menuitem", name="Plan and billing")).to_be_visible()
        expect(menu.get_by_role("menuitem", name="Log out")).to_be_visible()
        assert menu.get_by_role("menuitem", name="Learning").count() == 0
        page.wait_for_timeout(400)   # the menu fades in
        page.screenshot(path=f"{OUT}/h1_phone_menu_{scheme}.png")
        page.goto(f"{BASE}/create")
        page.wait_for_selector("text=Make a Short")
        sideways(page)
        page.screenshot(path=f"{OUT}/h4_create_phone_{scheme}.png")
        page.goto(f"{BASE}/settings")
        expect(page.get_by_role("heading", name="Clipping")).to_be_visible()
        sideways(page)
        page.screenshot(path=f"{OUT}/h3_settings_phone_{scheme}.png", full_page=True)
        print(f"375 px {scheme}: Create Beta tab, usage in More, nothing sideways")
        ctx.close()
    print("page errors:", errors)
    assert not errors, errors
    b.close()
