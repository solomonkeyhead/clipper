"""Clips made again with a new on-screen hook (D90), or as edited in the
editor (D103), one at a time in the background.

The hook is burned into the video, so changing it means rendering the clip
again: same range, framing and edits (runner.rerender). The new file replaces
the old one in the library, which goes to the Recycle Bin; the clip keeps its id,
caption, ratings and notes, and its title becomes the new hook. Posted clips
aren't re-rendered: their video is what's live.
"""

from __future__ import annotations

import queue
import shutil
import threading

from ..utils.logging import get_logger
from . import db, library
from .jobs import failure

log = get_logger(__name__)


class Rerenders:
    def __init__(self, publish) -> None:
        self.publish = publish
        self._queue: queue.Queue[tuple[int, str, dict | None, str | None]] = queue.Queue()
        self._lock = threading.Lock()
        self.state: dict[int, str] = {}     # clip id -> "queued" | "rendering"
        self.failed: dict[int, str] = {}    # clip id -> why the last try failed
        self._worker: threading.Thread | None = None

    def submit(self, clip_id: int, hook: str, edit: dict | None = None, shape: str | None = None) -> None:
        with self._lock:
            if self.state.get(clip_id):
                return
            self.state[clip_id] = "queued"
            self.failed.pop(clip_id, None)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="rerender", daemon=True)
                self._worker.start()
        self._queue.put((clip_id, hook, edit, shape))
        self.publish("clips.changed", {"id": clip_id})

    def status(self, clip_id: int) -> str | None:
        return self.state.get(clip_id)

    def _run(self) -> None:
        while True:
            try:
                clip_id, hook, edit, shape = self._queue.get(timeout=60)
            except queue.Empty:
                return
            self.state[clip_id] = "rendering"
            self.publish("clips.changed", {"id": clip_id})
            try:
                self._one(clip_id, hook, edit, shape)
            except Exception as exc:  # shown on the clip; the old video stays
                log.warning("re-render of clip %s failed: %s", clip_id, exc)
                self.failed[clip_id] = failure(exc, 300)
            finally:
                self.state.pop(clip_id, None)
                self.publish("clips.changed", {"id": clip_id})

    def _one(self, clip_id: int, hook: str, edit: dict | None = None, shape: str | None = None) -> None:
        """`shape`: made again in another shape (D240), kept for its later re-renders."""
        import json

        from ..config import Config
        from ..editing import ClipEdit
        from ..paths import runs_dir
        from ..runner import rerender
        from ..utils.recycle import recycle
        from .server import load_campaigns

        with db.connect() as con:
            clip = db.clip(con, clip_id)
        if clip is None:
            return
        campaign = load_campaigns().get(clip["campaign"])
        if campaign is None:
            raise RuntimeError(f"its campaign {clip['campaign']!r} is gone")
        made = ClipEdit.model_validate(edit) if edit is not None else None
        new = rerender(clip, hook, config=Config.load(), campaign=campaign, out_root=runs_dir(), edit=made,
                       shape=shape)
        old = library.clip_path(clip["file"])
        if old.exists() and not recycle(old):
            raise RuntimeError("couldn't move the old video to the Recycle Bin")
        shutil.move(str(new), str(old))
        captions = library.captions_path(old)   # the old captions go with the old video; the new ones replace them
        if captions.exists():
            recycle(captions)
        library.move_captions(new, old)
        changes: dict = {"hook": hook, "title": hook or clip["title"]}
        if shape:
            changes["scores"] = json.dumps({**json.loads(clip.get("scores") or "{}"), "shape": shape})
            clip = {**clip, "scores": changes["scores"]}
        if made is not None:  # the clip is now the edit: its range, length and pieces
            scores = {**json.loads(clip.get("scores") or "{}"), "edit": made.model_dump()}
            changes |= {"start_s": made.start, "end_s": made.end, "duration_s": round(made.length, 2),
                        "scores": json.dumps(scores)}
        with db.connect() as con:
            db.update_clip(con, clip_id, **changes)
        log.info("clip %s re-rendered (%s)", clip_id, "edited" if made else f"hook {hook!r}")


_runner: Rerenders | None = None


def start(publish) -> Rerenders:
    """The Control Center's one re-render queue."""
    global _runner
    _runner = Rerenders(publish)
    return _runner


def state_of(clip_id: int) -> tuple[str | None, str | None]:
    """(queued | rendering | None, why the last try failed | None) for a clip."""
    if _runner is None:
        return None, None
    return _runner.status(clip_id), _runner.failed.get(clip_id)
