"""The Create page's endpoints (D108): ideas, scripts, the voice drop, the build.

Writing a script or a batch of ideas is one AI call or three, done while the page
waits (10-40 s). The voice drop and the build run in the background, one video
at a time, telling the page how far along they are ("create.changed").
"""

from __future__ import annotations

import asyncio
import functools
import math
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from ..create import channel as channels
from ..utils.logging import get_logger
from .jobs import failure

log = get_logger(__name__)

#: video id -> (stage, percent) while it's being timed or built.
progress: dict[int, tuple[str, float]] = {}
_lock = threading.Lock()
#: Videos with a build waiting or under way, and those the user asked to stop (D122).
_running: set[int] = set()
cancelled: set[int] = set()


class Cancelled(Exception):
    """The user pressed Cancel: the build stops at its next step."""


#: Videos deleted while their build was running: deleted when it stops (D123).
doomed: set[int] = set()


def _made_titles(ch, videos: list[dict]) -> list[str]:
    """Every title the channel has made: its videos, the clips filed under it (a Short can outlive its
    Create row), and its own example scripts."""
    from . import db

    with db.connect() as con:
        clips = [r[0] or "" for r in con.execute("SELECT title FROM clips WHERE campaign=?", (ch.campaign,))]
    return [(v.get("script") or {}).get("title", "") for v in videos] + clips + [e.get("title", "") for e in ch.examples]


def _delete(row: dict) -> None:
    """The video gone from Create: its row, and its folder (voice, clips, work) to the Recycle Bin.
    Its idea goes back on the list; a finished clip stays in Clips."""
    from ..create import store
    from ..create.voice import folder
    from ..utils.recycle import recycle
    from . import db

    with db.connect() as con:
        con.execute("DELETE FROM create_videos WHERE id=?", (row["id"],))
    if row["topic_id"]:
        store.set_topic(row["topic_id"], "new")
    d = folder(row["id"])
    if d.exists():
        recycle(d)


def unstick() -> int:
    """Builds left "voiced" or "building" when Clipper closed mid-build: put back, so they can be
    built again or deleted, never stuck (D123). Run when the server starts; returns how many."""
    from ..create import store

    stuck = [v for v in store.videos(every=True) if v["status"] in ("voiced", "building") and v["id"] not in _running]
    for v in stuck:
        _stopped(v["id"])
        if not v["clip_id"]:
            store.update_video(v["id"], error="Clipper was closed during the build. Press Try again to build it.")
    return len(stuck)


def _check(video_id: int) -> None:
    if video_id in cancelled:
        raise Cancelled


def _stopped(video_id: int) -> None:
    """Where a cancelled build leaves the video: its last finished version if it has one (the old
    file is only replaced at the very end of a build), else ready to build again."""
    from ..create import store

    row = store.video(video_id)
    if row and row["clip_id"]:
        store.update_video(video_id, status="built", error="")
    else:
        store.update_video(video_id, status="failed", error="Build cancelled. Press Try again to build it.")


def _posts(videos: list[dict]) -> dict[int, dict]:
    """video id -> {"posts": [...], "marked": bool} for each finished video that's posted: a post
    found by the syncs, or its clip marked posted in Clips (D131)."""
    from ..learn import log as perf
    from . import db, stats

    with_clip = [v for v in videos if v.get("clip_id")]
    if not with_clip:
        return {}
    try:
        found = stats.posts_by_clip(perf.read())
    except Exception as exc:  # an unreadable log: marked-posted still counts
        log.warning("create: couldn't read the post log (%s)", exc)
        found = {}
    out = {}
    with db.connect() as con:
        for v in with_clip:
            clip = db.clip(con, v["clip_id"])
            if not clip:
                continue
            posts = [{"platform": p["platform"], "url": p["url"], "posted_at": p.get("posted_at"),
                      "views": p.get("views_latest")}
                     for p in found.get((clip["campaign"], clip["source_id"], clip["clip_id"]), [])]
            marked = clip["status"] in ("posted", "submitted")
            if posts or marked:
                out[v["id"]] = {"posts": posts, "marked": marked}
    return out


def _archive_posted(videos: list[dict], posted: dict[int, dict]) -> None:
    """Posted videos move to the archive by themselves, once; one the user brought back stays (D131)."""
    from ..create import store
    from . import db

    for v in videos:
        if v["id"] in posted and not v.get("archived_at") and not v.get("archive_hold") \
                and v["status"] not in ("voiced", "building"):
            dates = sorted(str(p["posted_at"]) for p in posted[v["id"]]["posts"] if p.get("posted_at"))
            v["archived_at"] = dates[0][:16] if dates else db.now()
            store.update_video(v["id"], archived_at=v["archived_at"])


def _view() -> dict:
    from ..create import packs, store

    ch = channels.load()
    videos = store.videos()
    try:
        posted = _posts(videos)
        _archive_posted(videos, posted)
    except Exception as exc:  # never stop the page over the archive
        log.warning("create: couldn't check what's posted (%s)", exc)
        posted = {}
    from ..create import topics as topics_mod
    from ..create import userclips
    from ..create.script import Script

    videos = [v for v in videos if v["id"] not in doomed]
    for v in videos:
        stage = progress.get(v["id"])
        v["stage"], v["pct"] = (stage if stage else (None, None))
        timings = v.pop("timings", None)
        v["shots"] = _shots(v, timings)
        v["cancelling"] = v["id"] in cancelled
        v["archived"] = bool(v.get("archived_at"))
        v["posts"] = posted.get(v["id"], {}).get("posts", [])
        v["posted"] = v["id"] in posted
        try:  # the user's own clips for this video (D119)
            v["mine"] = userclips.view(v["id"], Script.model_validate(v["script"]))
        except ValueError:
            v["mine"] = {"auto": True, "fill": "auto", "clips": []}
    return {"channel": {"slug": ch.slug, "name": ch.name, "handle": ch.handle, "voice": ch.voice, "campaign": ch.campaign,
                        "words_per_second": ch.words_per_second, "pack": ch.pack, "drawings": ch.drawings,
                        "check_name": channels.check_name(ch),
                        "niche": ch.niche, "subject": ch.subject, "music": ch.music, "sfx": ch.sfx,
                        "poses": list(ch.poses)},
            "channels": [{"slug": c.slug, "name": c.name, "handle": c.handle, "pack": c.pack} for c in channels.all_channels()],
            "packs": [{"key": p.key, "label": p.label, "about": p.about, "drawings": p.drawings} for p in packs.PACKS.values()],
            # Ideas already made into a Short (or one of the channel's own examples) don't show (D154).
            "topics": [t for t in store.topics() if not topics_mod.made_already(t["question"], _made_titles(ch, videos))],
            "videos": videos}


