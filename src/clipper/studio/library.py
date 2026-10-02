"""The clip library: data/library/<campaign>/, one place for every finished clip.

Runs file their accepted clips here themselves (`register`), so finding a clip
no longer means knowing which output folder a run wrote to. `import_folder`
brings in the hand-made "-ready" folders from before the library, reading the
captions from their POSTING.md / captions.txt and the posted state from the
"POSTED" prefix the user put on file names.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import data_root, ensure, runs_dir
from ..utils.cache import slugify
from ..utils.logging import get_logger
from . import db

log = get_logger(__name__)

if TYPE_CHECKING:  # pragma: no cover
    from ..campaign.manifest import ClipRecord
    from ..config import CampaignConfig
    from ..models import SourceInfo


def library_dir() -> Path:
    return data_root() / "library"


def clip_path(relative: str) -> Path:
    return library_dir() / relative


def register(records: list[ClipRecord], *, info: SourceInfo, campaign: CampaignConfig,
             captions: list[str] | None = None) -> list[int]:
    """Copy a run's accepted clips into the library and record them. Returns their ids."""
    from ..campaign.compliance import full_caption
    from . import evidence

    ids = []
    folder = ensure(library_dir() / campaign.name)
    with db.connect() as con:
        for i, record in enumerate(records):
            plan = record.plan
            slug = slugify(plan.hook_text or plan.text, max_length=40)
            name = f"{plan.clip_id}_{slug}_{info.source_id[:6]}.mp4"
            # From Clipper's own work area it moves: a second copy there was ~640 MB
            # of duplicates after a week. A folder the user chose keeps its copy.
            if record.file.resolve().is_relative_to(runs_dir().resolve()):
                shutil.move(record.file, folder / name)
            else:
                shutil.copy2(record.file, folder / name)
            ids.append(db.upsert_clip(con, {
                "campaign": campaign.name, "source_id": info.source_id,
                "clip_id": plan.clip_id, "source_title": info.title or "",
                "title": plan.hook_text or plan.text[:60], "file": f"{campaign.name}/{name}",
                "hook": plan.hook_text if plan.hook_shown else "",
                "caption": captions[i] if captions else full_caption(plan),
                "duration_s": record.duration, "start_s": plan.start, "end_s": plan.end,
                "scores": json.dumps(scores_of(record)),
                "evidence": json.dumps(evidence.snapshot(
                    record, info=info, campaign=campaign,
                    caption=captions[i] if captions else full_caption(plan)), ensure_ascii=False),
            }))
    return ids


#: Enough of a clip's words to compare it with others (studio/duplicates.py).
TEXT_CHARS = 1500


def scores_of(record: ClipRecord) -> dict:
    """What the scorer thought of a clip, as stored with it (see db.MIGRATIONS)."""
    text = record.plan.text[:TEXT_CHARS]
    if record.plan.candidate_id == "manual":
        return {"picked_by": "hand", "text": text, "text_v": 2}
    llm = record.raw.get("llm")
    return {"picked_by": "auto", "text": text, "text_v": 2,
            "score": round(llm, 2) if llm is not None else None,
            "rubric": record.rubric, "composite": round(record.plan.composite, 4),
            "pool": record.pool, "pool_rank": record.pool_rank,
            # The watch pass: the rubric total from watching, and what it saw.
            "watched": record.raw.get("watched"), "read": record.raw.get("llm_read"),
            "sees": record.sees, "visual_payoff": record.visual_payoff}


