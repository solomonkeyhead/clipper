"""Click through a campaign's Shape and brand section, New clips' shape and a clip's caption file (D240).

Run scripts/ui/serve.py <empty data dir> in the background first (port 8799), then
python scripts/ui/brand_walkthrough.py <folder for screenshots> <that data dir>."""
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

OUT, DATA = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Caveat.ttf"
logo = Image.new("RGBA", (600, 300), (255, 80, 40, 230))
ImageDraw.Draw(logo).text((60, 90), "BRAND", fill="white", font_size=140)
raw = io.BytesIO()
logo.save(raw, "PNG")
# A caption file beside the test server's finished clip, as a render leaves one (D240).
(DATA / "library" / "german-professor" / "001_voice.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n\n",
                                                                     encoding="utf-8")
with sync_playwright() as p:
    exe = next(__import__("glob").iglob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"), None)
    b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch(channel="msedge")
    page = b.new_page(viewport={"width": 1300, "height": 1000})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://127.0.0.1:8799/campaigns")
    title = f"Brand Test {int(__import__('time').time())}"
    made = page.evaluate("""(title) => fetch('/api/campaigns', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title, platform_targets: ['tiktok']})}).then(r => r.json())""", title)
    made["title"], name = title, made["name"]
    page.goto(f"http://127.0.0.1:8799/campaigns/{name}/edit")
    section = page.locator("#brand")
    expect(section).to_be_visible()
    preview = page.get_by_test_id("brand-preview")
    assert abs(preview.bounding_box()["width"] / preview.bounding_box()["height"] - 9 / 16) < 0.02, "the preview isn't 9:16"
    section.get_by_role("radio", name="1:1 square").click()
    box = preview.bounding_box()
    assert abs(box["width"] / box["height"] - 1) < 0.02, "the preview didn't turn square"
    with page.expect_file_chooser() as chooser:
        section.get_by_role("button", name="Upload a font").click()
    chooser.value.set_files(str(FONT))
    expect(section.get_by_text("Caveat for captions and the hook")).to_be_visible()
    with page.expect_file_chooser() as chooser:
        section.get_by_role("button", name="Upload a logo").click()
    chooser.value.set_files(files=[{"name": "logo.png", "mimeType": "image/png", "buffer": raw.getvalue()}])
    expect(preview.get_by_alt_text("Logo")).to_be_visible()
    expect(section.get_by_label("Logo corner")).to_have_value("auto")
    section.get_by_label("Text", exact=True).fill("#fff3c4")
    expect(section.get_by_role("button", name="Use Clipper's own")).to_have_count(1)
    page.wait_for_timeout(500)   # the font loads into the page
    section.screenshot(path=str(OUT / "brand_section.png"))
    # A bad upload says why, and changes nothing.
    with page.expect_file_chooser() as chooser:
        section.get_by_role("button", name="Change the font").click()
    chooser.value.set_files(files=[{"name": "fake.ttf", "mimeType": "font/ttf", "buffer": b"not a font" * 50}])
    expect(page.get_by_text("That file isn't a font Clipper can read")).to_be_visible()
    page.get_by_role("button", name="Save changes").click()
    page.wait_for_url(f"**/campaigns/{name}")
    form = page.evaluate(f"fetch('/api/campaigns/{name}/form').then(r => r.json())")
    assert form["output_shape"] == "1:1" and form["brand"]["text_colour"] == "#fff3c4", form
    assert form["brand"]["font"].endswith(".ttf") and form["brand"]["logo"].endswith(".png"), form
    print("brand: shape, font, logo and colour saved", form["brand"])
    # New clips: the campaign's shape, changeable for one run.
    page.goto("http://127.0.0.1:8799/new")
    page.get_by_role("button", name=made["title"]).click()
    shapes = page.get_by_role("radiogroup", name="Shape")
    expect(shapes.get_by_role("radio", name="1:1 square")).to_have_attribute("aria-checked", "true")
    shapes.get_by_role("radio", name="16:9 wide").click()
    expect(page.get_by_text("This run only; the campaign stays 1:1.")).to_be_visible()
    page.screenshot(path=str(OUT / "new_clips_shape.png"))
    # A clip with a caption file offers it beside the download.
    clips = page.evaluate("fetch('/api/clips').then(r => r.json())")
    with_srt = next(c for c in clips if c.get("captions"))
    page.goto("http://127.0.0.1:8799/clips")
    page.get_by_text(with_srt["title"], exact=True).first.click()
    expect(page.get_by_role("link", name="Captions (.srt)")).to_be_visible()
    page.screenshot(path=str(OUT / "clip_captions.png"))
    srt =page.evaluate(f"fetch('{with_srt['captions']}').then(r => r.text())")
    assert "Hello" in srt, srt
    print("captions link:", with_srt["captions"])
    assert not errors, errors
    b.close()
