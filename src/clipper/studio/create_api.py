"""The Create page's endpoints (D108): ideas, scripts, the voice drop, the build.

Writing a script or a batch of ideas is one AI call or three, done while the page
waits (10-40 s). The voice drop and the build run in the background, one video
at a time, telling the page how far along they are ("create.changed").
"""

from __future__ import annotations

import asyncio
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from ..utils.logging import get_logger

log = get_logger(__name__)

#: video id -> (stage, percent) while it's being timed or built.
progress: dict[int, tuple[str, float]] = {}
_lock = threading.Lock()


def _view() -> dict:
    from ..create import channel as channels
    from ..create import store

    ch = channels.load()
    videos = store.videos()
    for v in videos:
        stage = progress.get(v["id"])
        v["stage"], v["pct"] = (stage if stage else (None, None))
        v.pop("timings", None)
    return {"channel": {"name": ch.name, "handle": ch.handle, "voice": ch.voice, "campaign": ch.campaign,
                        "words_per_second": ch.words_per_second},
            "topics": store.topics(), "videos": videos}


def _work(video_id: int, publish) -> None:
    """Time the voice to the script, then build the video; one at a time."""
    from ..create import build, store, voice
    from ..create.script import Script
    from ..ingest.probe import probe

    def step(stage: str, pct: float) -> None:
        progress[video_id] = (stage, pct)
        publish("create.changed", {"id": video_id})

    with _lock:
        try:
            row = store.video(video_id)
            step("Listening to the voice", 3)
            path = Path(row["voice"])
            timings = voice.align(Script.model_validate(row["script"]), voice.heard(path), probe(path).duration)
            store.update_video(video_id, timings=timings.model_dump(), status="building", error="")
            build.build(video_id, progress=step)
        except Exception as exc:  # shown on the page
            log.warning("create: video %s failed: %s\n%s", video_id, exc, traceback.format_exc())
            store.update_video(video_id, status="failed", error=str(exc)[:400])
        finally:
            progress.pop(video_id, None)
            publish("create.changed", {"id": video_id})
            publish("clips.changed", {})


def routes(app: FastAPI, publish) -> None:
    from ..create import store
    from ..create.ai import CreateError

    def video_or_404(video_id: int) -> dict:
        found = store.video(video_id)
        if found is None:
            raise HTTPException(404, "no such video")
        return found

    def ai(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CreateError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/create")
    def create_view() -> dict:
        return _view()

    @app.post("/api/create/ideas")
    async def create_ideas(body: dict | None = None) -> dict:
        from ..create import topics

        count = max(5, min(50, int((body or {}).get("count") or 20)))
        return {"added": await asyncio.to_thread(ai, topics.generate, count)}

    @app.post("/api/create/topics/{topic_id}/skip")
    def create_skip(topic_id: int) -> dict:
        store.set_topic(topic_id, "skipped")
        return {"ok": True}

    @app.post("/api/create/topics/{topic_id}/script")
    async def create_script(topic_id: int) -> dict:
        from ..create import script

        topic = store.topic(topic_id)
        if topic is None:
            raise HTTPException(404, "no such idea")
        written, notes = await asyncio.to_thread(ai, script.write_checked, topic["question"], topic["angle"])
        store.set_topic(topic_id, "used")
        return {"id": store.add_video(topic_id, written.model_dump(), notes)}

    @app.post("/api/create/videos/{video_id}/rewrite")
    async def create_rewrite(video_id: int) -> dict:
        """Another take on the same question."""
        from ..create import script

        row = video_or_404(video_id)
        topic = store.topic(row["topic_id"]) if row["topic_id"] else None
        question = topic["question"] if topic else row["script"].get("title", "")
        take = int(row["script"].get("take", 1)) + 1
        written, notes = await asyncio.to_thread(ai, script.write_checked, question,
                                                 topic["angle"] if topic else "", take=take)
        store.update_video(video_id, script={**written.model_dump(), "take": take}, check_notes=notes,
                           status="draft", voice="", timings="", error="")
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/script")
    def create_edit(video_id: int, body: dict) -> dict:
        """The script as edited on the page. A voice made for the old words no longer fits."""
        from ..create.script import Script, tidy

        row = video_or_404(video_id)
        try:
            edited = tidy(Script.model_validate({**row["script"], **(body.get("script") or {})}))
        except ValueError as exc:
            raise HTTPException(400, f"that script can't be read: {exc}") from exc
        status = "draft" if row["status"] in ("draft", "approved") else "approved"
        store.update_video(video_id, script={**edited.model_dump(), "take": row["script"].get("take", 1)},
                           status=status, voice="", timings="",
                           check_notes=row["check_notes"] + "\n(Edited by hand since.)"
                           if "(Edited by hand since.)" not in row["check_notes"] else row["check_notes"])
        return {"ok": True}

    @app.post("/api/create/videos/{video_id}/approve")
    def create_approve(video_id: int) -> dict:
        video_or_404(video_id)
        store.update_video(video_id, status="approved")
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/voice/{filename}")
    async def create_voice(video_id: int, filename: str, request: Request) -> dict:
        """The voiceover made on ElevenLabs, dropped in: kept, then timed and built in the background."""
        from ..create import voice

        row = video_or_404(video_id)
        if row["status"] not in ("approved", "voiced", "built", "failed"):
            raise HTTPException(400, "approve the script first: the voice is made from it")
        suffix = Path(filename).suffix.lower()
        if suffix not in voice.AUDIO:
            raise HTTPException(400, f"that isn't an audio file ({', '.join(sorted(voice.AUDIO))})")
        from ..utils.recycle import recycle

        for old in voice.folder(video_id).glob("voice.*"):  # an earlier take goes to the Recycle Bin
            recycle(old)
        kept = voice.folder(video_id) / f"voice{suffix}"
        partial = kept.with_name(kept.name + ".part")
        with partial.open("wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
        partial.replace(kept)
        store.update_video(video_id, voice=str(kept), status="voiced", error="")
        threading.Thread(target=_work, args=(video_id, publish), name=f"create-{video_id}", daemon=True).start()
        return {"queued": True}

    @app.post("/api/create/videos/{video_id}/build")
    def create_build(video_id: int) -> dict:
        """Build again (after a failure, or to pick new footage)."""
        row = video_or_404(video_id)
        if not row["voice"]:
            raise HTTPException(400, "drop the voiceover in first")
        threading.Thread(target=_work, args=(video_id, publish), name=f"create-{video_id}", daemon=True).start()
        return {"queued": True}

    @app.delete("/api/create/videos/{video_id}")
    def create_delete(video_id: int) -> dict:
        """Remove a video from Create (its files to the Recycle Bin; a finished clip stays in Clips)."""
        from ..create.voice import folder
        from ..utils.recycle import recycle
        from . import db

        row = video_or_404(video_id)
        with db.connect() as con:
            con.execute("DELETE FROM create_videos WHERE id=?", (video_id,))
        if row["topic_id"]:
            store.set_topic(row["topic_id"], "new")
        d = folder(video_id)
        if d.exists():
            recycle(d)
        return {"ok": True}
