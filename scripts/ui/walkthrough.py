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
    built = cards.nth(1)                         # the newest (a draft) first, the built one second
    if built.get_attribute("aria-expanded") == "false":
        built.click()
    review = page.get_by_role("button", name="Change parts you don't like")
    expect(review).to_be_visible()
    print("review open:", review.get_attribute("aria-expanded"))
    footage = page.get_by_role("button", name="New footage")
    print("parts listed:", footage.count())
    page.screenshot(path=f"{OUT}/1_open.png", full_page=True)

    # choose new footage for the last part in the picker (D129), then a drawing on another
    footage.last.click()
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
    page.get_by_role("button", name="New drawing").nth(1).click()
    expect(page.get_by_text("new drawing on the next build")).to_be_visible()
    print("after new drawing: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    assert footage.count() == 7, "the parts list vanished after asking for a new drawing"
    # the camera on a part's footage (D164): only that shot is made again, and it counts as a change
    page.get_by_label("Camera, part 1").select_option("still")
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
    page.get_by_role("button", name="Undo").first.click()
    expect(page.get_by_role("button", name="Rebuild with 1 change")).to_be_visible()
    page.get_by_role("button", name="Rebuild with 1 change").click()
    page.wait_for_selector("text=The last rebuild didn't finish", timeout=60000)
    expect(review).to_be_visible()
    print("after failed rebuild: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    page.screenshot(path=f"{OUT}/3_after_rebuild.png", full_page=True)
    # a part set by hand (D171): a drawing's way in and size, from its Adjust panel; captions and the cover
    adjust = page.get_by_role("button", name="Adjust")
    for k in range(adjust.count()):
        adjust.nth(k).click()
        if page.get_by_label("Drawing size, part 3").count():
            break
        adjust.nth(k).click()
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
    for k in range(adjust.count()):
        adjust.nth(k).click()
        if page.get_by_label("Framing, part 1").count():
            break
    page.get_by_label("Framing, part 1").select_option(label="framing: left")
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
    page.get_by_role("button", name="Use this frame").first.click()
    expect(page.get_by_text("Cover: the frame at").first).to_be_visible()
    print("cover frame chosen")
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
    page.get_by_role("button", name="Back to Create").click()
    expect(page.get_by_text("Back in Create")).to_be_visible()
    expect(page.get_by_role("button", name="Archive 1")).to_have_count(0)
    expect(page.get_by_text("You brought it back from the Archive")).to_be_visible()
    print("brought back: cards", cards.count())
    page.reload()
    page.wait_for_selector("text=Make a Short")
    expect(cards).to_have_count(3)
    assert page.get_by_role("button", name="Archive 1").count() == 0, "a brought-back video went back into the archive"
    page.get_by_role("button", name="Move to the Archive").first.click()
    expect(page.get_by_role("button", name="Archive 1")).to_be_visible()
    expect(cards).to_have_count(2)
    print("archived by hand: cards", cards.count())
    # the footage libraries card in Settings (D130)
    page.goto("http://127.0.0.1:8799/settings")
    expect(page.get_by_text("Footage libraries for Create")).to_be_visible()
    for name in ("Pixabay", "Pexels", "Coverr"):
        expect(page.get_by_label(name, exact=True)).to_be_visible()
    page.get_by_text("Footage libraries for Create").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/5_settings.png")
    print("settings: footage card with Pixabay, Pexels, Coverr")
    print("page errors:", errors)
    b.close()
