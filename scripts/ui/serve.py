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
    store.update_video(vid, status="built", voice=str(data / "v.mp3"), clip_id=clip_id,
                       timings={"words": [], "beats": [(i * 4.0, i * 4.0 + 4.0) for i in range(n)], "duration": n * 4.0, "matched": 1.0})
    c.post("/api/create/ready/voice-on-a-recording")   # a second video, still a draft: the "active" one
import uvicorn
uvicorn.run(app, host="127.0.0.1", port=8799, log_level="warning")