def backfill_text() -> int:
    """The spoken words of clips that have none on record, or only the first 400
    characters (clips filed before `text_v` 2).

    The duplicate check (studio/duplicates.py) compares words, so a clip without
    them can't be matched. They are transcribed once, with a small fast model --
    enough to compare, not to caption.
    """
    from ..config import TranscriptionConfig
    from ..transcribe.whisper import load_model

    with db.connect() as con:
        todo = [c for c in db.clips(con)
                if json.loads(c.get("scores") or "{}").get("text_v") != 2
                and clip_path(c["file"]).exists()]
    if not todo:
        return 0
    from ..transcribe.whisper import GPU_LOCK

    model, *_ = load_model(TranscriptionConfig(model="small", compute_type="int8_float16"))
    done = 0
    for clip in todo:
        try:
            with GPU_LOCK:  # clipping jobs use the GPU too (transcribe.whisper)
                segments, _info = model.transcribe(str(clip_path(clip["file"])), vad_filter=True,
                                                   beam_size=1)
                text = " ".join(s.text.strip() for s in segments)[:TEXT_CHARS]
        except Exception as exc:  # one unreadable file mustn't stop the rest
            log.info("no words for clip %s: %s", clip["id"], exc)
            continue
        scores = json.loads(clip.get("scores") or "{}")
        with db.connect() as con:
            con.execute("UPDATE clips SET scores=? WHERE id=?",
                        (json.dumps({**scores, "text": text or " ", "text_v": 2}), clip["id"]))
        done += 1
    return done


def backfill_evidence(campaigns: dict) -> int:
    """A (late) evidence snapshot for clips made before they were kept."""
    from . import evidence

    with db.connect() as con:
        todo = [c for c in db.clips(con) if not c.get("evidence")]
        for clip in todo:
            con.execute("UPDATE clips SET evidence=? WHERE id=?", (json.dumps(
                evidence.late_snapshot(clip, campaigns.get(clip["campaign"])), ensure_ascii=False),
                clip["id"]))
    return len(todo)


def backfill_scores() -> int:
    """Scores for clips filed before they were stored, from their runs' scoring files.

    Joins each clip to its candidate through the performance log, then reads
    that candidate's rubric from data/work/<source>/signals.json and scored.json.
    Clips whose work files are gone, or that were cut by hand, are marked so
    they aren't looked up again.
    """
    from ..learn import log as perf
    from ..paths import work_dir

    with db.connect() as con:
        todo = [c for c in db.clips(con) if c.get("scores") is None]
    if not todo:
        return 0
    candidate = {(r.get("campaign"), r.get("source_id"), r.get("clip_id")): r.get("candidate_id")
                 for r in perf.read() if r.get("candidate_id")}
    cache: dict[str, tuple[dict, dict, dict]] = {}
    filled = 0
    with db.connect() as con:
        for clip in todo:
            cid = candidate.get((clip["campaign"], clip["source_id"], clip["clip_id"]))
            scores: dict = {"picked_by": "hand" if cid == "manual" else "unknown"}
            if cid and cid != "manual":
                if clip["source_id"] not in cache:
                    cache[clip["source_id"]] = _load_scoring(work_dir(clip["source_id"]))
                values, scored, texts = cache[clip["source_id"]]
                entry = scored.get(cid)
                if entry is not None:
                    ranked = sorted((e for e in scored.values() if not e.get("dropped")),
                                    key=lambda e: e.get("composite", 0), reverse=True)
                    llm = (entry.get("raw") or {}).get("llm")
                    scores = {"picked_by": "auto", "text": texts.get(cid, "")[:400],
                              "score": round(llm, 2) if llm is not None else None,
                              "rubric": _average(values.get(cid) or {}),
                              "composite": round(entry.get("composite", 0), 4),
                              "pool": len(ranked),
                              "pool_rank": next((i for i, e in enumerate(ranked, 1)
                                                 if e.get("candidate_id") == cid), None)}
                    filled += 1
            con.execute("UPDATE clips SET scores=? WHERE id=?", (json.dumps(scores), clip["id"]))
    return filled


