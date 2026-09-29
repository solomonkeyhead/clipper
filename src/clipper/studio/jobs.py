"""Clipping jobs started from the Control Center, one at a time, with live progress.

A job is `runner.run` on a campaign and a source video, the same as `clipper run`
on the command line, or `runner.cut` for hand-picked ranges (`clipper cut`); its
clips land in the library like any run's. In "auto" mode Clipper decides how
many: every moment that clears the quality bar, up to the campaign's maximum. Progress
comes from the pipeline's own log lines (ingest, transcribe, score, render k of
N), mapped to a stage and a percentage and pushed to open pages as
`job.progress` events -- the research's rule for waits over 10 seconds: show
real stages and a percent-done bar.
"""

from __future__ import annotations

import itertools
import logging
import queue
import re
import threading
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class Job:
    id: int
    campaign: str
    source: str
    top: int | None                 # None: Clipper decides (auto)
    mode: str = "auto"              # auto | top | manual
    ranges: list[tuple[float, float]] = field(default_factory=list)
    status: str = "queued"          # queued | running | done | failed
    stage: str = "Waiting to start"
    pct: float = 0.0
    clips: int = 0
    message: str = ""
    created: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    finished: str = ""

    @property
    def name(self) -> str:
        return Path(self.source).name

    def view(self) -> dict:
        return {**asdict(self), "name": self.name}


#: (log text pattern, stage shown, percent reached)
STAGES = [
    (r"^ingested|reusing ingest", "Reading the video", 6),
    (r"^loaded .* in", "Transcribing (a few minutes for an episode)", 10),
    (r"^transcribed|reusing transcript", "Transcribed", 40),
    (r"^cut scan", "Finding scenes", 45),
    (r"scenes \(", "Finding scenes", 50),
    (r"^generated \d+ candidates", "Scoring moments", 55),
    (r"^scoring \d+ candidates", "Scoring moments", 58),
    (r"^selected \d+ clip", "Picked the moments", 68),
]
RENDER_FROM, RENDER_TO = 70, 98


class _Progress(logging.Handler):
    """Turns the pipeline's INFO lines for one job into stage + percent."""

    def __init__(self, job: Job, publish) -> None:
        super().__init__(logging.INFO)
        self.job, self.publish = job, publish
        self.total, self.done = 0, 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage().strip()
        except Exception:
            return
        job = self.job
        for pattern, stage, pct in STAGES:
            if re.search(pattern, text):
                job.stage, job.pct = stage, max(job.pct, pct)
                break
        selected = re.match(r"^selected (\d+) clip", text)
        if selected:
            self.total = int(selected.group(1))
        if re.search(r" (accepted|rejected): ", text):
            self.done += 1
            total = max(self.total, self.done)
            job.pct = max(job.pct, RENDER_FROM + (RENDER_TO - RENDER_FROM) * self.done / total)
            job.stage = f"Rendered {self.done} of {total}"
        elif re.search(r" rendered in ", text) or re.search(r"tightened:", text):
            job.stage = f"Rendering clip {self.done + 1} of {max(self.total, self.done + 1)}"
            job.pct = max(job.pct, RENDER_FROM)
        self.publish("job.progress", job.view())


def explain_stop(note: str, made: int) -> str:
    """The selection's reason for stopping, in plain words, for an auto job."""
    if note.startswith("reached the requested"):
        return (f"Stopped at the campaign's maximum of {made}; raise \"Most clips per video\" "
                "on the campaign to allow more")
    if "below the absolute quality bar" in note:
        return "Every other moment scored below the quality bar"
    if "no candidate survived" in note:
        return "Nothing in this video scored well enough to clip"
    return note[:1].upper() + note[1:]


class JobRunner:
    """A single worker thread and its queue; jobs are kept for the session."""

    def __init__(self, publish) -> None:
        self.publish = publish
        self.jobs: dict[int, Job] = {}
        self._configs: dict[int, object] = {}
        self._queue: queue.Queue[Job] = queue.Queue()
        self._ids = itertools.count(1)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def submit(self, campaign, source: str, top: int | None,
               ranges: list[tuple[float, float]] | None = None) -> Job:
        """Queue a run for a loaded `CampaignConfig` and a source path.

        `ranges` (seconds) cuts exactly those moments; otherwise `top` clips at
        most, or with `top` None as many as are good enough.
        """
        mode = "manual" if ranges else "top" if top else "auto"
        job = Job(id=next(self._ids), campaign=campaign.name, source=source, top=top,
                  mode=mode, ranges=list(ranges or []))
        self.jobs[job.id] = job
        self._configs[job.id] = campaign
        self._queue.put(job)
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, daemon=True, name="clip-jobs")
                self._thread.start()
        self.publish("job.progress", job.view())
        return job

    def list(self) -> list[dict]:
        return [j.view() for j in sorted(self.jobs.values(), key=lambda j: -j.id)]

    def _work(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=5)
            except queue.Empty:
                return
            self._run(job)

    def _run(self, job: Job) -> None:
        from .. import runner
        from ..config import Config
        from ..paths import runs_dir

        pipeline = logging.getLogger("clipper")
        handler = _Progress(job, self.publish)
        previous = pipeline.level
        pipeline.addHandler(handler)
        pipeline.setLevel(logging.INFO)
        job.status, job.stage = "running", "Starting"
        self.publish("job.progress", job.view())
        campaign = self._configs.pop(job.id)
        try:
            if job.mode == "manual":
                handler.total = len(job.ranges)
                result = runner.cut(job.source, job.ranges, config=Config.load(),
                                    campaign=campaign, out_root=runs_dir())
            else:
                # Auto: the quality bar decides how many, up to the campaign's maximum.
                result = runner.run(job.source, config=Config.load(), campaign=campaign,
                                    out_root=runs_dir(),
                                    top=job.top or campaign.max_clips_per_source)
            job.clips = len(result.accepted)
            job.status, job.pct = "done", 100.0
            job.stage = (f"Made {job.clips} clip{'s' if job.clips != 1 else ''}" if job.clips
                         else "No clips made")
            notes = []
            if job.mode == "auto" and result.selection_note:
                notes.append(explain_stop(result.selection_note, job.clips))
            elif not job.clips:
                notes.append(result.selection_note)
            if result.rejected:
                notes.append(f"{len(result.rejected)} failed quality checks and were left out")
            job.message = ". ".join(n for n in notes if n)
        except Exception as exc:  # shown on the page, and in the log for debugging
            job.status, job.stage, job.message = "failed", "Failed", str(exc)[:400]
            log.error("clip job %s failed:\n%s", job.id, traceback.format_exc())
        finally:
            pipeline.removeHandler(handler)
            pipeline.setLevel(previous)
            job.finished = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.publish("job.progress", job.view())
            self.publish("clips.changed")
