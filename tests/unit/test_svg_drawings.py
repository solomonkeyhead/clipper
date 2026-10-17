"""D219: Opus's animated SVG drawings, timed to the words, filmed in a browser."""

import cv2
import pytest

from clipper.create import sketch, svgfilm
from clipper.create.script import Visual

SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='1080' height='1120'><style>"
       "#b{animation:go 0.3s linear var(--w2) both}@keyframes go{from{transform:translateX(0)}to{transform:translateX(600px)}}"
       "</style><rect width='1080' height='1120' fill='#18181a'/><rect id='b' x='100' y='500' width='200' height='200' "
       "fill='#ffd65a'/><text x='540' y='200'>water <tspan>film</tspan></text><text x='540' y='900'>grips</text></svg>")


def test_words_are_timed_from_the_voice_or_by_position_or_at_the_usual_pace():
    said = [(0.0, "a"), (0.4, "b"), (0.9, "c")]
    assert svgfilm.timings(3, said, 2.0) == [0.0, 0.4, 0.9]
    assert svgfilm.timings(6, said, 2.0) == [0.0, 0.0, 0.4, 0.4, 0.9, 0.9]   # a number read as two words
    paced = svgfilm.timings(4, None, 10.0)
    assert paced[0] == 0 and abs(paced[1] - 1 / svgfilm.WORDS_PER_SECOND) < 1e-9


def test_the_drawings_words_are_read_and_changed_in_order():
    assert svgfilm.texts(SVG) == ["water film", "grips"]
    changed = svgfilm.set_texts(SVG, ["oil & water", ""])   # an empty one keeps the drawing's
    assert svgfilm.texts(changed) == ["oil & water", "grips"] and "oil &amp; water" in changed


def test_nothing_that_runs_or_loads_is_kept():
    dirty = ("junk <svg onload='x()'><script>alert(1)</script><style>@import url(http://x/y.css);"
             ".a{filter:url(#chalk);background:url(http://evil/p.png)}</style><a href='http://x'>a</a>"
             "<foreignObject><div/></foreignObject></svg> junk")
    out = svgfilm.clean(dirty)
    assert out.startswith("<svg") and out.endswith("</svg>")
    assert not any(bad in out for bad in ("onload", "<script", "@import", "http", "foreignObject"))
    assert "url(#chalk)" in out
    with pytest.raises(ValueError):
        svgfilm.clean("no drawing here")


def test_a_drawing_is_there_when_it_has_marks_or_an_svg_with_no_change_waiting():
    assert not sketch.drawn(None) and not sketch.drawn(sketch.Sketch())
    assert sketch.drawn(sketch.Sketch(svg=SVG))
    assert not sketch.drawn(sketch.Sketch(svg=SVG, ask="make it bigger"))


def test_opus_draws_then_looks_at_its_frames_and_fixes(monkeypatch, data_root):
    asked = []

    def fake_ask(system, user, schema, **kw):
        asked.append((system, user, kw))
        assert schema is None   # the SVG as plain text: in JSON it came back unreadable
        if "media" not in kw:
            return "Here it is:\n" + SVG
        return "- the motion is too small\n" + SVG.replace("600px", "800px")

    monkeypatch.setattr(sketch, "ask", fake_ask)
    monkeypatch.setattr(svgfilm, "sheet", lambda *a, **k: (b"png", [0.5, 1.0]))
    got = sketch.draw_svg("The pad squeezes water out and grips.", "a pad on a disc")
    assert "800px" in got.svg and got.count == 7 and not got.marks
    assert "w3=squeezes" in asked[0][1] and "--w1" in asked[0][0]
    assert asked[1][2]["media"] == [(b"png", "image/png")] and all(kw["job"] == "sketch" for _, _, kw in asked)

    asked.clear()
    sketch.draw_svg("The pad grips.", "a pad", base=SVG, change="make the spring bigger", rounds=0)
    assert "make the spring bigger" in asked[0][1] and SVG in asked[0][1]


@pytest.mark.skipif(not svgfilm.ready(), reason="no browser to film drawings in")
def test_the_drawing_moves_on_its_word(tmp_path):
    from clipper.create import diagrams

    v = Visual(kind="diagram", template="sketch", sketch=sketch.Sketch(svg=SVG, count=2, grid=900))
    out = diagrams.render(v, 1.0, tmp_path / "d.mp4", words=[(0.0, "the"), (0.5, "pad")], frames=30)
    cap = cv2.VideoCapture(str(out))
    frames = []
    while (ok := cap.read())[0]:
        frames.append(ok[1])
    assert len(frames) == 30
    yellow = lambda f: (f[:, :, 0] < 120) & (f[:, :, 2] > 200)   # noqa: E731 (BGR)
    xs = [yellow(frames[k]).nonzero()[1].mean() for k in (5, 29)]
    assert xs[1] - xs[0] > 300   # still before "pad", moved across after it
