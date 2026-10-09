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
    mode: str = "auto"              # auto | top | manual | prepare (for the editor)
    ranges: list[tuple[float, float]] = field(default_factory=list)
    # Clips made in the editor (editing.ClipEdit as dicts, D103); a manual job.
    edits: list[dict] = field(default_factory=list)
    status: str = "queued"          # queued | running | done | failed
    stage: str = "Waiting to start"
    pct: float = 0.0
    clips: int = 0
    message: str = ""
    # What happened to every moment (select/report.py), shown as the job's results.
    report: dict = field(default_factory=dict)
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
        # Two jobs run at once (D75): only this job's thread, and the threads it
        # starts (named after it), are this job's progress.
        self.thread = threading.current_thread().name

    def emit(self, record: logging.LogRecord) -> None:
        if record.threadName != self.thread and not record.threadName.startswith(self.thread + "-"):
            return
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


#: The selection's own terms, in plain words.
PLAIN = {"below the relative composite threshold": "well below this video's best",
         "below the absolute quality bar": "below the quality bar"}


def failure(exc: BaseException, limit: int = 400) -> str:
    """Why a job failed, for the page (D176): a full disk said plainly, with how much is free, where it came
    as ffmpeg's or Windows' own words; anything else as it was."""
    import errno
    import shutil

    from ..paths import data_root

    text = str(exc)
    if (isinstance(exc, OSError) and exc.errno == errno.ENOSPC) or any(
            s in text for s in ("No space left on device", "not enough space on the disk")):
        try:
            free = f" ({shutil.disk_usage(data_root()).free / 1024**3:.1f} GB free)"
        except OSError:
            free = ""
        return (f"The disk is full{free}. Free some space, then try again: a long video and its clips "
                "can take 5-10 GB.")
    return text[:limit]


def explain_stop(note: str, made: int) -> str:
    """The selection's reason for stopping, in plain words, for an auto job."""
    if note.startswith("reached the requested"):
        return (f"Stopped at the campaign's maximum of {made}; raise \"Most clips per video\" "
                "on the campaign to allow more")
    if "below the absolute quality bar" in note and not note.startswith("produced"):
        return "Every other moment scored below the quality bar"
    if "no candidate survived" in note:
        return "Nothing in this video scored well enough to clip"
    return tidy(note)


def tidy(message: str) -> str:
    """"Produced 1 of 500 requested; the rest were 2 overlapping" -- 500 is the
    internal "no limit" -- as "The others were 2 overlapping"."""
    match = re.match(r"(?i)produced \d+ of \d+ requested(?:; the rest were (.*))?$", message.strip())
    if match:
        message = f"The others were {match.group(1)}" if match.group(1) else ""
    for term, plain in PLAIN.items():
        message = message.replace(term, plain)
    return message[:1].upper() + message[1:]


KEEP = 30


def _load_kept() -> dict[int, dict]:
    """The jobs of earlier sessions. One still queued or running was cut off when Clipper closed (D175)."""
    import json

    from . import db

    with db.connect() as con:
        rows = con.execute("SELECT id, data FROM jobs ORDER BY id DESC LIMIT ?", (KEEP,)).fetchall()
    out = {}
    for r in rows:
        job = json.loads(r["data"])
        job["message"] = ". ".join(t for t in (tidy(part) for part in (job.get("message") or "").split(". ")) if t)
        if job.get("status") in ("queued", "running"):
            job.update(status="failed", stage="Stopped", message="Clipper closed before this finished. Clip the "
                       "video again: the steps it had finished are reused, not done twice.")
        out[r["id"]] = job
    return out


def _keep(job: Job) -> None:
    """Kept when it's queued, when it starts and when it ends, so a job Clipper closed on is still listed
    after a restart, said to have stopped, not gone without a word (D175)."""
    import json

    from . import db

    try:
        with db.connect() as con:
            con.execute("INSERT OR REPLACE INTO jobs (id, data, finished) VALUES (?, ?, ?)",
                        (job.id, json.dumps(job.view()), job.finished))
    except Exception as exc:  # the job itself is done; only its record is lost
        log.warning("could not keep job %s: %s", job.id, exc)


#: Videos clipped at once (D75). One job waits on the AI while another uses the
#: GPU and CPU; Whisper still runs one at a time (transcribe.whisper.GPU_LOCK)
#: and the AI's per-minute budget is shared (llm.base.RateLimiter.shared).
JOB_WORKERS = 2