def _shots(v: dict, timings: dict | None) -> list[dict]:
    """The pictures of a built video in order: which sentences each covers and when it plays,
    for reviewing them one by one (D125). Empty until the video has been timed."""
    from ..create.script import Script, spans

    if not timings or v["status"] not in ("built", "failed"):
        return []
    try:
        from ..create.voice import Timings, nudged

        script = Script.model_validate(v["script"])
        beats = nudged(script, Timings.model_validate(timings)).beats   # where the cuts fall, nudges and all (D171)
        return [{"beats": [k + 1 for k in g], "start": beats[g[0]][0], "end": beats[g[-1]][1]}
                for g in spans(script) if g[-1] < len(beats)]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        # Said on the page, never a list that quietly vanishes (D128).
        log.warning("create: video %s: parts can't be listed: %s", v.get("id"), exc)
        v["problem"] = f"The parts can't be listed: {str(exc).splitlines()[0][:200]}"
        return []


def _work(video_id: int, publish) -> None:
    """Time the voice to the script, then build the video; one at a time."""
    from ..create import build, store, voice
    from ..create.script import Script
    from ..ingest.probe import probe

    def step(stage: str, pct: float) -> None:
        _check(video_id)  # every step is a place a cancelled build stops
        progress[video_id] = (stage, pct)
        publish("create.changed", {"id": video_id})

    _running.add(video_id)
    try:
        with _lock:
            try:
                _check(video_id)  # cancelled while it waited for another build to finish
                row = store.video(video_id)
                channels.set_current(row["channel"])  # this video's channel, in this thread (D146)
                step("Listening to the voice", 3)
                path = Path(row["voice"])
                if row["timings"]:  # the same words and voice as last time: the same cuts (D125)
                    store.update_video(video_id, status="building", error="")
                else:
                    written = Script.model_validate(row["script"])
                    # Long pauses cut first (D155): the build then uses the tightened copy of the take.
                    path, words, seconds = voice.tighten(path, written, voice.heard(path), probe(path).duration)
                    timings = voice.align(written, words, seconds)
                    _check(video_id)
                    store.update_video(video_id, voice=str(path), timings=timings.model_dump(), status="building", error="")
                build.build(video_id, progress=step)
            except Cancelled:
                log.info("create: video %s build cancelled", video_id)
                _stopped(video_id)
            except Exception as exc:  # shown on the page
                log.warning("create: video %s failed: %s\n%s", video_id, exc, traceback.format_exc())
                before = store.video(video_id)
                # A rebuild that fails leaves the video built before in place, with the reason (D127).
                store.update_video(video_id, status="built" if before and before["clip_id"] else "failed",
                                   error=failure(exc))
            finally:
                progress.pop(video_id, None)
    finally:
        _running.discard(video_id)
        cancelled.discard(video_id)
        if video_id in doomed:  # deleted while it was building
            doomed.discard(video_id)
            from ..create import store

            row = store.video(video_id)
            if row:
                _delete(row)
        publish("create.changed", {"id": video_id})
        publish("clips.changed", {})


def _start(video_id: int, publish) -> None:
    threading.Thread(target=_work, args=(video_id, publish), name=f"create-{video_id}", daemon=True).start()


