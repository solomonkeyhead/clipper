"""The clip editor's endpoints (D103): open a video or a clip, tighten, preview and save an edit. Moved out of
server.py when it passed 2,200 lines. Wired in by `routes`, like create_api."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import db, server
from . import editor as clip_editor
from .api_models import EditorView, EditorWord, EditRule


def routes(app: FastAPI, publish, *, jobs, rerenders) -> None:
    """`jobs`: the clipping queue (preparing a video is a job); `rerenders`: the clip re-render queue."""

    EDIT_LABELS = {"internal_cuts": "Cuts inside the clip", "visual_effects": "Zooms",
                   "added_text": "On-screen hook", "captions": "Captions",
                   "overlays": "B-roll and overlays", "audio_additions": "Music and sound effects",
                   "re_edit": "Reordering scenes"}

    def editor_campaign(name: str):
        campaign = server.load_campaigns().get(name)
        if campaign is None:
            raise HTTPException(400, "pick a campaign")
        return campaign

    def editor_clip(clip_id: int) -> dict:
        with db.connect() as con:
            row = db.clip(con, clip_id)
        if row is None:
            raise HTTPException(404, "no such clip")
        return row

    def editor_edit(body: dict, duration: float, campaign):
        from ..campaign.edits import permissions
        from ..editing import ClipEdit, problems

        try:
            edit = ClipEdit.model_validate(body.get("edit") or {}).tidy(duration)
        except ValueError as exc:
            raise HTTPException(400, f"that edit can't be read: {exc}") from exc
        wrong = problems(edit, permissions(campaign), campaign)
        if wrong:
            raise HTTPException(400, "; ".join(wrong).capitalize())
        return edit

    @app.get("/api/editor")
    def editor_view(campaign: str = "", source: str = "", clip: int | None = None) -> EditorView:
        """A video to edit: a library clip (`clip`), or a source video and campaign."""
        from ..campaign.edits import CLASSES, permissions
        from ..editing import blocked
        from ..ingest.download import load_info
        from ..utils.cache import source_id_for_file

        row = editor_clip(clip) if clip is not None else None
        if row is not None:
            campaign, sid, path = row["campaign"], row["source_id"], ""
        else:
            target = server.allowed_source(source)
            sid, path = source_id_for_file(target), str(target)
        found = editor_campaign(campaign)
        if not clip_editor.prepared(sid):
            if row is not None:
                raise HTTPException(409, "this clip's working files are gone; clip its video again instead")
            return EditorView(source_id=sid, prepared=False, source=path, name=Path(path).name,
                              campaign=found.name)
        info = load_info(sid)
        if not Path(info.media.path).exists():
            raise HTTPException(409, f"its video isn't on this PC any more ({Path(info.media.path).name})")
        clip_editor.make_proxy(sid, publish)
        perms = permissions(found)
        no = blocked(perms)
        edit = None
        if row is not None:
            saved = (json.loads(row.get("scores") or "{}") or {}).get("edit")
            start, end = row.get("start_s"), row.get("end_s")
            if start is None or end is None:
                # Clips filed before their range was kept: the start is in the id, the length known.
                from ..utils.timecode import from_slug_timestamp

                start = from_slug_timestamp(row.get("clip_id") or "")
                end = start + float(row.get("duration_s") or 30) if start is not None else None
            pieces = [{"start": start, "end": end, "zoom": 1.0}] if start is not None and end is not None else []
            edit = saved or {"pieces": pieces, "hook": row.get("hook") or "", "fixes": []}
        return EditorView(
            source_id=sid, prepared=True, source=info.media.path, name=info.title or Path(info.media.path).name,
            campaign=found.name, duration=info.media.duration, fps=info.media.fps or 30.0,
            has_audio=info.media.has_audio, proxy_ready=clip_editor.proxy_ready(sid),
            words=[EditorWord(**w) for w in clip_editor.words(sid)],
            rules=[EditRule(key=c, label=EDIT_LABELS.get(c, c), allowed=perms.allowed[c], blocked=c in no,
                            why=perms.reasons[c]) for c in CLASSES],
            min_seconds=found.duration.min_seconds, max_seconds=found.duration.max_seconds,
            hooks=list(found.hook_texts), clip_id=clip, clip_status=row["status"] if row else None, edit=edit)

    @app.post("/api/editor/prepare")
    def editor_prepare(body: dict) -> dict:
        """Read and transcribe a video so the editor can open it: a job, with progress."""
        found = editor_campaign(str(body.get("campaign") or ""))
        target = server.allowed_source(str(body.get("source") or ""))
        return jobs.submit(found, str(target), None, prepare=True).view()

    @app.get("/api/editor/{source_id}/proxy")
    def editor_proxy(source_id: str) -> FileResponse:
        path = clip_editor.proxy_path(source_id)
        if not path.is_file():
            raise HTTPException(404, "the editor's copy isn't ready yet")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/api/editor/{source_id}/peaks")
    def editor_peaks(source_id: str) -> list[int]:
        if not clip_editor.prepared(source_id):
            raise HTTPException(404, "no such video")
        return clip_editor.peaks(source_id)

    @app.post("/api/editor/tighten")
    def editor_tighten(body: dict) -> dict:
        """The edit with its pauses and fillers cut, as ordinary cuts to adjust or undo."""
        from ..campaign.edits import permissions
        from ..editing import ClipEdit, blocked, tighten
        from ..ingest.download import load_info
        from ..models import Transcript
        from ..paths import work_dir
        from ..runner import _loudness

        sid = str(body.get("source_id") or "")
        if not clip_editor.prepared(sid):
            raise HTTPException(404, "no such video")
        found = editor_campaign(str(body.get("campaign") or ""))
        no = blocked(permissions(found))
        if "internal_cuts" in no:
            raise HTTPException(400, f"No cuts inside the clip: {no['internal_cuts']}")
        info = load_info(sid)
        edit = ClipEdit.model_validate(body.get("edit") or {}).tidy(info.media.duration)
        words = Transcript.load(work_dir(sid) / "transcript.json").words
        tightened, cuts = tighten(edit, words, _loudness(info), min_length=found.duration.min_seconds or 0.0)
        return {"edit": tightened.model_dump(), "removed": round(edit.length - tightened.length, 2),
                "cuts": [{"start": c.start, "end": c.end, "why": c.reason} for c in cuts]}

    @app.post("/api/editor/preview")
    def editor_preview(body: dict) -> dict:
        """A quick draft render of the edit, through the real pipeline: exactly the
        framing, captions and hook the clip will have, smaller and faster."""
        from ..config import Config
        from ..ingest.download import load_info
        from ..paths import runs_dir
        from ..runner import RerenderError
        from ..runner import rerender as render_again

        sid = str(body.get("source_id") or "")
        if not clip_editor.prepared(sid):
            raise HTTPException(404, "no such video")
        found = editor_campaign(str(body.get("campaign") or ""))
        info = load_info(sid)
        edit = editor_edit(body, info.media.duration, found)
        base = editor_clip(int(body["clip"])) if body.get("clip") is not None else {"scores": "{}"}
        clip = {**base, "source_id": sid, "clip_id": "preview", "start_s": edit.start, "end_s": edit.end}
        clip_editor.clear_previews(sid)
        try:
            made = render_again(clip, edit.hook, config=Config.load(), campaign=found, out_root=runs_dir(),
                                edit=edit, draft=True)
        except RerenderError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"url": f"/api/editor/{sid}/preview/{made.name}", "length": round(edit.length, 2)}

    @app.get("/api/editor/{source_id}/preview/{name}")
    def editor_preview_file(source_id: str, name: str) -> FileResponse:
        path = clip_editor.preview_dir(source_id) / Path(name).name
        if path.suffix != ".mp4" or not path.is_file():
            raise HTTPException(404, "that preview is gone; make another")
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/editor/save")
    def editor_save(body: dict) -> dict:
        """Render the edit for real: over its clip (`clip`), or as a new clip."""
        from ..ingest.download import load_info
        from . import footage

        sid = str(body.get("source_id") or "")
        if not clip_editor.prepared(sid):
            raise HTTPException(404, "no such video")
        found = editor_campaign(str(body.get("campaign") or ""))
        info = load_info(sid)
        edit = editor_edit(body, info.media.duration, found)
        if body.get("clip") is not None:
            clip_id = int(body["clip"])
            row = editor_clip(clip_id)
            if row["status"] in ("posted", "submitted"):
                raise HTTPException(400, "it's posted: its video is what's live")
            rerenders.submit(clip_id, edit.hook, edit.model_dump())
            return {"queued": True, "clip": clip_id}
        source = str(server.allowed_source(info.media.path))
        footage.remember([source], found.name, "clipped")
        return {"queued": True, "job": jobs.submit(found, source, None, edits=[edit.model_dump()]).view()}
