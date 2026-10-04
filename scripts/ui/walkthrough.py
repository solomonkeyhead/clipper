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

    # ask for footage on the last part, then a drawing on another
    footage.last.click()
    expect(page.get_by_text("new footage on the next build")).to_be_visible()
    print("after new footage: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    page.get_by_role("button", name="New drawing").nth(1).click()
    expect(page.get_by_text("new drawing on the next build")).to_be_visible()
    print("after new drawing: review", review.get_attribute("aria-expanded"), "parts", footage.count())
    rebuild = page.get_by_role("button", name="Rebuild with 2 changes")
    expect(rebuild).to_be_visible()
    page.screenshot(path=f"{OUT}/2_changes.png", full_page=True)

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
    print("page errors:", errors)
    b.close()
