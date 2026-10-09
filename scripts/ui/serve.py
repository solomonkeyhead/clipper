"""Clipper's real server on test data: one finished video from the ready-made script, a draft, and a posted one."""
import os, subprocess, sys
from pathlib import Path
# A build's drawing workers import this file again (D179): only a run of it sets anything up.
if __name__ == "__main__":
    data = Path(sys.argv[1]); data.mkdir(parents=True, exist_ok=True)
    os.environ["CLIPPER_DATA_DIR"] = str(data)
    from clipper.studio.server import create_app
    from clipper.create import store
    from clipper.studio import db, library
    app = create_app()
    from fastapi.testclient import TestClient
    c = TestClient(app)
    if not store.videos():
        # Channels live in the data folder since D145: the Professor's, as Marc has it.
        from clipper.create import channel as _channel
        _channel.save(_channel.make("physics", "German Professor"))
        vid = c.post("/api/create/ready/voice-on-a-recording").json()["id"]
        n = len(store.video(vid)["script"]["beats"])
        rel = "german-professor/001_voice.mp4"
        dest = library.clip_path(rel); dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30",
                        "-t", str(n * 4), str(dest)], check=True)
        with db.connect() as con:
            clip_id = db.upsert_clip(con, {"campaign": "german-professor", "source_id": "create", "clip_id": f"create-{vid}",
                                           "title": "Voice", "file": rel, "caption": "c"})
        # What a real build leaves behind (D128): the footage each part used, with Pixabay's number ids
        # and Pexels' "pexels-..." ids, as remember() writes them.
        from clipper.create.script import Script
        script = Script.model_validate(store.video(vid)["script"])
        beats = []
        for k, b in enumerate(script.beats):
            if b.visual.kind == "stock" and not b.visual.hold:
                pick = {"id": f"pexels-{k}" if k == 1 else 4000000 + k, "url": "https://example.invalid/v.mp4", "width": 1080,
                        "height": 1920, "duration": 9, "tags": "microphone, studio", "thumb": "", "center": 0.5}
                b = b.model_copy(update={"visual": b.visual.model_copy(update={"picked": [pick]})})
            beats.append(b)
        store.update_video(vid, script={**script.model_copy(update={"beats": beats}).model_dump(), "take": 1})
        store.update_video(vid, status="built", voice=str(data / "v.mp3"), clip_id=clip_id,
                           timings={"words": [], "beats": [(i * 4.0, i * 4.0 + 4.0) for i in range(n)], "duration": n * 4.0, "matched": 1.0})
        c.post("/api/create/ready/voice-on-a-recording")   # a second video, still a draft: the "active" one
        # A small WebM for the footage previews: the open-source Chromium the walkthrough uses can't play
        # H.264 (Chrome and Edge can), so the hover test plays this instead (D130).
        rel2 = "german-professor/preview.webm"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=270x480:rate=25",
                        "-t", "6", "-c:v", "libvpx-vp9", "-b:v", "300k", str(library.clip_path(rel2))], check=True)
        with db.connect() as con:
            db.upsert_clip(con, {"campaign": "german-professor", "source_id": "test", "clip_id": "preview",
                                 "title": "Preview", "file": rel2, "caption": "c"})
        # A third, posted: marked posted in Clips, so it moves to the Archive by itself (D131).
        posted = c.post("/api/create/ready/voice-on-a-recording").json()["id"]
        with db.connect() as con:
            pclip = db.upsert_clip(con, {"campaign": "german-professor", "source_id": "create", "clip_id": f"create-{posted}",
                                         "title": "Voice (posted)", "file": rel, "caption": "c"})
            db.update_clip(con, pclip, status="posted")
        store.update_video(posted, status="built", voice=str(data / "v.mp3"), clip_id=pclip,
                           timings={"words": [], "beats": [(i * 4.0, i * 4.0 + 4.0) for i in range(n)], "duration": n * 4.0, "matched": 1.0})
    # Stand-ins for the footage libraries and the footage model (no keys or network here): every search
    # gives 8 clips with thumbnails, and the "model" writes searches and scores clips 9 down to 2 (D129).
    import json as _json
    from PIL import Image as _Image
    from clipper.create import stock as _stock
    _thumbs = _stock._dir() / "thumbs"
    _thumbs.mkdir(parents=True, exist_ok=True)


    def _fake_search(query):
        hits = []
        for k in range(8):
            cid = f"pexels-{abs(hash(query)) % 1000}{k}" if k % 3 == 0 else 5000000 + (abs(hash(query)) % 1000) * 10 + k
            if not (_thumbs / f"{cid}.jpg").exists():
                _Image.new("RGB", (180, 320), ((k * 40) % 255, 90, 200 - k * 20)).save(_thumbs / f"{cid}.jpg")
            hits.append({"id": cid, "duration": 10, "tags": f"{query}, clip {k}", "url": "https://example.invalid/v.mp4",
                         "width": 1080, "height": 1920, "thumb": "x", "preview": "/media/2"})   # a real, playable video
        return hits


    def _fake_ask(system, user, schema, **kw):
        if schema.__name__ == "_Searches":
            return _json.dumps({"searches": ["man looking in mirror", "woman smiling at reflection", "bathroom mirror"]})
        if schema.__name__ == "_Ranks":
            n = user.count("\n") + 1
            return _json.dumps({"scores": [max(2, 9 - k) for k in range(n)], "centers": [0.5] * n})
        raise _stock.CreateError("no model in the test setup")


    def _fake_photos(query):
        """One photo per search (D162), to see the picker label it."""
        cid = f"photo-pixabay-{abs(hash(query)) % 1000}"
        if not (_thumbs / f"{cid}.jpg").exists():
            _Image.new("RGB", (320, 213), (200, 120, 60)).save(_thumbs / f"{cid}.jpg")
        return [{"id": cid, "duration": 0, "tags": f"{query}, photo", "url": "https://example.invalid/p.jpg",
                 "width": 1280, "height": 853, "thumb": "x", "preview": ""}]


    _stock.search = _fake_search
    _stock.photos = _fake_photos
    _stock.ask = _fake_ask

    # A slow stand-in for the writer (D188): the page shows "writing" with its steps, then the draft.
    import time as _time
    from clipper.create import script as _script

    def _fake_write(question, angle="", *, take=1, steer="", progress=None):
        for stage, pct in (("Writing the script", 5), ("The editor's read and the physics check", 30), ("Planning the pictures", 70)):
            if progress:
                progress(stage, pct)
            _time.sleep(2)
        made = _script.Script.model_validate(store.videos(every=True)[-1]["script"])
        return made.model_copy(update={"title": question}), "note"

    _script.write_checked = _fake_write
    if not store.topics():   # an idea to write from
        store.add_topics([{"question": "Why does a spoon flip your reflection?", "angle": "concave mirror", "felt": True}])

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8799, log_level="warning")
