"""The user's own clips in a Create video (D119): dropped in on the page, placed on the
sentences they fit, cut to the voice, and the rest of the voice filled with the planned
footage and diagrams.

A video's clips live in `<video folder>/mine/` with a small manifest. Where a clip goes is
stored on the beat itself (`Visual.clip`, `clip_start`, `fill`), so it travels with the
sentence when the script is edited, and the picture the script planned for that sentence
stays behind it as the filler. Three ways to place, mixable:

* by hand: pick the clip (and where in it to start) on each sentence;
* "in order": a free, deterministic layout, no model involved;
* "automatically": the model looks at a filmstrip of every clip and matches clips to
  sentences; if it can't (limit, offline, bad answer) the in-order layout is used.

At build time, with "place for me" on and nothing placed, the automatic way runs by itself.
A clip shorter than its sentence is made to fit by `fill_plan`; clips longer than the
sentence are cut. Clip sound is never used: only the voice.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from ..ingest.probe import probe
from ..render.ffmpeg import FFmpegError, ffmpeg_path
from ..utils.logging import get_logger
from . import channel as channels
from .ai import CreateError, ask
from .script import Script
from .voice import folder

log = get_logger(__name__)

EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
MIN_SECONDS = 0.5         # shorter than this is a flash, not a shot
MAX_CLIPS = 30
MAX_BYTES = 3 * 1024**3
MAX_AI_CLIPS = 12         # the model looks at one filmstrip per clip; more than this is too many images
LOW_RES = 720             # a clip shorter than this (its short side) looks soft filling a phone
FILLS = ("auto", "planned", "loop", "slow", "hold")
MARK = "Build notes:"     # the line the notes block starts with
_OLD_MARKS = ("Your clips:",)   # its earlier name, still cleaned up
_lock = threading.Lock()


# ---------- the manifest ----------

def mine_dir(video_id: int) -> Path:
    path = folder(video_id) / "mine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manifest(video_id: int) -> Path:
    return mine_dir(video_id) / "manifest.json"


def load(video_id: int) -> dict:
    """{"auto": bool, "fill": str, "next": int, "clips": [...]}; never raises on a missing or
    damaged manifest (a damaged one reads as empty rather than blocking the page)."""
    fresh = {"auto": True, "fill": "auto", "next": 1, "clips": []}
    try:
        data = json.loads(_manifest(video_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fresh
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        return fresh
    return {**fresh, **data}


def _save(video_id: int, data: dict) -> None:
    path = _manifest(video_id)
    partial = path.with_suffix(".tmp")
    partial.write_text(json.dumps(data, indent=1), encoding="utf-8")
    partial.replace(path)


def clean_name(name: str) -> str:
    """A file name safe to show and store: no folders, no control characters, not endless."""
    base = re.split(r"[\\/]", name)[-1]
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return (base or "clip")[:80]


def files(video_id: int) -> dict[str, tuple[Path, float, str]]:
    """clip id -> (file, seconds, name), for the clips whose files are still there."""
    out = {}
    for c in load(video_id)["clips"]:
        path = mine_dir(video_id) / c["file"]
        if path.is_file():
            out[c["id"]] = (path, float(c["duration"]), c["name"])
    return out


def thumb_path(video_id: int, clip_id: str) -> Path | None:
    if not any(c["id"] == clip_id for c in load(video_id)["clips"]):
        return None
    path = mine_dir(video_id) / f"{clip_id}.jpg"
    return path if path.is_file() else None


def file_path(video_id: int, clip_id: str) -> Path | None:
    got = files(video_id).get(clip_id)
    return got[0] if got else None


def _thumb(src: Path, seconds: float, out: Path) -> None:
    try:
        subprocess.run([str(ffmpeg_path()), "-loglevel", "error", "-y", "-ss", f"{min(1.0, seconds / 2):.2f}",
                        "-i", str(src), "-frames:v", "1", "-vf", "scale=360:-2", str(out)],
                       capture_output=True, timeout=60, check=True)
    except (subprocess.SubprocessError, OSError) as exc:  # the thumbnail is a nicety
        log.info("create: no thumbnail for %s (%s)", src.name, exc)


def add(video_id: int, partial: Path, name: str) -> dict:
    """Keep an uploaded file as one of the video's clips: checked first (a video format,
    readable, has pictures, long enough, not a duplicate, within the limits). `partial` is
    moved in, or sent to the Recycle Bin when the clip is refused."""
    from ..utils.recycle import recycle

    name = clean_name(name)
    suffix = Path(name).suffix.lower()

    def refuse(why: str):
        recycle(partial)
        raise CreateError(why)

    if suffix not in EXTS:
        refuse(f"{name} isn't a video file Clipper takes ({', '.join(sorted(EXTS))})")
    size = partial.stat().st_size
    if size <= 0:
        refuse(f"{name} is empty")
    try:
        info = probe(partial)
    except (FFmpegError, FileNotFoundError, ValueError) as exc:
        log.info("create: unreadable clip %s: %s", name, exc)
        refuse(f"{name} can't be read as a video (damaged, or an odd format)")
    if info.width <= 0 or info.height <= 0:
        refuse(f"{name} has no picture in it (sound only?)")
    if info.duration < MIN_SECONDS:
        refuse(f"{name} is under half a second long")
    with _lock:
        data = load(video_id)
        if len(data["clips"]) >= MAX_CLIPS:
            refuse(f"That's the most clips one video takes ({MAX_CLIPS})")
        if any(c["name"] == name and c["size"] == size for c in data["clips"]):
            refuse(f"{name} is already added")
        clip_id = f"c{data['next']}"
        kept = mine_dir(video_id) / f"{clip_id}{suffix}"
        partial.replace(kept)
        entry = {"id": clip_id, "name": name, "file": kept.name, "duration": round(info.duration, 2),
                 "width": info.width, "height": info.height, "size": size,
                 "low_res": min(info.width, info.height) < LOW_RES}
        data["clips"].append(entry)
        data["next"] += 1
        _save(video_id, data)
    _thumb(kept, info.duration, mine_dir(video_id) / f"{clip_id}.jpg")
    return entry


def remove(video_id: int, clip_id: str) -> bool:
    """Take a clip out (its files to the Recycle Bin). False if there was no such clip."""
    from ..utils.recycle import recycle

    with _lock:
        data = load(video_id)
        gone = [c for c in data["clips"] if c["id"] == clip_id]
        if not gone:
            return False
        data["clips"] = [c for c in data["clips"] if c["id"] != clip_id]
        _save(video_id, data)
    for path in (mine_dir(video_id) / gone[0]["file"], mine_dir(video_id) / f"{clip_id}.jpg"):
        if path.exists():
            recycle(path)
    return True


def settings(video_id: int, *, auto: bool | None = None, fill: str | None = None) -> dict:
    if fill is not None and fill not in FILLS:
        raise CreateError(f"fill must be one of {', '.join(FILLS)}")
    with _lock:
        data = load(video_id)
        if auto is not None:
            data["auto"] = bool(auto)
        if fill is not None:
            data["fill"] = fill
        _save(video_id, data)
    return data


def view(video_id: int, script: Script) -> dict:
    """What the page shows: the settings, and each clip with the sentences it's placed on."""
    data = load(video_id)
    present = files(video_id)
    clips = []
    for c in data["clips"]:
        used = [i + 1 for i, b in enumerate(script.beats) if b.visual.clip == c["id"]]
        clips.append({k: c[k] for k in ("id", "name", "duration", "width", "height", "low_res")}
                     | {"used": used, "missing": c["id"] not in present})
    return {"auto": data["auto"], "fill": data["fill"], "clips": clips}


