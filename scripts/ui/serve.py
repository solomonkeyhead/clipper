"""Clipper's real server on test data: one finished video from the ready-made script, plus a draft."""
import os, subprocess, sys
from pathlib import Path
data = Path(sys.argv[1]); data.mkdir(parents=True, exist_ok=True)
os.environ["CLIPPER_DATA_DIR"] = str(data)
from clipper.studio.server import create_app
from clipper.create import store
from clipper.studio import db, library
app = create_app()
from fastapi.testclient import TestClient
c = TestClient(app)
if not store.videos():
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
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=8799, log_level="warning")