class JobRunner:
    """JOB_WORKERS worker threads on one queue, oldest job first; jobs are kept for the session."""

    def __init__(self, publish) -> None:
        self.publish = publish
        self.jobs: dict[int, Job] = {}
        self._configs: dict[int, object] = {}
        self._queue: queue.Queue[Job] = queue.Queue()
        # Finished jobs are kept in the database, so their results outlive a restart.
        self._done = _load_kept()
        self._ids = itertools.count(max([0, *self._done]) + 1)
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def submit(self, campaign, source: str, top: int | None,
               ranges: list[tuple[float, float]] | None = None, *,
               edits: list[dict] | None = None, prepare: bool = False) -> Job:
        """Queue a run for a loaded `CampaignConfig` and a source path.

        `ranges` (seconds) cuts exactly those moments, and `edits` renders clips
        made in the editor; otherwise `top` clips at most, or with `top` None as
        many as are good enough. `prepare` only reads and transcribes the video,
        for the editor.
        """
        mode = ("prepare" if prepare else "manual" if ranges or edits
                else "top" if top else "auto")
        job = Job(id=next(self._ids), campaign=campaign.name, source=source, top=top,
                  mode=mode, ranges=list(ranges or []), edits=list(edits or []))
        self.jobs[job.id] = job
        self._configs[job.id] = campaign
        _keep(job)
        self._queue.put(job)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            if len(self._threads) < JOB_WORKERS:
                worker = threading.Thread(target=self._work, daemon=True, name="clip-jobs")
                worker.start()
                self._threads.append(worker)
        self.publish("job.progress", job.view())
        return job

    @staticmethod
    def _prepare(job: Job, campaign) -> None:
        """Read and transcribe the video, so the editor can open it (D103)."""
        from ..config import Config
        from ..ingest.download import ingest
        from ..runner import campaign_config
        from ..transcribe.whisper import transcribe

        info = ingest(job.source)
        transcribe(info, campaign_config(Config.load(), campaign).transcription)
        job.report = {"source_id": info.source_id}
        job.status, job.pct, job.stage = "done", 100.0, "Ready to edit"

    def list(self) -> list[dict]:
        live = {j.id: j.view() for j in self.jobs.values()}
        merged = {**self._done, **live}
        return [merged[i] for i in sorted(merged, reverse=True)[:KEEP]]

    def _work(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=5)
            except queue.Empty:
                return
            # The thread carries the job's id, so its log lines (and those of
            # the threads it starts) reach only this job's progress.
            threading.current_thread().name = f"clip-job-{job.id}"
            self._run(job)

    def _run(self, job: Job) -> None:
        from .. import runner
        from ..config import Config
        from ..paths import runs_dir

        pipeline = logging.getLogger("clipper")
        handler = _Progress(job, self.publish)
        pipeline.addHandler(handler)
        if pipeline.getEffectiveLevel() > logging.INFO:  # the progress reads INFO lines
            pipeline.setLevel(logging.INFO)
        job.status, job.stage = "running", "Starting"
        _keep(job)
        self.publish("job.progress", job.view())
        campaign = self._configs.pop(job.id)
        try:
            if job.mode == "prepare":
                self._prepare(job, campaign)
                return
            if job.mode == "manual":
                from ..editing import ClipEdit

                handler.total = len(job.edits or job.ranges)
                result = runner.cut(job.source, job.ranges, config=Config.load(),
                                    campaign=campaign, out_root=runs_dir(),
                                    edits=[ClipEdit.model_validate(e) for e in job.edits] or None)
            else:
                # Auto: the quality bar decides how many, up to the campaign's
                # maximum if it has one (runner.clip_limit).
                result = runner.run(job.source, config=Config.load(), campaign=campaign,
                                    out_root=runs_dir(), top=job.top)
            job.clips = len(result.accepted)
            job.report = getattr(result, "report", None) or {}
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
            job.status, job.stage, job.message = "failed", "Failed", failure(exc)
            log.error("clip job %s failed:\n%s", job.id, traceback.format_exc())
        finally:
            pipeline.removeHandler(handler)
            job.finished = datetime.now().strftime("%Y-%m-%d %H:%M")
            _keep(job)
            self.publish("job.progress", job.view())
            self.publish("clips.changed")
            if job.clips:  # the AI rule check reads the new clips (D81)
                from . import rulecheck

                rulecheck.start(job.campaign, self.publish)