# ---------- placing on the script ----------

def estimate(script: Script, words_per_second: float) -> list[float]:
    """Seconds each sentence will take read aloud, before the real voice is known."""
    wps = words_per_second if words_per_second > 0 else 2.6
    return [max(1.2, len(b.text.split()) / wps) for b in script.beats]


def _cleared(script: Script, beats: list[int] | None = None) -> Script:
    out = []
    for i, b in enumerate(script.beats):
        if b.visual.clip and (beats is None or i in beats):
            b = b.model_copy(update={"visual": b.visual.model_copy(
                update={"clip": "", "clip_start": None, "fill": "auto"})})
        out.append(b)
    return script.model_copy(update={"beats": out})


def clear(script: Script) -> Script:
    """Every sentence back to its planned picture."""
    return _cleared(script)


def forget(script: Script, keep: set[str]) -> tuple[Script, int]:
    """The script without placements of clips that aren't in `keep` (deleted, or missing
    from disk); how many sentences were affected."""
    gone = [i for i, b in enumerate(script.beats) if b.visual.clip and b.visual.clip not in keep]
    return _cleared(script, gone), len(gone)


def set_beat(script: Script, beat: int, clip: str, start: float | None, fill: str, known: dict[str, float]) -> Script:
    """One sentence's clip set by hand. `beat` is 1-based; clip "" goes back to the planned
    picture; start None continues from where the last sentence using this clip left off.
    `known` maps clip id to its length."""
    if not 1 <= beat <= len(script.beats):
        raise CreateError("no such sentence")
    if fill not in FILLS:
        raise CreateError(f"fill must be one of {', '.join(FILLS)}")
    if clip and clip not in known:
        raise CreateError("no such clip")
    if start is not None:
        if start != start or start < 0:  # NaN or negative
            raise CreateError("start is a number of seconds, 0 or more")
        if clip and start > known[clip] - MIN_SECONDS:
            raise CreateError(f"that clip is {known[clip]:.1f}s long: start earlier")
    visual = script.beats[beat - 1].visual.model_copy(
        update={"clip": clip, "clip_start": start if clip else None, "fill": fill if clip else "auto"})
    beats = list(script.beats)
    beats[beat - 1] = beats[beat - 1].model_copy(update={"visual": visual})
    return script.model_copy(update={"beats": beats})


