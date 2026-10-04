"""Click through the real Create page like the user did, and check after every step (D127).

Run scripts/ui/serve.py <empty data dir> in the background first (port 8799), then
python scripts/ui/walkthrough.py <folder for screenshots>. Needs `pip install playwright`;
the browser is the pre-installed Chromium in /opt/pw-browsers."""
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
    rebuild = page.get_by_role("button", name="Rebuild with 2 changes")
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
    # the footage libraries card in Settings (D130)
    page.goto("http://127.0.0.1:8799/settings")
    expect(page.get_by_text("Footage libraries for Create")).to_be_visible()
    for name in ("Pixabay", "Pexels", "Coverr"):
        expect(page.get_by_label(name, exact=True)).to_be_visible()
    page.get_by_text("Footage libraries for Create").scroll_into_view_if_needed()
    page.screenshot(path=f"{OUT}/4_settings.png")
    print("settings: footage card with Pixabay, Pexels, Coverr")
    print("page errors:", errors)
    b.close()