def _beat(body: dict, count: int | None = None) -> int:
    """The sentence number (from 1) a request is about; with `count`, it must be one of them."""
    try:
        beat = int(body.get("beat"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "beat is a sentence number") from exc
    if count is not None and not 1 <= beat <= count:
        raise HTTPException(400, "no such sentence")
    return beat


def _editable(row: dict) -> None:
    """The video can change unless it is being timed or built right now: a script edit or a new
    voice mid-build cleared the voice and timings under the running build (D132)."""
    if row["status"] in ("voiced", "building"):
        raise HTTPException(409, "it's being built: wait until it finishes (or press Cancel), then change it")


def routes(app: FastAPI, publish) -> None:
    try:
        if unstick():
            log.info("create: put back builds left unfinished when Clipper last closed")
    except Exception as exc:  # never stop the page from starting
        log.warning("create: couldn't check for unfinished builds (%s)", exc)

    from ..create import store
    from ..create.ai import CreateError

    def video_or_404(video_id: int) -> dict:
        found = store.video(video_id)
        if found is None:
            raise HTTPException(404, "no such video")
        channels.set_current(found["channel"])  # whatever it asks of the AI is in this video's channel's voice (D146)
        return found

    def ai(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CreateError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/create")
    def create_view() -> dict:
        return _view()

    # ---- channels (D146): any number, each made from a niche pack and then its own

    def _activate(slug: str) -> None:
        from . import db

        with db.connect() as con:
            db.set_setting(con, "create_channel", slug)

    @app.post("/api/create/channels")
    def channel_new(body: dict) -> dict:
        from ..create import packs

        name = " ".join(str(body.get("name") or "").split())
        if not name:
            raise HTTPException(400, "give the channel a name")
        slug = channels.slugify(name)
        if any(c.slug == slug for c in channels.all_channels()):
            raise HTTPException(409, "you already have a channel with that name")
        extra = {k: str(body[k]).strip() for k in ("voice",) if str(body.get(k) or "").strip()}
        channel = channels.make(str(body.get("pack") or packs.DEFAULT), name, handle=str(body.get("handle") or "").strip(),
                                niche=str(body.get("niche") or "").strip(), **extra)
        channels.save(channel)
        _activate(channel.slug)
        return {"slug": channel.slug}

    @app.get("/api/create/channels/{slug}")
    def channel_get(slug: str) -> dict:
        found = next((c for c in channels.all_channels() if c.slug == slug), None)
        if found is None:
            raise HTTPException(404, "no such channel")
        return found.model_dump(exclude={"examples"}) | {"examples": len(found.examples)}

    @app.put("/api/create/channels/{slug}")
    def channel_edit(slug: str, body: dict) -> dict:
        found = next((c for c in channels.all_channels() if c.slug == slug), None)
        if found is None:
            raise HTTPException(404, "no such channel")
        texts = ("name", "handle", "niche", "persona", "voice", "subject", "expert", "areas", "watermark",
                 "idea_focus", "idea_avoid", "script_focus", "script_avoid", "character", "signoff", "presenter")
        updates = {k: str(body[k]).strip() for k in texts if k in body}
        if "rules" in body:
            updates["rules"] = [str(r).strip() for r in body["rules"] if str(r).strip()]
        for flag in ("drawings", "music", "sfx"):
            if flag in body:
                updates[flag] = bool(body[flag])
        if "reactions" in body:   # D156: the character's reaction pictures, one path a line
            updates["reactions"] = [str(r).strip() for r in body["reactions"] if str(r).strip()]
        if "poses" in body:   # D158: "name = path" a line
            updates["poses"] = {n.strip().lower(): p.strip() for n, _, p in
                                (line.partition("=") for line in str(body["poses"]).splitlines()) if n.strip() and p.strip()}
        if "board" in body:
            from ..create.diagrams import PALETTES

            if body["board"] not in PALETTES:
                raise HTTPException(400, "unknown board style")
            updates["board"] = body["board"]
        if "words_per_second" in body:
            updates["words_per_second"] = max(1.2, min(4.0, float(body["words_per_second"])))
        channels.save(found.model_copy(update=updates))
        return {"slug": slug}

    @app.put("/api/create/channel/active")
    def channel_active(body: dict) -> dict:
        slug = str(body.get("slug") or "")
        if not any(c.slug == slug for c in channels.all_channels()):
            raise HTTPException(404, "no such channel")
        _activate(slug)
        return {"slug": slug}

    @app.delete("/api/create/channels/{slug}")
    def channel_delete(slug: str) -> dict:
        everything = store.videos(every=True)
        if any(v["channel"] == slug for v in everything):
            raise HTTPException(409, "this channel has videos: remove them from Create first")
        channels.delete(slug)
        return {"deleted": slug}

    @app.get("/api/create/ai")
    def create_ai() -> dict:
        """Which AI Create will ask, and why any didn't answer. Makes no model call, so the
        page can show it at all times: Claude or Gemini was a guess for two videos (D118)."""
        from ..config import Config
        from ..create import ai as create_ai_module

        config = Config.load()
        try:
            order = [b.describe() for b in create_ai_module.backends(config, job="sketch")]
            problem = ""
        except CreateError as exc:
            order, problem = [], str(exc)
        # Only problems from the last 15 minutes: an old one read as if it were still happening (D154).
        now = time.time()
        recent = {k: v for k, v in create_ai_module.misses.items()
                  if now - create_ai_module.missed_at.get(k, now) < 15 * 60}
        return {"order": order, "last_used": create_ai_module.last_used, "misses": recent,
                "missed_ago_s": {k: round(now - create_ai_module.missed_at.get(k, now)) for k in recent},
                # Who does each job now, Settings' picks included: the defaults said "Gemini does footage"
                # after Marc gave footage to Claude (D161).
                "claude_only": config.llm.create_claude_only,
                "gemini_jobs": [j for j in ("script", "check", "critic", "review", "footage", "place", "topics")
                                if create_ai_module.first_choice(config, j) == "gemini"],
                "paid_api_jobs": list(config.llm.paid_api_jobs),
                "problem": problem}

    @app.post("/api/create/ideas")
    async def create_ideas(body: dict | None = None) -> dict:
        from ..create import topics

        count = max(5, min(50, int((body or {}).get("count") or 20)))
        steer = str((body or {}).get("steer") or "").strip()[:400]
        return {"added": await asyncio.to_thread(ai, topics.generate, count, steer)}

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
        written.series = topic.get("series") or ""   # the idea's series, shown with the video (D155)
        return {"id": store.add_video(topic_id, written.model_dump(), notes)}

    @app.post("/api/create/videos/{video_id}/rewrite")
    async def create_rewrite(video_id: int, body: dict | None = None) -> dict:
        """Another take on the same question, steered by the owner's note if they gave one (D153)."""
        from ..create import script

        row = video_or_404(video_id)
        _editable(row)
        if not row["topic_id"]:  # the user's own script: no one to rewrite it from (D120)
            raise HTTPException(400, "this is your own script, so there's no other take to write; "
                                     "edit it, or use Plan pictures and Check physics")
        topic = store.topic(row["topic_id"])
        question = topic["question"] if topic else row["script"].get("title", "")
        take = int(row["script"].get("take", 1)) + 1
        steer = str((body or {}).get("steer") or "").strip()[:400]
        written, notes = await asyncio.to_thread(ai, script.write_checked, question,
                                                 topic["angle"] if topic else "", take=take, steer=steer)
        written.series = (topic or {}).get("series") or ""
        store.update_video(video_id, script={**written.model_dump(), "take": take}, check_notes=notes,
                           status="draft", voice="", timings="", error="")
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/sound")
    def create_sound(video_id: int, body: dict) -> dict:
        """Music and chalk sounds for one video (D156): true, false, or null for the channel's setting. The voice
        and timings stay; the next build uses it."""
        row = video_or_404(video_id)
        _editable(row)
        script = dict(row["script"])
        for k in ("music", "sfx"):
            if k in body:
                script[k] = None if body[k] is None else bool(body[k])
        store.update_video(video_id, script=script)
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/edit")
    def create_edit_style(video_id: int, body: dict) -> dict:
        """The edit's flourishes for one video (D165): zooms, cut-ins and captions on or off (D171), the transition
        (auto, cut, whip, zoom), and the cover's moment in seconds (null: the best still). The next build uses them."""
        row = video_or_404(video_id)
        _editable(row)
        script = dict(row["script"])
        for k in ("zooms", "cut_ins", "captions"):
            if k in body:
                script[k] = bool(body[k])
        if "cover" in body:
            try:
                script["cover"] = None if body["cover"] is None else round(max(0.0, float(body["cover"])), 2)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "cover is a time in seconds, or null for automatic") from exc
        if "transitions" in body:
            if body["transitions"] not in ("auto", "cut", "whip", "zoom"):
                raise HTTPException(400, "transitions are auto, cut, whip or zoom")
            script["transitions"] = body["transitions"]
        store.update_video(video_id, script=script)
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/script")
    def create_edit(video_id: int, body: dict) -> dict:
        """The script as edited on the page. A voice made for the old words no longer fits."""
        from ..create.script import Script, tidy

        row = video_or_404(video_id)
        _editable(row)
        try:
            edited = tidy(Script.model_validate({**row["script"], **(body.get("script") or {})}))
        except ValueError as exc:
            raise HTTPException(400, f"that script can't be read: {exc}") from exc
        # A new voice comes with new words: cuts nudged for the old one don't apply, nor a caption written for a
        # sentence that changed (D171).
        kept = {b.get("text") for b in row["script"].get("beats") or []}
        edited = edited.model_copy(update={"beats": [b.model_copy(update={"nudge": 0.0, "caption": b.caption if b.text in kept else ""})
                                                     for b in edited.beats]})
        status = "draft" if row["status"] in ("draft", "approved") else "approved"
        store.update_video(video_id, script={**edited.model_dump(), "take": row["script"].get("take", 1)},
                           status=status, voice="", timings="",
                           check_notes=row["check_notes"] + "\n(Edited by hand since.)"
                           if "(Edited by hand since.)" not in row["check_notes"] else row["check_notes"])
        return {"ok": True}

    @app.post("/api/create/videos/{video_id}/approve")
    def create_approve(video_id: int) -> dict:
        row = video_or_404(video_id)
        if row["status"] not in ("draft", "approved"):  # a built video set back to "approved" lost its place
            raise HTTPException(400, "only a draft script is approved")
        store.update_video(video_id, status="approved")
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/voice/{filename}")
    async def create_voice(video_id: int, filename: str, request: Request) -> dict:
        """The voiceover made on ElevenLabs, dropped in: kept, then timed and built in the background."""
        from ..create import voice

        row = video_or_404(video_id)
        _editable(row)  # a new take mid-build went to the Recycle Bin under the running build
        if row["status"] not in ("approved", "built", "failed"):
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
        store.update_video(video_id, voice=str(kept), status="voiced", error="", timings="")  # timed afresh
        _start(video_id, publish)
        return {"queued": True}

    @app.post("/api/create/videos/{video_id}/voice-none")
    def create_no_voice(video_id: int) -> dict:
        """No voiceover (D146): the words are timed at the channel's speaking pace, over a silent
        track, so the video is captions over its pictures."""
        from ..create import voice
        from ..create.script import Script
        from ..utils.recycle import recycle

        row = video_or_404(video_id)
        _editable(row)
        if row["status"] not in ("approved", "built", "failed"):
            raise HTTPException(400, "approve the script first")
        script = Script.model_validate(row["script"])
        words, seconds = voice.silent(script, channels.load().words_per_second)
        for old in voice.folder(video_id).glob("voice.*"):
            recycle(old)
        kept = voice.write_silence(voice.folder(video_id) / "voice.wav", seconds)
        timings = voice.align(script, words, seconds)
        store.update_video(video_id, voice=str(kept), status="voiced", error="", timings=timings.model_dump())
        _start(video_id, publish)
        return {"queued": True}

    @app.post("/api/create/videos/{video_id}/pictures")
    async def create_pictures(video_id: int) -> dict:
        """New pictures for the same words and voice, then built again."""
        from ..create import script
        from ..create.script import Script

        row = video_or_404(video_id)
        _editable(row)
        if not row["voice"]:
            raise HTTPException(400, "drop the voiceover in first")
        planned, notes = await asyncio.to_thread(ai, script.replan, Script.model_validate(row["script"]))
        store.update_video(video_id, script={**planned.model_dump(), "take": row["script"].get("take", 1)},
                           check_notes=notes, status="voiced", error="")
        _start(video_id, publish)
        return {"queued": True}

    @app.post("/api/create/videos/{video_id}/build")
    def create_build(video_id: int) -> dict:
        """Build again (after a failure, or to pick new footage)."""
        row = video_or_404(video_id)
        if not row["voice"]:
            raise HTTPException(400, "drop the voiceover in first")
        if row["status"] in ("voiced", "building"):
            raise HTTPException(409, "it's being built already")
        # Locked from now: the clips and placements can't change under a build that has begun (D119).
        store.update_video(video_id, status="voiced", error="")
        _start(video_id, publish)
        return {"queued": True}

    # ---------- the user's own clips (D119) ----------

    def _script(row: dict):
        from ..create.script import Script

        return Script.model_validate(row["script"])

    def _put_script(row: dict, script) -> None:
        """Saved only if it reads back: a change that would leave the script unreadable is refused
        with the reason, never saved to break the video (D128)."""
        from ..create.script import Script

        data = script.model_dump()
        try:
            Script.model_validate(data)
        except ValueError as exc:
            log.warning("create: refused to save video %s's script: %s", row["id"], exc)
            raise HTTPException(500, f"that change couldn't be saved: {str(exc).splitlines()[0][:200]}") from exc
        store.update_video(row["id"], script={**data, "take": row["script"].get("take", 1)})

    @app.put("/api/create/videos/{video_id}/clips/{filename}")
    async def create_clip_add(video_id: int, filename: str, request: Request) -> dict:
        """One of the user's own clips, dropped in: kept after checks (a video, readable, long enough)."""
        from ..create import userclips

        _editable(video_or_404(video_id))
        suffix = Path(filename).suffix.lower()
        if suffix not in userclips.EXTS:
            raise HTTPException(400, f"{userclips.clean_name(filename)} isn't a video file "
                                     f"({', '.join(sorted(userclips.EXTS))})")
        partial = userclips.mine_dir(video_id) / f"upload-{threading.get_ident()}-{id(request)}{suffix}.part"
        received = 0
        try:
            with partial.open("wb") as out:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > userclips.MAX_BYTES:
                        raise HTTPException(413, f"that file is over {userclips.MAX_BYTES // 1024**3} GB")
                    out.write(chunk)
        except BaseException:  # a dropped connection or a refused size: no half file left behind
            from ..utils.recycle import recycle

            if partial.exists():
                recycle(partial)
            raise
        try:
            entry = await asyncio.to_thread(userclips.add, video_id, partial, filename)
        except CreateError as exc:
            raise HTTPException(400, str(exc)) from exc
        publish("create.changed", {"id": video_id})
        return entry

    @app.get("/api/create/videos/{video_id}/clips/{clip_id}/thumb")
    def create_clip_thumb(video_id: int, clip_id: str):
        from fastapi.responses import FileResponse

        from ..create import userclips

        found = userclips.thumb_path(video_id, clip_id)
        if not found:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(found, media_type="image/jpeg")

    @app.get("/api/create/videos/{video_id}/clips/{clip_id}/file")
    def create_clip_file(video_id: int, clip_id: str):
        from fastapi.responses import FileResponse

        from ..create import userclips

        found = userclips.file_path(video_id, clip_id)
        if not found:
            raise HTTPException(404, "no such clip")
        return FileResponse(found)

    @app.delete("/api/create/videos/{video_id}/clips/{clip_id}")
    def create_clip_remove(video_id: int, clip_id: str) -> dict:
        """Take a clip out; any sentence using it goes back to its planned picture."""
        from ..create import userclips

        row = video_or_404(video_id)
        _editable(row)
        if not userclips.remove(video_id, clip_id):
            raise HTTPException(404, "no such clip")
        script, _ = userclips.forget(_script(row), set(userclips.files(video_id)))
        _put_script(row, script)
        return {"ok": True}

    @app.put("/api/create/videos/{video_id}/clips-settings")
    def create_clip_settings(video_id: int, body: dict) -> dict:
        from ..create import userclips

        _editable(video_or_404(video_id))
        fill = body.get("fill")
        auto = body.get("auto")
        if fill is not None and fill not in userclips.FILLS:
            raise HTTPException(400, f"fill must be one of {', '.join(userclips.FILLS)}")
        if auto is not None and not isinstance(auto, bool):
            raise HTTPException(400, "auto is true or false")
        userclips.settings(video_id, auto=auto, fill=fill)
        return {"ok": True}

    @app.post("/api/create/videos/{video_id}/place")
    async def create_place(video_id: int, body: dict | None = None) -> dict:
        """Place the clips on the sentences: `how` is "ai" (the model matches them), "order"
        (free, in the order added) or "clear" (every sentence back to its planned picture)."""
        from ..create import channel as channels
        from ..create import userclips

        row = video_or_404(video_id)
        _editable(row)
        body = body or {}
        how = body.get("how", "ai")
        if how not in ("ai", "order", "clear"):
            raise HTTPException(400, "how is ai, order or clear")
        script = _script(row)
        if how == "clear":
            _put_script(row, userclips.clear(script))
            note = "Every sentence is back to its planned picture."
        else:
            seconds = userclips.estimate(script, channels.load().words_per_second)
            try:
                placed, note = await asyncio.to_thread(
                    userclips.place, video_id, script, seconds, use_ai=how == "ai", strict=bool(body.get("strict")))
            except CreateError as exc:
                raise HTTPException(400, str(exc)) from exc
            _put_script(row, placed)
        store.update_video(video_id, check_notes=userclips.with_notes(row["check_notes"], [note]))
        publish("create.changed", {"id": video_id})
        return {"note": note}

    @app.put("/api/create/videos/{video_id}/placement")
    def create_placement(video_id: int, body: dict) -> dict:
        """One sentence's clip, by hand: `beat` (from 1), `clip` ("" = the planned picture),
        `start` (seconds into the clip, or null to carry on), `fill`."""
        from ..create import userclips

        row = video_or_404(video_id)
        _editable(row)
        beat = _beat(body)
        start = body.get("start")
        if start is not None:
            try:
                start = float(start)
            except (TypeError, ValueError) as exc:
                raise HTTPException(400, "start is a number of seconds") from exc
        known = {cid: dur for cid, (_, dur, _) in userclips.files(video_id).items()}
        try:
            script = userclips.set_beat(_script(row), beat, str(body.get("clip") or ""), start,
                                        str(body.get("fill") or "auto"), known)
        except CreateError as exc:
            raise HTTPException(400, str(exc)) from exc
        _put_script(row, script)
        publish("create.changed", {"id": video_id})
        return {"ok": True}

    # ---------- the user's own script (D120) ----------

    @app.post("/api/create/videos")
    async def create_own(body: dict) -> dict:
        """A script the user wrote: their words kept as they are, cut into sentences. With
        `plan`, the model also plans the pictures and checks the physics; if it can't, the
        script is kept anyway with plain footage searches and a note saying why."""
        from ..create import script as scripts

        tags = body.get("hashtags") or []
        if isinstance(tags, str):
            tags = tags.replace(",", " ").split()
        try:
            written = scripts.from_text(str(body.get("title") or ""), str(body.get("text") or ""),
                                        str(body.get("description") or ""), [str(t) for t in tags])
        except CreateError as exc:
            raise HTTPException(400, str(exc)) from exc
        notes = "Written by you."
        if body.get("plan"):
            try:
                written, planned = await asyncio.to_thread(scripts.replan, written)
                notes = f"Written by you.\n{planned}"
            except CreateError as exc:
                notes = f"Written by you. The pictures weren't planned ({exc}); press Plan pictures to try again."
        return {"id": store.add_video(None, written.model_dump(), notes)}

    @app.post("/api/create/videos/{video_id}/plan")
    async def create_plan(video_id: int) -> dict:
        """Plan the pictures for the script as it is now (sentences kept as written, pictures the
        user chose themselves kept too), with the physics check (the sketches are drawn when it is built, D136)."""
        from ..create import script as scripts

        row = video_or_404(video_id)
        _editable(row)
        if row["status"] not in ("draft", "approved"):
            raise HTTPException(400, "use New pictures on a finished video")
        planned, notes = await asyncio.to_thread(ai, scripts.replan, _script(row))
        store.update_video(video_id, script={**planned.model_dump(), "take": row["script"].get("take", 1)},
                           check_notes=notes)
        return {"ok": True}

    @app.post("/api/create/videos/{video_id}/check")
    async def create_check(video_id: int) -> dict:
        """The physics check on its own, as the script stands."""
        from ..create import ai as ai_module
        from ..create import script as scripts

        row = video_or_404(video_id)
        ai_module.misses.clear()
        review = await asyncio.to_thread(ai, scripts.check, _script(row))
        name = channels.check_name()
        notes = (f"{name}: no problems found." if review.ok or not review.problems
                 else f"{name}:\n" + "\n".join(f"- {p}" for p in review.problems))
        who = ai_module.last_used.split(":", 1)[-1] if ai_module.last_used else "unknown"
        store.update_video(video_id, check_notes=f"{notes}\nChecked by: {who}.")
        return {"notes": notes}

    # ---------- reviewing a finished video, picture by picture (D125) ----------

    @app.post("/api/create/videos/{video_id}/redo")
    def create_redo(video_id: int, body: dict) -> dict:
        """Ask for a new picture on one sentence of a finished video; everything else stays as
        it was built. `want`: "footage" (another stock clip; the one used is turned down),
        "drawing" (a new chalk drawing), or "undo" (back to what it had). `note`: optional words,
        used as the footage search or as what to draw."""
        from ..create.script import _query_from

        row = video_or_404(video_id)
        _editable(row)
        script = _script(row)
        beat = _beat(body, len(script.beats))
        want, note = body.get("want"), " ".join(str(body.get("note") or "").split())[:200]
        b = script.beats[beat - 1]
        v = b.visual
        if want == "undo":
            if not v.previous:
                raise HTTPException(400, "nothing to undo on that sentence")
            new = type(v).model_validate({**v.previous, "previous": None, "redo": False})
        elif want in ("footage", "drawing"):
            before = v.model_dump(exclude={"previous"})
            common = {"hold": False, "manual": True, "redo": True, "previous": v.previous or before, "clip": ""}
            if want == "footage":
                queries = ([note] if note else []) + [q for q in [*v.queries, v.query] if q.strip()]
                queries = list(dict.fromkeys(queries or [_query_from(b.text)]))[:3]
                new = v.model_copy(update={**common, "kind": "stock", "queries": queries, "query": queries[0], "wish": note,
                                           "card": v.card or b.emphasis or _query_from(b.text), "picked": [],
                                           "avoid": list(dict.fromkeys([*v.avoid, *(h.get("id") for h in v.picked)]))})
            else:
                idea = note or (v.idea if v.template == "sketch" else "") or \
                    f"A simple, clear chalk drawing of what this sentence shows: {b.text}"
                new = v.model_copy(update={**common, "kind": "diagram", "template": "sketch", "idea": idea,
                                           "sketch": None, "picked": []})
        else:
            raise HTTPException(400, "want is footage, drawing or undo")
        beats = list(script.beats)
        beats[beat - 1] = b.model_copy(update={"visual": new})
        _put_script(row, script.model_copy(update={"beats": beats}))
        publish("create.changed", {"id": video_id})
        return {"ok": True}

    # ---------- choosing footage for one part yourself (D129) ----------

    #: video id -> the candidates last offered for it, by id: only these can be chosen (the page
    #: sends ids, never addresses to download).
    offered: dict[int, dict[str, dict]] = {}

    def _part(row: dict, script, beat: int) -> tuple[list[int], float]:
        """The sentences of the picture that `beat` (from 1) starts or belongs to, and how long it plays."""
        from ..create.script import spans

        group = next((g for g in spans(script) if beat - 1 in g), [beat - 1])
        timings = row["timings"] or {}
        beats = timings.get("beats") or []
        if beats and group[-1] < len(beats):
            return group, beats[group[-1]][1] - beats[group[0]][0]
        return group, 4.0

    @app.post("/api/create/videos/{video_id}/footage")
    async def create_footage(video_id: int, body: dict) -> dict:
        """Footage to choose from for one part: searches written for it (and the user's words), both
        libraries, the clip it has now and clips used elsewhere in the video left out, every one scored."""
        from ..create import stock

        row = video_or_404(video_id)
        _editable(row)
        script = _script(row)
        beat = _beat(body, len(script.beats))
        group, seconds = _part(row, script, beat)
        root = script.beats[group[0]]
        wish = " ".join(str(body.get("wish") or "").split())[:200]
        text = " ".join(script.beats[k].text for k in group)
        exclude = {h.get("id") for k, b in enumerate(script.beats) for h in b.visual.picked} | set(root.visual.avoid)
        own = [q for q in [*root.visual.queries, root.visual.query] if q.strip()]

        def find() -> tuple[list[str], list[dict]]:
            planned = stock.plan_searches(text, script.text, wish=wish)
            queries = list(dict.fromkeys([*([wish] if wish else []), *planned, *own]))[:6] or [text]
            return queries, stock.candidates(queries, seconds, exclude, text, script.text)

        try:
            queries, found = await asyncio.to_thread(find)
        except CreateError as exc:
            raise HTTPException(400, str(exc)) from exc
        offered[video_id] = {str(h["id"]): h for h in found}
        return {"beat": group[0] + 1, "seconds": round(seconds, 2), "clips": max(1, math.ceil(seconds / 4.5 - 1e-6)),
                "searches": queries,
                "candidates": [{"id": str(h["id"]), "tags": h.get("tags", ""), "duration": h.get("duration", 0),
                                "tall": h["height"] > h["width"], "score": h.get("score"), "query": h.get("query", ""),
                                "preview": h.get("preview") or "",
                                "source": str(h["id"]).split("-", 1)[0] if "-" in str(h["id"]) else "pixabay"}
                               for h in found]}

    @app.post("/api/create/videos/{video_id}/footage/use")
    def create_footage_use(video_id: int, body: dict) -> dict:
        """Use the footage the user picked for a part (in the order picked) on the next build; the
        clip it had is turned down, and Undo brings it back."""
        row = video_or_404(video_id)
        _editable(row)
        script = _script(row)
        beat = _beat(body, len(script.beats))
        ids = [str(x) for x in (body.get("ids") or [])]
        pool = offered.get(video_id, {})
        if not ids:
            raise HTTPException(400, "pick at least one clip")
        if any(x not in pool for x in ids):
            raise HTTPException(400, "those clips aren't on offer any more: search again")
        group, _ = _part(row, script, beat)
        b = script.beats[group[0]]
        v = b.visual
        picks = [{k: pool[x].get(k) for k in ("id", "url", "width", "height", "duration", "tags", "thumb", "center")}
                 for x in dict.fromkeys(ids)][:8]
        new = v.model_copy(update={
            "kind": "stock", "picked": picks, "redo": True, "manual": True, "hold": False, "clip": "", "notice": "",
            "previous": v.previous or v.model_dump(exclude={"previous"}),
            "avoid": list(dict.fromkeys([*v.avoid, *(h.get("id") for h in v.picked)])),
            "queries": list(dict.fromkeys([q for q in [pool[ids[0]].get("query", ""), *v.queries] if q]))[:3] or v.queries,
            "card": v.card or b.emphasis})
        if new.queries:
            new = new.model_copy(update={"query": new.queries[0]})
        beats = list(script.beats)
        beats[group[0]] = b.model_copy(update={"visual": new})
        _put_script(row, script.model_copy(update={"beats": beats}))
        publish("create.changed", {"id": video_id})
        return {"ok": True}

    @app.post("/api/create/videos/{video_id}/part")
    @app.post("/api/create/videos/{video_id}/camera")
    def create_part(video_id: int, body: dict) -> dict:
        """One part of a built video set by hand (D164, D171), each field optional: the camera on its footage ("" the
        build's choice, else push, pull, drift, still), how it comes in (transition: "", cut, whip, zoom), a
        drawing's size (scale) and place (shift, + down), its words (title, labels; texts: a sketch's words in
        order), where the cut to it falls (nudge, seconds), and one sentence's caption (caption, for `beat`).
        Only what changed is made again on the next build; the footage and drawing stay."""
        from ..create.build import MOTIONS

        row = video_or_404(video_id)
        _editable(row)
        script = _script(row)
        beat = _beat(body, len(script.beats))
        group, _ = _part(row, script, beat)
        beats = list(script.beats)
        b = beats[group[0]]
        v = b.visual
        change: dict = {}
        if "camera" in body:
            camera = str(body.get("camera") or "")
            if camera and camera not in MOTIONS:
                raise HTTPException(400, f"camera is one of {', '.join(MOTIONS)}, or empty for automatic")
            change["camera"] = camera
        if "transition" in body:
            way = str(body.get("transition") or "")
            if way not in ("", "cut", "whip", "zoom"):
                raise HTTPException(400, "transition is cut, whip, zoom, or empty for automatic")
            change["transition"] = way
        try:
            if "scale" in body:
                change["scale"] = round(min(1.5, max(0.5, float(body["scale"]))), 2)
            if "shift" in body:
                change["shift"] = round(min(0.3, max(-0.3, float(body["shift"]))), 3)
            nudge = round(min(1.5, max(-1.5, float(body["nudge"]))), 2) if "nudge" in body else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "scale, shift and nudge are numbers") from exc
        words = lambda x: " ".join(str(x or "").split())[:80]   # noqa: E731
        if "title" in body:
            change["title"] = words(body["title"])
        if "labels" in body:
            change["labels"] = [words(x) for x in body.get("labels") or []][:8]
        if "texts" in body and v.sketch:
            texts = iter([words(x) for x in body.get("texts") or []])
            marks = [m.model_copy(update={"text": next(texts, m.text) or m.text}) if m.kind == "text" else m
                     for m in v.sketch.marks]
            change["sketch"] = v.sketch.model_copy(update={"marks": marks})
        updated = {k: x for k, x in change.items() if getattr(v, k) != x}
        if updated:
            beats[group[0]] = b.model_copy(update={"visual": v.model_copy(update={**updated, "restyle": True})})
        if nudge is not None and nudge != b.nudge:
            if group[0] == 0:
                raise HTTPException(400, "the first part starts with the video; its cut can't move")
            beats[group[0]] = beats[group[0]].model_copy(update={
                "nudge": nudge, "visual": beats[group[0]].visual.model_copy(update={"restyle": True})})
        if "caption" in body:
            text = " ".join(str(body.get("caption") or "").split())[:200]
            if text != beats[beat - 1].caption:
                beats[beat - 1] = beats[beat - 1].model_copy(update={"caption": text})
                root = beats[group[0]]
                beats[group[0]] = root.model_copy(update={"visual": root.visual.model_copy(update={"restyle": True})})
        _put_script(row, script.model_copy(update={"beats": beats}))
        publish("create.changed", {"id": video_id})
        return {"ok": True}

    @app.get("/api/create/stock-thumb/{clip_id}")
    def create_stock_thumb(clip_id: str):
        from fastapi.responses import FileResponse

        from ..create import stock

        found = stock.thumb_file(clip_id)
        if not found:
            raise HTTPException(404, "no thumbnail")
        return FileResponse(found, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/create/videos/{video_id}/still")
    def create_still(video_id: int, t: float):
        """One frame of the built video at `t` seconds, small: the review's picture of a shot."""
        import subprocess

        from fastapi.responses import Response

        from ..render.ffmpeg import ffmpeg_path
        from . import db, library

        row = video_or_404(video_id)
        if not row["clip_id"]:
            raise HTTPException(404, "not built yet")
        with db.connect() as con:
            found = con.execute("SELECT file FROM clips WHERE id=?", (row["clip_id"],)).fetchone()
        path = library.clip_path(found["file"]) if found else None
        if not path or not path.is_file():
            raise HTTPException(404, "the video file isn't there")
        try:
            jpg = subprocess.run([str(ffmpeg_path()), "-loglevel", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(path),
                                  "-frames:v", "1", "-vf", "scale=216:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                                 capture_output=True, timeout=30, check=True).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise HTTPException(500, "couldn't read a frame") from exc
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "max-age=60"})

    # ---------- ready-made scripts that ship with Clipper (D121) ----------

    @functools.cache
    def _ready() -> dict[str, dict]:
        import json as _json

        from ..create.script import Script

        found = {}
        folder = Path(__file__).resolve().parent.parent / "create" / "library"
        for path in sorted(folder.glob("*.json")):
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
                found[path.stem] = {"about": str(data.get("about", "")), "script": Script.model_validate(data["script"])}
            except (OSError, ValueError, KeyError):
                log.warning("create: unreadable ready-made script %s", path.name)
        return found

    @app.get("/api/create/ready")
    def create_ready() -> list[dict]:
        """Scripts that come with Clipper, written and drawn already: nothing to wait for. They are
        physics, so only a physics channel is offered them (D146)."""
        found = _ready() if channels.load().pack == "physics" else {}
        # An idea that is a ready-made script's question, reworded, isn't offered twice (D133).
        from difflib import SequenceMatcher

        titles = [r["script"].title.lower() for r in found.values()]
        for t in store.topics():
            if any(SequenceMatcher(None, t["question"].lower(), x).ratio() >= 0.75 for x in titles):
                store.set_topic(t["id"], "used")
        made: dict[str, list[dict]] = {}
        for v in store.videos():
            name = v.get("ready") or ""
            if not name and v["topic_id"] is None and v["check_notes"].startswith("A ready-made script"):
                # Made before videos named their ready-made script (D131): known by its title.
                name = next((n for n, r in found.items() if r["script"].title == v["script"].get("title")), "")
                if name:
                    store.update_video(v["id"], ready=name)
            if name:
                made.setdefault(name, []).append({"id": v["id"], "archived": bool(v.get("archived_at")),
                                                  "status": v["status"]})
        return [{"name": name, "title": r["script"].title, "about": r["about"], "words": r["script"].words,
                 "made": made.get(name, [])}
                for name, r in found.items()]

    @app.post("/api/create/ready/{name}")
    def create_from_ready(name: str) -> dict:
        found = _ready().get(name)
        if found is None:
            raise HTTPException(404, "no such ready-made script")
        return {"id": store.add_video(None, found["script"].model_dump(),
                                      "A ready-made script: the words and the pictures are already done. "
                                      "Footage is searched for when you build; edit anything you like.", ready=name)}

    @app.post("/api/create/videos/{video_id}/archive")
    def create_archive(video_id: int, body: dict) -> dict:
        """Into the archive or back out, by hand (D131). Posted videos go in by themselves; one
        brought back stays out, whatever is posted later, until it's archived again."""
        from . import db

        row = video_or_404(video_id)
        if body.get("archived", True):
            if row["status"] in ("voiced", "building"):
                raise HTTPException(409, "it's being built: archive it when the build is done")
            store.update_video(video_id, archived_at=db.now(), archive_hold=0)
        else:
            store.update_video(video_id, archived_at=None, archive_hold=1)
        publish("create.changed", {"id": video_id})
        return {"ok": True}

    @app.post("/api/create/videos/{video_id}/cancel")
    def create_cancel(video_id: int) -> dict:
        """Stop a build (D122). It stops at its next step, a few seconds at most unless the AI is
        mid-answer; the last finished version stays. A build that isn't running at all (Clipper
        was closed during it) is put back at once."""
        row = video_or_404(video_id)
        if row["status"] not in ("voiced", "building"):
            raise HTTPException(400, "it isn't being built")
        if video_id in _running:
            cancelled.add(video_id)
            publish("create.changed", {"id": video_id})
            return {"stopping": True}
        _stopped(video_id)
        publish("create.changed", {"id": video_id})
        return {"stopping": False}

    @app.delete("/api/create/videos/{video_id}")
    def create_delete(video_id: int, clip: bool = False) -> dict:
        """Remove a video from Create (its files to the Recycle Bin; a finished clip stays in Clips
        unless `clip` asks for it to go to Clips' trash too, D133), whatever state it is in: nothing
        can leave a video stuck on the page (D123)."""
        from . import db

        row = video_or_404(video_id)
        if clip and row.get("clip_id"):
            with db.connect() as con:
                db.trash(con, row["clip_id"], True)
            publish("clips.changed", {"id": row["clip_id"]})
        if video_id in _running:
            # Mid-build: stop it, and delete once it has let go of its files (D123).
            cancelled.add(video_id)
            doomed.add(video_id)
            publish("create.changed", {"id": video_id})
            return {"ok": True, "after_stop": True}
        _delete(row)
        return {"ok": True}
