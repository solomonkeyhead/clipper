"""Click through the real Create page like the user did, and check after every step (D127).

Run scripts/ui/serve.py <empty data dir> in the background first (port 8799), then
python scripts/ui/walkthrough.py <folder for screenshots>. Needs `pip install playwright`;
the browser is the pre-installed Chromium in /opt/pw-browsers (a cloud session's)."""
import sys, time
from playwright.sync_api import sync_playwright, expect

OUT = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=next(__import__("glob").iglob("/opt/pw-browsers/chromium-*/chrome-linux/chrome")))
    page = b.new_page(viewport={"width": 1400, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8799/create")
    page.wait_for_selector("text=Make a Short")
    cards = page.locator("button[aria-expanded]", has_text="Why does your voice")
    print("cards", cards.count())
    # D205: a draft's sentences show their words and picture only; one sentence's controls open on click
    draft = cards.nth(0)
    if draft.get_attribute("aria-expanded") == "false":
        draft.click()
    expect(page.get_by_label("Sentence 3")).to_be_visible()
    assert page.get_by_label("Picture", exact=True).count() == 0, "a draft opened with every sentence's controls"
    page.get_by_label("Sentence 3").click()
    expect(page.get_by_label("Picture", exact=True)).to_have_count(1)
    three = page.get_by_label("Sentence 3").input_value()
    page.get_by_role("button", name="Move down").click()
    expect(page.get_by_label("Sentence 4")).to_have_value(three)
    expect(page.locator("[data-open=true] textarea")).to_have_value(three)   # the controls went with it
    page.get_by_role("button", name="Move up").click()
    expect(page.get_by_label("Description")).to_have_count(0)                # folded
    print("draft: one sentence's controls at a time, they follow a moved sentence")
    page.screenshot(path=f"{OUT}/0_draft.png", full_page=True)
    # D207, D215: amber marks the next thing to do and nothing else: on a draft, Approve (or Check) alone
    amber = page.evaluate("""() => [...document.querySelectorAll('main *')].filter(e => { const s = getComputedStyle(e);
        return e.getClientRects().length && ([s.backgroundColor, s.borderTopColor].includes('rgb(255, 194, 41)')
          || (s.color === 'rgb(255, 194, 41)' && !e.children.length && e.textContent.trim())); }).length""")
    assert amber <= 1, f"{amber} amber things on a draft"
    # D208: the bar says the step and what Approve locks; a checked (ready-made) script has Approve as its filled button
    expect(page.get_by_text("Step 1 of 4, the script.")).to_be_visible()
    # D215: the draft's other actions are under its bar's More
    bar = page.get_by_text("Step 1 of 4, the script.").locator("xpath=..")
    bar.locator("summary", has_text="More").click()
    expect(page.get_by_role("button", name="Check physics again")).to_be_visible()
    bar.locator("summary", has_text="More").click()
    draft.click()
    built = cards.nth(1)                        # the newest (a draft) first, the built one second
    if built.get_attribute("aria-expanded") == "false":
        built.click()
    review = page.get_by_role("button", name="Change parts you don't like")
    expect(review).to_be_visible()
    print("review open:", review.get_attribute("aria-expanded"))
    # D206: the finished card's poster reads, and the player is bigger on a wide window
    assert page.locator("img[src^='/thumb/']").first.bounding_box()["height"] >= 79, "the finished card's poster is a strip again"
    assert page.locator("video").first.bounding_box()["width"] >= 287, "the player is narrow on a wide window"
    # D215: a part is picked first ("Change this part"), then its tools open under it
    footage = page.get_by_test_id("part")

    def choose(part):
        if part.get_by_role("button", name="Change this part").count():
            part.get_by_role("button", name="Change this part").click()
        return part

    print("parts listed:", footage.count())
    page.screenshot(path=f"{OUT}/1_open.png", full_page=True)

    # choose new footage for the last part in the picker (D129), then a drawing on another
    choose(footage.last).get_by_role("button", name="New footage").click()
    picker = page.get_by_test_id("footage-picker")
    expect(picker).to_be_visible()
    offered = picker.locator("button[aria-pressed]")
    expect(offered.first).to_be_visible(timeout=30000)
    srcs = [offered.nth(k).locator("img").get_attribute("src") for k in range(offered.count())]
    print("offered:", offered.count(), "| clip in use offered again:", any("4000008" in x for x in srcs))
    assert offered.count() > 0 and not any("4000008" in x for x in srcs), "the clip in use was offered again"
    print("first offer says:", offered.first.inner_text().splitlines()[0])
    # hovering a candidate plays it (D130)
    first_preview = offered.first.get_by_test_id("preview")
    first_preview.hover()
    clip = first_preview.locator("video")
    expect(clip).to_have_count(1)
    page.wait_for_function("el => !el.paused && el.currentTime > 0.3", arg=clip.element_handle(), timeout=15000)
    print("hover preview: playing at", round(clip.evaluate("el => el.currentTime"), 2), "s")
    page.mouse.move(5, 5)
    page.wait_for_function("el => el.paused", arg=clip.element_handle(), timeout=5000)
    print("hover preview: paused when the pointer left")
    expect(picker.get_by_text("Photo · Pixabay").first).to_be_visible()   # photos among the footage (D163)
    picker.scroll_into_view_if_needed()
    picker.screenshot(path=f"{OUT}/1b_picker.png")
    offered.first.click()
    picker.get_by_role("button", name="Use this one").click()
    expect(page.get_by_text("your footage on the next build")).to_be_visible()
    expect(picker).to_have_count(0)
    print("after new footage: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    assert footage.count() == 7, "the parts list vanished after asking for new footage"
    choose(footage.nth(1)).get_by_role("button", name="New drawing").click()
    expect(page.get_by_text("new drawing on the next build")).to_be_visible()
    print("after new drawing: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    assert footage.count() == 7, "the parts list vanished after asking for a new drawing"
    # the camera on a part's footage (D164): only that shot is made again, and it counts as a change
    choose(footage.nth(0)).get_by_label("Camera, part 1").select_option("still")
    expect(page.get_by_text("new camera on the next build")).to_be_visible()
    rebuild = page.get_by_role("button", name="Rebuild with 3 changes")
    expect(rebuild).to_be_visible()
    page.screenshot(path=f"{OUT}/2_changes.png", full_page=True)

    # the page reloaded: the changes are saved and the list still reads
    page.reload()
    page.wait_for_selector("text=Make a Short")
    if cards.nth(1).get_attribute("aria-expanded") == "false":
        cards.nth(1).click()
    expect(page.get_by_text("your footage on the next build")).to_be_visible()
    print("after reload: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    assert footage.count() == 7, "the parts list is gone after reloading"

    # close and open the section again
    review.click(); time.sleep(0.3)
    print("closed:", review.get_attribute("aria-expanded"), "parts", footage.count())
    review.click(); time.sleep(0.3)
    print("opened again:", review.get_attribute("aria-expanded"), "parts", footage.count())

    # undo one, then rebuild (it fails here: no real voice) and the review must still be there
    for k in range(footage.count()):
        undo = choose(footage.nth(k)).get_by_role("button", name="Undo")
        if undo.count():
            undo.click()
            break
    expect(page.get_by_role("button", name="Rebuild with 1 change")).to_be_visible()
    page.get_by_role("button", name="Rebuild with 1 change").click()
    page.wait_for_selector("text=The last rebuild didn't finish", timeout=60000)
    expect(review).to_be_visible()
    print("after failed rebuild: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    page.screenshot(path=f"{OUT}/3_after_rebuild.png", full_page=True)
    # a part set by hand (D171): a drawing's way in and size, from its Adjust panel; captions and the cover
    def adjust_until(label):
        for k in range(footage.count()):
            choose(footage.nth(k)).get_by_role("button", name="Adjust").click()
            if page.get_by_label(label).count():
                return
        raise AssertionError(f"no part has {label}")

    adjust_until("Drawing size, part 3")
    page.get_by_label("Way in, part 3").select_option("whip")
    page.get_by_label("Drawing size, part 3").select_option(label="size 115%")
    expect(page.get_by_label("Way in, part 3")).to_have_value("whip")
    expect(page.get_by_test_id("part-adjust")).to_be_visible()
    page.get_by_test_id("part-adjust").screenshot(path=f"{OUT}/4b_adjust.png")
    print("part 3 set by hand: whip in, 115%")
    # D180: a sketch's colours and arrows on the same panel, then footage framing on part 1
    marks = page.get_by_test_id("sketch-marks")
    marks.get_by_label("Colour of “air”").select_option("red")
    marks.get_by_role("button", name="Turn round").first.click()
    for _ in range(20):
        if page.evaluate("async () => (await (await fetch('/api/create')).json()).videos"
                         ".some(v => (v.script?.beats || []).some(b => (b.visual?.sketch?.marks || []).some(m => m.text === 'air' && m.color === 'red')))"):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the colour set for 'air' wasn't saved")
    marks.screenshot(path=f"{OUT}/4c_marks.png")
    adjust_until("Framing, part 1")
    page.get_by_label("Framing, part 1").select_option(label="Framed left")
    expect(page.get_by_label("Framing, part 1")).to_have_value("0.25")
    print("sketch colour, arrow and footage framing set by hand")
    page.get_by_text("Captions", exact=True).first.click()
    caption = page.get_by_label("Caption for sentence 2")
    caption.fill("You think the recording lies.")
    caption.blur()
    for _ in range(20):   # saved when the field loses focus
        if page.evaluate("async () => (await (await fetch('/api/create')).json()).videos"
                         ".some(v => (v.script?.beats || []).some(b => b.caption === 'You think the recording lies.'))"):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the caption written for sentence 2 wasn't saved")
    print("caption for sentence 2 saved")
    page.get_by_label("Caption size").select_option(label="size: bigger")
    page.get_by_label("Caption place").select_option(label="place: lower")
    page.get_by_label("Said word colour").select_option(label="said word: blue")
    expect(page.get_by_label("Said word colour")).to_have_value("#4fd2ff")
    print("caption size, place and colour set (D180)")
    page.get_by_role("button", name="Look and sound").first.click()   # sound, the loop and the cover, folded (D199)
    page.get_by_label("Seamless loop").first.check()
    page.get_by_label("Presenter").first.select_option("ends")   # the opening and the punchline only (D203)
    for _ in range(20):
        if page.evaluate("async () => (await (await fetch('/api/create')).json()).videos"
                         ".some(v => v.script?.on_screen === 'ends')"):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("the presenter choice wasn't saved")
    expect(page.get_by_text("the presenter").first).to_be_visible()   # the Build bar lists it as not built yet
    print("presenter: opening and punchline")
    page.get_by_role("button", name="Use this frame").first.click()
    expect(page.get_by_text("Cover: the frame at").first).to_be_visible()
    print("cover frame chosen")
    # D199: New pictures is under More and asks first
    page.get_by_role("button", name="Build again").locator("xpath=..").locator("summary", has_text="More").click()
    page.get_by_role("button", name="New pictures for every part…").click()
    expect(page.get_by_text("Plans every picture you haven't chosen yourself")).to_be_visible()
    page.get_by_role("button", name="Keep these").click()
    print("new pictures asks first")
    # the archive (D131): the posted video is in it by itself, comes back, and goes in again by hand
    expect(page.get_by_text("in the Archive")).to_be_visible()
    expect(page.get_by_role("button", name="Make again")).to_be_visible()
    shelf = page.get_by_role("button", name="Archive 1")
    expect(shelf).to_be_visible()
    print("cards before opening the archive:", cards.count())
    assert cards.count() == 2, "the posted video is still in the list"
    shelf.click()
    expect(cards).to_have_count(3)
    cards.nth(2).click()
    expect(page.get_by_text("Marked posted in Clips.")).to_be_visible()
    page.screenshot(path=f"{OUT}/4_archive.png", full_page=True)
    cards.nth(2).locator("xpath=..").locator("summary").click()   # a card's actions are named under More (D215)
    page.get_by_role("button", name="Back to Create").click()
    expect(page.get_by_text("Back in Create")).to_be_visible()
    expect(page.get_by_role("button", name="Archive 1")).to_have_count(0)
    expect(page.get_by_text("You brought it back from the Archive")).to_be_visible()
    print("brought back: cards", cards.count())
    page.reload()
    page.wait_for_selector("text=Make a Short")
    expect(cards).to_have_count(3)
    assert page.get_by_role("button", name="Archive 1").count() == 0, "a brought-back video went back into the archive"
    cards.first.locator("xpath=..").locator("summary").click()
    page.get_by_role("button", name="Move to the Archive").first.click()
    expect(page.get_by_role("button", name="Archive 1")).to_be_visible()
    expect(cards).to_have_count(2)
    print("archived by hand: cards", cards.count())
    # D192: with a slow server, a click still answers at once: archiving moves the card before the server has
    # answered, and a button waiting on it spins and takes no second click.
    page.evaluate("""() => { const real = window.fetch; window.__sent = 0;
        window.fetch = async (u, o) => { if (o && o.method && o.method !== 'GET') { window.__sent++;
            await new Promise((r) => setTimeout(r, 1500)); } return real(u, o); }; }""")
    cards.first.locator("xpath=..").locator("summary").click()
    page.get_by_role("button", name="Move to the Archive").first.click()
    expect(cards).to_have_count(1, timeout=300)
    expect(page.get_by_role("button", name="Archive 2")).to_be_visible(timeout=300)
    page.wait_for_timeout(1700)
    again = page.get_by_role("button", name="Make again")
    sent = page.evaluate("window.__sent")
    again.click()
    again.click(force=True)
    expect(again).to_have_attribute("data-busy", "true", timeout=400)
    assert page.evaluate("window.__sent") == sent + 1, "a second click on a waiting button was sent"
    page.wait_for_timeout(1700)
    print("slow server: the archive moved at once, a waiting button spun and took one click")
    # D209: the Dashboard leads with one setup list, then Next up; the header names the AI fix and goes to it
    page.goto("http://127.0.0.1:8799/")
    expect(page.get_by_role("heading", name="Set up Clipper")).to_be_visible()
    heads = page.locator("main h2").all_inner_texts()
    assert heads.index("Next up") < heads.index("Since you were last here"), heads
    assert page.get_by_text("What will you use Clipper for").count() == 0, "the old questionnaire is back"
    print("dashboard:", heads)
    # D211: one lime fill on the whole screen, shell included (the nav item lights its icon only)
    fills = page.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => e.getClientRects().length
        && getComputedStyle(e).backgroundColor === 'rgb(215, 255, 58)').length""")
    assert fills <= 1, f"{fills} lime fills on the Dashboard"
    assert page.get_by_role("link", name="Add an AI key").count() == 0, "the AI fix shows twice on the Dashboard"
    # the footage libraries card in Settings (D130)
    page.goto("http://127.0.0.1:8799/settings")
    expect(page.get_by_text("Footage libraries for Create")).to_be_visible()
    for name in ("Pixabay", "Pexels", "Coverr"):
        expect(page.get_by_label(name, exact=True)).to_be_visible()
    page.get_by_text("Footage libraries for Create").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/5_settings.png")
    print("settings: footage card with Pixabay, Pexels, Coverr")
    # D216: one AI choice for every job, no theme switch, no auto-post; Learning without the score charts
    expect(page.get_by_label("Which AI")).to_be_visible()
    for gone in ("Who does what: clipping", "Theme", "Auto-post approved clips", "What you use Clipper for"):
        assert page.get_by_text(gone, exact=True).count() == 0, f"{gone!r} is back in Settings"
    page.goto("http://127.0.0.1:8799/learning")
    expect(page.get_by_role("heading", name="What Clipper has learned")).to_be_visible()
    for gone in ("Does the score work?", "What's working", "Export my taste"):
        assert page.get_by_text(gone).count() == 0, f"{gone!r} is back on Learning"
    assert page.get_by_role("button", name="Ask Clipper").count() == 0, "the Ask button is back"
    print("D216: Which AI in Settings; Learning, Ask, theme and auto-post trimmed")
    print("page errors:", errors)
    b.close()