def order_layout(seconds: list[float], allowed: list[int], clips: list[tuple[str, float]]
                 ) -> tuple[dict[int, tuple[str, float | None]], list[str]]:
    """The free layout: the clips in the order given, each over the next sentences it fully
    covers (at least one), the sentences left over spread evenly after each clip, the first
    clip on the first sentence allowed (the hook). `allowed` are the sentences clips may go
    on. Returns {sentence index: (clip id, start; None = carry on)} and the ids of the clips
    there was no room for."""
    pool = sorted(allowed)
    groups: list[tuple[str, int]] = []   # (clip id, how many sentences it covers)
    unused: list[str] = []
    for clip_id, length in clips:
        if not pool:
            unused.append(clip_id)
            continue
        taken, total = 1, seconds[pool.pop(0)]
        while pool and total + seconds[pool[0]] <= length:
            total += seconds[pool.pop(0)]
            taken += 1
        groups.append((clip_id, taken))
    spare = len(pool)
    slots = len(groups)
    layout: dict[int, tuple[str, float | None]] = {}
    positions = iter(sorted(allowed))
    for k, (clip_id, taken) in enumerate(groups):
        for n in range(taken):
            layout[next(positions)] = (clip_id, 0.0 if n == 0 else None)
        for _ in range(spare // slots + (1 if k < spare % slots else 0)):
            next(positions)
    return layout, unused


class _Put(BaseModel):
    beat: int
    clip: str
    start: float = 0.0


class _Plan(BaseModel):
    placements: list[_Put] = Field(default_factory=list)
    note: str = ""


PLACE = """You are the editor of a 45-second narrated {subject} Short for phones. The user filmed or
picked their own clips and wants them on the sentences they fit. You see one filmstrip image per
clip (four frames, left to right, in time order) and the sentences with how long each is spoken.

Place clips on sentences: for each sentence, say which clip (if any) shows what is being said, and
the second of the clip to start from. Rules:
- A sentence takes at most one clip. A clip may cover several sentences in a row; give one entry
  per sentence, and for the later ones the start does not matter (it carries on).
- Match the picture to the words: what the sentence talks about, or the mood and moment it
  belongs to. The first sentence is the hook: put the clip most striking to watch there.
- Prefer a clip about as long as the sentences it covers; a clip shorter than its sentence is
  filled out by the app, so it is acceptable but not ideal.
- Sentences marked [diagram] carry the {subject} explanation: leave them alone unless a clip
  clearly shows the same thing.
- Use each clip's id exactly as given. Sentences are numbered from 1. Start is in seconds, inside the clip."""


def filmstrip(path: Path, duration: float, frames: int = 4, height: int = 220) -> bytes:
    """Frames across a clip, side by side in one JPEG: what the model looks at."""
    from PIL import Image

    shots = []
    for k in range(frames):
        t = duration * (k + 0.5) / frames
        try:
            png = subprocess.run([str(ffmpeg_path()), "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                                  "-frames:v", "1", "-vf", f"scale=-2:{height}", "-f", "image2pipe", "-vcodec", "png", "-"],
                                 capture_output=True, timeout=60, check=True).stdout
            shots.append(Image.open(io.BytesIO(png)).convert("RGB"))
        except (subprocess.SubprocessError, OSError):
            continue
    if not shots:
        raise CreateError(f"couldn't look inside {path.name}")
    strip = Image.new("RGB", (sum(s.width for s in shots), height))
    x = 0
    for s in shots:
        strip.paste(s, (x, 0))
        x += s.width
    buf = io.BytesIO()
    strip.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def normalize(plan: _Plan, count: int, lengths: dict[str, float]) -> dict[int, tuple[str, float | None]]:
    """The model's answer made safe: only real sentences (`count` of them) and clips, one clip
    per sentence, starts inside the clip, and a clip carrying on over the sentences after its
    first (start None) rather than restarting."""
    taken: dict[int, tuple[str, float]] = {}
    for p in plan.placements:
        i = p.beat - 1
        if 0 <= i < count and p.clip in lengths and i not in taken:
            start = p.start if p.start == p.start else 0.0  # NaN
            room = lengths[p.clip] - min(lengths[p.clip], MIN_SECONDS)
            taken[i] = (p.clip, max(0.0, min(start, room)))
    layout: dict[int, tuple[str, float | None]] = {}
    for i in sorted(taken):
        clip, start = taken[i]
        layout[i] = (clip, None if i - 1 in taken and taken[i - 1][0] == clip else start)
    return layout


def place(video_id: int, script: Script, seconds: list[float], *, use_ai: bool = True,
          strict: bool = False) -> tuple[Script, str]:
    """Place the video's clips on the script, replacing earlier placements. With `use_ai` the
    model matches them to sentences (every clip used unless `strict`); with no model, or when
    it fails, the free in-order layout. Returns the script and a note for the page."""
    from . import ai

    known = files(video_id)
    clips = [(cid, dur) for cid, (_, dur, _) in known.items()]
    if not clips:
        raise CreateError("add some clips first")
    lengths = dict(clips)
    count = len(script.beats)
    protected = {i for i, b in enumerate(script.beats) if b.visual.kind == "diagram"}
    free = [i for i in range(count) if i not in protected] or list(range(count))
    layout: dict[int, tuple[str, float | None]] = {}
    how, why = "in the order you added them", ""
    if use_ai and len(clips) <= MAX_AI_CLIPS:
        try:
            strips = [(filmstrip(path, dur), "image/jpeg") for path, dur, _ in known.values()]
            listing = "\n".join(f"Image {k}: clip id {cid} ({name}), {dur:.1f}s long"
                                for k, (cid, (_, dur, name)) in enumerate(known.items(), start=1))
            sentences = "\n".join(f"{i + 1}. [{'diagram' if i in protected else 'footage'}, about {s:.1f}s] {b.text}"
                                  for i, (b, s) in enumerate(zip(script.beats, seconds, strict=True)))
            rule = ("Only place a clip where it really fits; leave sentences without one rather than force it."
                    if strict else
                    "The user chose every one of these clips and wants all of them used: place each where it "
                    "fits best, even loosely, and leave the other sentences without one.")
            answer = ask(channels.fill(PLACE), f"Video title: {script.title}\n\nSentences:\n{sentences}\n\nClips:\n{listing}\n\n{rule}",
                         _Plan, temperature=0.0, media=strips)
            layout = normalize(_Plan.model_validate(json.loads(answer)), count, lengths)
            if layout:
                how = f"by {ai.last_used.split(':', 1)[-1] or 'the AI'}"
            else:
                why = "the AI placed none of them"
        except (CreateError, ValueError, TypeError) as exc:
            why = str(exc).splitlines()[0][:160] if str(exc) else "the AI didn't answer"
            log.info("create: clips placed in order, not by the AI (%s)", why)
    elif use_ai:
        why = f"more than {MAX_AI_CLIPS} clips is too many for the AI to look at"
    by_ai = bool(layout)
    if not by_ai:
        layout, _ = order_layout(seconds, free, clips)
    elif not strict:  # the clips the AI left out still go on sentences nothing else took
        used = {c for c, _ in layout.values()}
        rest = [(cid, dur) for cid, dur in clips if cid not in used]
        if rest:
            more, _ = order_layout(seconds, [i for i in free if i not in layout], rest)
            layout |= more
    placed = {c for c, _ in layout.values()}
    left = [known[cid][2] for cid, _ in clips if cid not in placed]
    beats = []
    for i, b in enumerate(script.beats):
        clip, start = layout.get(i, ("", None))
        beats.append(b.model_copy(update={"visual": b.visual.model_copy(
            update={"clip": clip, "clip_start": start, "fill": "auto"})}))
    note = f"Placed {len(placed)} of {len(clips)} clips {how}" + (f" ({why})" if why else "") + "."
    if left:
        note += " Not used: " + ", ".join(left) + "."
    return script.model_copy(update={"beats": beats}), note


def prepare(video_id: int, script: Script, seconds: list[float]) -> tuple[Script, str]:
    """The script as it will be built: placements of clips that are gone dropped, and, when
    the video has clips, "place for me" is on and nothing is placed yet, placed automatically.
    Returns the script and a note ("" when there is nothing to say)."""
    data = load(video_id)
    present = files(video_id)
    script, dropped = forget(script, set(present))
    note = (f"{dropped} sentence{'s' if dropped != 1 else ''} used a clip that is gone; "
            "the planned picture is used." if dropped else "")
    if present and data["auto"] and not any(b.visual.clip for b in script.beats):
        script, placed = place(video_id, script, seconds)
        note = f"{note} {placed}".strip()
    return script, note


# ---------- the notes block (what the clips, the footage and placing did) ----------

def with_notes(existing: str, lines: list[str]) -> str:
    """`existing` with the "Build notes:" block replaced by `lines` (none: removed)."""
    kept, skipping = [], False
    for line in existing.split("\n"):
        if line.startswith((MARK, *_OLD_MARKS)):
            skipping = True
            continue
        if skipping and line.startswith("  - "):
            continue
        skipping = False
        kept.append(line)
    text = "\n".join(kept).rstrip("\n")
    if not lines:
        return text
    block = "\n".join([MARK, *[f"  - {x}" for x in lines]])
    return f"{text}\n{block}" if text else block


# ---------- fitting a clip to its sentence ----------

def fill_plan(avail: float, seconds: float, fill: str) -> tuple[str, float, float]:
    """How a clip with `avail` seconds left plays over a sentence of `seconds`:
    (mode, slow-down factor, seconds still to fill after the clip). Modes: "cut" (the clip is
    long enough), "hold" (the clip, then its last frame), "slow" (stretched at most 2x, then
    held), "loop" (repeated), "planned" (the clip, then the sentence's planned picture).
    "auto" holds for a gap under 0.8s and otherwise goes on to the planned picture."""
    if avail >= seconds - 0.05:
        return "cut", 1.0, 0.0
    rest = seconds - avail
    if fill == "auto":
        fill = "hold" if rest <= 0.8 else "planned"
    if fill == "slow":
        factor = min(2.0, seconds / avail)
        return "slow", factor, max(0.0, seconds - avail * factor)
    if fill == "loop":
        return "loop", 1.0, 0.0
    return fill, 1.0, rest