def _load_scoring(work: Path) -> tuple[dict, dict, dict]:
    def read(name: str) -> dict:
        try:
            return json.loads((work / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    values = {v.get("candidate_id"): v for v in read("signals.json").get("values", [])}
    scored = {s.get("candidate_id"): s for s in read("scored.json").get("scored", [])}
    texts = {c.get("candidate_id"): c.get("text", "") for c in read("candidates.json").get("candidates", [])}
    return values, scored, texts


def _average(values: dict) -> dict[str, float]:
    from ..runner import RUBRIC_FIELDS

    answers = [a for a in (values.get("llm_a"), values.get("llm_b")) if a]
    if not answers:
        return {}
    return {f: round(sum(float(a.get(f, 0)) for a in answers) / len(answers), 2)
            for f in RUBRIC_FIELDS}


def thumbnail(video: Path, *, at: float = 1.5) -> Path | None:
    """A cached JPEG still of `video` (library/.thumbs/), remade when the video changes."""
    import subprocess

    from ..render.ffmpeg import ffmpeg_path

    folder = ensure(library_dir() / ".thumbs")
    stat = video.stat()
    still = folder / f"{slugify(str(video.relative_to(library_dir())), max_length=80)}" \
                     f"_{int(stat.st_mtime)}.jpg"
    if still.exists():
        return still
    try:
        subprocess.run([str(ffmpeg_path()), "-v", "error", "-y", "-ss", f"{at}", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=270:-2", "-q:v", "4", str(still)],
                       check=True, timeout=30, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return still if still.exists() else None


@dataclass
class Noted:
    """What a POSTING.md / captions.txt says about one file."""

    caption: str = ""
    hook: str = ""
    duration_s: float | None = None


_POSTED = re.compile(r"^POSTED[\s_-]*", re.IGNORECASE)


def base_name(filename: str) -> str:
    """A file's name without extension or the user's "POSTED" prefix, lower-cased."""
    return _POSTED.sub("", Path(filename).stem).strip().lower()


def read_notes(folder: Path) -> dict[str, Noted]:
    """Captions and hooks from a ready folder's POSTING.md or captions.txt, by base name."""
    notes: dict[str, Noted] = {}
    posting = folder / "POSTING.md"
    if posting.exists():
        text = posting.read_text(encoding="utf-8")
        for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
            head = re.match(r"(\S+?\.mp4)\s*(?:\((\d+(?:\.\d+)?)s\))?", block)
            if not head:
                continue
            hook = re.search(r"^Hook on screen:\s*(.+)$", block, re.MULTILINE)
            caption = re.search(r"```\s*\n(.*?)\n```", block, re.DOTALL)
            notes[base_name(head.group(1))] = Noted(
                caption=caption.group(1).strip() if caption else "",
                hook=hook.group(1).strip() if hook else "",
                duration_s=float(head.group(2)) if head.group(2) else None)
    captions = folder / "captions.txt"
    if captions.exists():
        text = captions.read_text(encoding="utf-8-sig")
        for block in re.split(r"\n\s*\n", text):
            head = re.match(r"\s*(.+?)\s+\((\d+(?:\.\d+)?)s\)\s*$", block.splitlines()[0]
                            if block.strip() else "")
            if not head:
                continue
            hook = re.search(r"On screen:\s*(.+)", block)
            caption = re.search(r"Caption:\s*(.+)", block)
            notes[base_name(head.group(1))] = Noted(
                caption=caption.group(1).strip() if caption else "",
                hook=hook.group(1).strip() if hook else "",
                duration_s=float(head.group(2)))
    return notes


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()[:60]


def import_folder(folder: Path, campaign: str, log_rows: list[dict[str, str]]) -> list[int]:
    """Bring a pre-library "-ready" folder's clips into the library.

    Each file is matched to its performance-log row by file name, else by the
    caption its notes give it, which supplies the source and clip ids that
    link it to its posts. A file with no row keeps its own name as its id.
    """
    notes = read_notes(folder)
    target = ensure(library_dir() / campaign)
    rows = [r for r in log_rows if (r.get("platform") or "tiktok") != "instagram"
            and r.get("campaign") == campaign]
    ids = []
    with db.connect() as con:
        for path in sorted(folder.glob("*.mp4")):
            noted = notes.get(base_name(path.name), Noted())
            row = next((r for r in rows if r.get("file") == path.name), None)
            if row is None and noted.caption:
                row = next((r for r in rows if _key(r.get("caption", "")) == _key(noted.caption)),
                           None)
            name = _POSTED.sub("", path.name)
            shutil.copy2(path, target / name)
            posted = bool(_POSTED.match(path.name)) or bool(row and (row.get("url") or "").strip())
            ids.append(db.upsert_clip(con, {
                "campaign": campaign,
                "source_id": (row or {}).get("source_id") or f"import:{folder.name}",
                "clip_id": (row or {}).get("clip_id") or base_name(path.name),
                "source_title": (row or {}).get("source_title", ""),
                "title": (noted.hook or (row or {}).get("hook")
                          or first_sentence((row or {}).get("caption") or noted.caption)
                          or base_name(path.name)),
                "file": f"{campaign}/{name}",
                "hook": noted.hook or (row or {}).get("hook", ""),
                "caption": (row or {}).get("caption") or noted.caption,
                "duration_s": noted.duration_s or _float((row or {}).get("duration_s")),
                "status": "posted" if posted else "ready",
            }))
    return ids


def split_caption(caption: str) -> tuple[str, str]:
    """(caption line, hashtags) of a stored caption, with or without a description."""
    caption = (caption or "").strip()
    if "\n\n" in caption:
        parts = caption.split("\n\n")
        tags = parts[-1] if all(w.startswith("#") for w in parts[-1].split()) else ""
        return parts[0].strip(), tags.strip()
    line, _, tags = caption.partition("  ")
    if tags and not all(w.startswith("#") for w in tags.split()):
        return caption, ""
    return line.strip(), tags.strip()


def with_description(caption: str, description: str) -> str:
    line, tags = split_caption(caption)
    return "\n\n".join(p for p in (line, description.strip(), tags) if p)


def describe_clips(campaign_name: str, *, backends=None, listener=None) -> list[tuple[str, str]]:
    """Write descriptions for a campaign's clips that aren't posted yet.

    Posted clips keep their caption: it is what is live, and what the syncs match
    on. Needs each clip's time range (start_s/end_s) and its source's transcript.
    Returns (title, description) for each clip described.
    """
    from ..campaign.description import describe, pasted_brief
    from ..config import CampaignConfig, Config
    from ..learn import log as perf
    from ..models import SourceInfo, Transcript
    from ..paths import REPO_ROOT, work_dir
    from ..runner import with_range_transcript
    from .stats import posts_by_clip

    campaign = CampaignConfig.load(REPO_ROOT / "campaigns" / f"{campaign_name}.yaml")
    if not campaign.long_description:
        raise ValueError(f"{campaign_name}: set long_description: true in its yaml first")
    config = Config.load()
    if backends is None:
        from ..pipeline import build_backend

        backends = [build_backend(config)]
    rows = perf.read()
    posts = posts_by_clip(rows)
    brief = pasted_brief(campaign_name)
    done = []
    with db.connect() as con:
        for clip in db.clips(con, campaign_name):
            live = posts.get((clip["campaign"], clip["source_id"], clip["clip_id"]))
            if live or clip["status"] != "ready" or clip["start_s"] is None:
                continue
            work = work_dir(clip["source_id"])
            if not (work / "transcript.json").exists():
                continue
            words = Transcript.load(work / "transcript.json").words
            start, end = clip["start_s"], clip["end_s"]
            if listener is None and (work / "info.json").exists():
                from ..transcribe.recheck import AudioRecheck

                info = SourceInfo.load(work / "info.json")
                if info.audio_path and Path(info.audio_path).exists():
                    listener = AudioRecheck(Path(info.audio_path), config.transcription)
            if listener is not None:
                words = with_range_transcript(words, start, end, listener.words_between(start, end))
            text = " ".join(w.text for w in words if start <= (w.start + w.end) / 2 < end)
            line, _ = split_caption(clip["caption"])
            description = describe(text, campaign, line, backends, hook=clip["hook"] or "", brief=brief)
            if not description:
                continue
            caption = with_description(clip["caption"], description)
            db.update_clip(con, clip["id"], caption=caption)
            for row in rows:  # the log row, so the syncs and Control Center agree
                if ((row.get("platform") or "tiktok") == "tiktok" and not row.get("url")
                        and (row.get("campaign"), row.get("source_id"), row.get("clip_id"))
                        == (clip["campaign"], clip["source_id"], clip["clip_id"])):
                    row["caption"] = caption
            done.append((clip["title"], description))
    perf.write(rows)
    return done


def first_sentence(caption: str, limit: int = 60) -> str:
    """A caption's opening sentence, for a clip with no hook to be named by."""
    text = re.split(r"(?<=[.!?])\s|\s#|\s@", (caption or "").strip(), maxsplit=1)[0].strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None
