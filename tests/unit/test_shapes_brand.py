"""Clip shapes, caption files and the brand kit (D240)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from clipper.campaign import editor
from clipper.config import CampaignConfig, Config, SafeArea, shape_render
from clipper.models import CropKeyframe, LayoutPlan, LayoutSegment, Word
from clipper.render import brand
from clipper.render import captions as cap
from clipper.render.graph import RenderSpec, build_command
from clipper.runner import campaign_config

FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "Inter.ttf"
AUTH = "Official footage supplied by the campaign Test, which pays clippers to post it."


def _spec(layout: LayoutPlan, width: int = 1080, height: int = 1920, **kw) -> RenderSpec:
    return RenderSpec(source=Path("src.mp4"), output=Path("out.mp4"), start=1.5, duration=12.0, layout=layout,
                      ass_path=Path("c.ass"), fonts_dir=Path("fonts"), width=width, height=height, fps=30,
                      encoder="libx264", loudness_lufs=-14.0, true_peak_dbtp=-1.5, crf=20, nvenc_cq=23,
                      x264_preset="veryfast", audio_bitrate="192k", audio_rate=48000, **kw)


FIT = LayoutPlan(kind="fit_crop", crop_width=900, crop_height=1080, keyframes=[CropKeyframe(t=0, x=300, y=0)])
BLUR = LayoutPlan(kind="blurred_fit", crop_width=1920, crop_height=1080)
FOLLOW = LayoutPlan(kind="follow_crop", crop_width=608, crop_height=1080, keyframes=[CropKeyframe(t=0, x=400, y=0)])
PER_SHOT = LayoutPlan(kind="per_shot", segments=[LayoutSegment(start=0, end=3, layout=FIT),
                                                 LayoutSegment(start=3, end=9, layout=BLUR),
                                                 LayoutSegment(start=9, end=12, layout=FOLLOW)])

#: The 9:16 command line exactly as it was before shapes and brand kits: Marc's campaigns must render
#: byte for byte as they did.
PINNED_916 = (
    "[0:v]split=3[shot0][shot1][shot2];[shot0]trim=start=0.000:end=3.000,setpts=PTS-STARTPTS[cut0];"
    "[cut0]crop=900:1080:300:0,split=2[bgs0][fgs0];[bgs0]scale=270:480:force_original_aspect_ratio=increase,"
    "crop=270:480,gblur=sigma=10.0,scale=1080:1920:flags=bicubic,eq=brightness=-0.15[bgbs0];"
    "[fgs0]scale=1080:-2:flags=lanczos[fgss0];[bgbs0][fgss0]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,"
    "format=yuv420p[seg0];[shot1]trim=start=3.000:end=9.000,setpts=PTS-STARTPTS[cut1];[cut1]split=2[bgs1][fgs1];"
    "[bgs1]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=10.0,"
    "scale=1080:1920:flags=bicubic,eq=brightness=-0.15[bgbs1];[fgs1]scale=1080:-2:flags=lanczos[fgss1];"
    "[bgbs1][fgss1]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1,format=yuv420p[seg1];"
    "[shot2]trim=start=9.000:end=12.000,setpts=PTS-STARTPTS[cut2];[cut2]crop=608:1080:400:0,"
    "scale=1080:1920:flags=lanczos,setsar=1,format=yuv420p[seg2];[seg0][seg1][seg2]concat=n=3:v=1:a=0,fps=30,"
    "setsar=1,format=yuv420p,ass=f='c.ass':fontsdir='fonts':original_size=1080x1920,"
    "drawtext=text='Made with Clipper':fontsize=28:fontcolor=white@0.8:shadowcolor=black@0.6:shadowx=2:shadowy=2:"
    "x=w-tw-120:y=820-th[v];[0:a]loudnorm=I=-14.0:TP=-1.5:LRA=8.0,aresample=48000:resampler=soxr,"
    "aformat=sample_fmts=fltp:channel_layouts=stereo[a]")


def test_916_command_line_is_unchanged():
    cmd = build_command(_spec(PER_SHOT, watermark="Made with Clipper"))
    assert cmd == ["-hide_banner", "-nostdin", "-loglevel", "error", "-ss", "1.500", "-t", "12.000", "-i", "src.mp4",
                   "-filter_complex", PINNED_916, "-map", "[v]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k",
                   "-ar", "48000", "-ac", "2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                   "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                   "-fps_mode", "cfr", "-y", "out.mp4"]


def test_a_campaign_that_does_not_say_keeps_916():
    base = Config()
    campaign = CampaignConfig(name="t", source_authorization=AUTH)
    render = campaign_config(base, campaign).render
    assert (render.width, render.height, render.safe_area) == (1080, 1920, base.render.safe_area)
    assert (render.brand_font, render.brand_logo, render.caption_text_colour) == ("", "", "")
    assert shape_render("9:16") == {}


def test_square_and_wide_shapes_set_the_render():
    square = campaign_config(Config(), CampaignConfig(name="t", source_authorization=AUTH, output_shape="1:1")).render
    assert (square.width, square.height, square.draft_width, square.draft_height) == (1080, 1080, 540, 540)
    wide = campaign_config(Config(), CampaignConfig(name="t", source_authorization=AUTH, output_shape="16:9")).render
    assert (wide.width, wide.height) == (1920, 1080)


def test_wide_from_wide_is_the_full_frame_and_vertical_is_fitted_by_height():
    full = build_command(_spec(BLUR, 1920, 1080))[11]
    assert full.startswith("[0:v]scale=1920:1080:flags=lanczos,fps=30") and "gblur" not in full
    vertical = build_command(_spec(LayoutPlan(kind="blurred_fit", crop_width=1080, crop_height=1920), 1920, 1080))[11]
    assert "[fg]scale=-2:1080:flags=lanczos[fgs]" in vertical and "gblur" in vertical


def test_square_follows_the_face_crop():
    square = LayoutPlan(kind="follow_crop", crop_width=1080, crop_height=1080, keyframes=[CropKeyframe(t=0, x=500, y=0)])
    graph = build_command(_spec(square, 1080, 1080))[11]
    assert graph.startswith("[0:v]crop=1080:1080:500:0,scale=1080:1080:flags=lanczos")


def test_a_logo_is_a_second_input_in_its_corner():
    cmd = build_command(_spec(FOLLOW, logo=Path("logo.png")))
    assert cmd[cmd.index("src.mp4") + 1:cmd.index("src.mp4") + 3] == ["-i", "logo.png"]
    graph = cmd[cmd.index("-filter_complex") + 1]
    # 9:16 by itself: bottom right, above the button rail (y=820), at most 180x120.
    assert "[1:v]format=rgba,scale=180:120:force_original_aspect_ratio=decrease[logo]" in graph
    assert "[base][logo]overlay=x=960-w:y=820-h:format=auto,format=yuv420p,ass=" in graph
    wide = build_command(_spec(BLUR, 1920, 1080, logo=Path("logo.png")))[13]
    assert "overlay=x=1824-w:y=60:" in wide   # 16:9: top right
    stacked = build_command(_spec(FOLLOW, logo=Path("logo.png"), watermark="Made with Clipper"))[13]
    assert "overlay=x=960-w:y=772-h:" in stacked   # above the watermark


def test_wide_captions_are_sized_by_the_short_side():
    words = [Word(start=1.0, end=1.4, text="Hello"), Word(start=1.5, end=1.9, text="there.")]
    wide = cap.build_ass(words, style=cap.get_style("bold_pop"), width=1920, height=1080,
                         safe_area=SafeArea(top=60, bottom=80, side=96, right=96), duration=5.0)
    assert "Style: Caption,Anton,96," in wide and "PlayResX: 1920" in wide


def _words() -> list[Word]:
    text = "So here is the thing. Nobody tells you {this} at all"
    return [Word(start=10 + i * 0.5, end=10 + i * 0.5 + 0.4, text=t) for i, t in enumerate(text.split())]


def test_srt_has_the_burned_in_pages_in_clip_time():
    ass = cap.build_ass(_words(), style=cap.get_style("bold_pop"), width=1080, height=1920, safe_area=SafeArea(),
                        clip_start=10.0, duration=8.0, hook_text="A hook", hook_seconds=2.0)
    srt = cap.build_srt(ass)
    cues = srt.strip().split("\n\n")
    assert cues[0].startswith("1\n00:00:00,000 --> ")
    assert cues[0].endswith("So here is the\nthing.")          # its line break, as burned in
    assert cues[1] == "2\n00:00:02,500 --> 00:00:08,000\nNobody tells you\n{this} at all"   # braces unescaped
    assert "A hook" not in srt                                   # the hook isn't a caption
    assert len(cues) == len(cap.caption_pages(ass))


def test_srt_follows_the_cover_and_teaser_put_first():
    ass = cap.build_ass(_words(), style=cap.get_style("bold_pop"), width=1080, height=1920, safe_area=SafeArea(),
                        clip_start=10.0, duration=8.0)
    pages = cap.caption_pages(ass)
    srt = cap.build_srt(ass, [(2.5, 4.0, 0.5), (0.0, float("inf"), 2.0)])
    first = srt.split("\n\n")[0].splitlines()[1]
    assert first.startswith("00:00:00,500 --> ")                # the teaser's page, after 0.5 s of cover
    assert f"{cap._srt_time(pages[0][0] + 2.0)} --> " in srt     # the clip itself, after cover and teaser


def test_brand_colours_and_font_apply_to_the_style(tmp_path, monkeypatch):
    if not FONT.is_file():
        pytest.skip("assets/fonts/Inter.ttf is git-ignored")
    monkeypatch.setattr(brand, "assets_dir", lambda: tmp_path)
    saved = brand.save_font(FONT.read_bytes(), "MyFont.TTF")
    assert saved["family"] == "Inter" and brand.path_of(saved["file"]) == tmp_path / saved["file"]
    rc = Config().render.model_copy(update={"brand_font": saved["file"], "caption_text_colour": "#ff0000",
                                            "caption_highlight_colour": "#00ff00"})
    style = cap.style_for(rc)
    assert (style.font, style.primary, style.highlight) == ("Inter", "&H000000FF", "&H0000FF00")
    assert cap.fonts_dir_for(rc) == tmp_path
    assert cap.style_for(Config().render) is cap.get_style("bold_pop")   # no kit: the style as it was


def test_font_uploads_are_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(brand, "assets_dir", lambda: tmp_path)
    with pytest.raises(brand.BrandError, match="isn't a font"):
        brand.save_font(b"not a font at all" * 100, "evil.ttf")
    with pytest.raises(brand.BrandError, match="font file"):
        brand.save_font(b"x", "font.exe")
    with pytest.raises(brand.BrandError, match="over"):
        brand.save_font(b"\0" * (brand.FONT_CAP + 1), "big.ttf")
    assert not list(tmp_path.iterdir())


def test_logos_are_reencoded_and_only_named_files_are_read(tmp_path, monkeypatch):
    from PIL import Image, PngImagePlugin

    monkeypatch.setattr(brand, "assets_dir", lambda: tmp_path)
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "<script>hello</script>")
    raw = io.BytesIO()
    Image.new("RGBA", (2400, 1200), (255, 0, 0, 128)).save(raw, "PNG", pnginfo=info)
    saved = brand.save_logo(raw.getvalue() + b"appended junk")
    stored = (tmp_path / saved["file"]).read_bytes()
    assert b"<script>" not in stored and b"appended junk" not in stored
    assert (saved["width"], saved["height"]) == (1024, 512)
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")
    with pytest.raises(brand.BrandError, match="PNG or JPG"):
        brand.save_logo(gif.getvalue())
    with pytest.raises(brand.BrandError, match="isn't a picture"):
        brand.save_logo(b"\x89PNG\r\n\x1a\n" + b"x" * 50)
    for name in ("../config.yaml", "C:/Windows/win.ini", "logo.png", saved["file"].upper()):
        assert brand.path_of(name) is None


def test_the_form_writes_shape_and_brand_only_when_chosen():
    form = editor.CampaignForm(title="Test", source_authorization=AUTH)
    data = editor.merged(form, {"output_shape": "1:1", "brand": {"logo": "0" * 16 + ".png"}})
    assert "output_shape" not in data and "brand" not in data
    chosen = form.model_copy(update={"output_shape": "16:9",
                                     "brand": editor.BrandKit(text_colour="#ffffff", logo_corner="top_left")})
    data = editor.merged(chosen, None)
    assert data["output_shape"] == "16:9" and data["brand"] == {"text_colour": "#ffffff", "logo_corner": "top_left"}
    assert editor.validate(data).brand.text_colour == "#ffffff"
    with pytest.raises(ValueError):
        editor.BrandKit(font="../../secret.ttf")
