"""Run outputs: the manifest, the performance template, and the report.

Three files with three audiences (docs/BUILD_BRIEF.md section 13):

* `manifest.csv` / `manifest.json` -- machine-readable, one row per clip, with
  every sub-score so `clipper learn` can correlate them against real views later.
* the performance log (`data/performance.xlsx`, see learn/log.py) -- one row
  per clip across all runs, which the user fills in after posting. Its
  `candidate_id` joins back to the scores.
* `report.md` -- for a human deciding what to post.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..campaign.compliance import ComplianceReport, full_caption
from ..config import CampaignConfig
from ..learn import log as perf
from ..models import ClipPlan, QAReport, SourceInfo
from ..transcribe.correct import WordFix
from ..utils.logging import get_logger
from ..utils.timecode import format_duration, to_ffmpeg

log = get_logger(__name__)

MANIFEST_COLUMNS = [
    "clip_id", "candidate_id", "source_id", "source_title", "campaign", "start", "end", "duration",
    "composite", "llm_a", "llm_b", "audio", "heatmap", "text",
    "layout", "caption_style", "hook_text", "suggested_caption", "hashtags",
    "credit_text", "qa_status", "compliance_status", "file", "created_at",
    "caption_fixes",
]


@dataclass
class ClipRecord:
    """One finished clip and everything known about it."""

    plan: ClipPlan
    file: Path
    qa: QAReport
    compliance: ComplianceReport
    components: dict[str, float] = field(default_factory=dict)
    raw: dict[str, float] = field(default_factory=dict)
    llm_a_total: float | None = None
    llm_b_total: float | None = None
    rendered_duration: float | None = None
    # Words the transcript correction changed in this clip's captions.
    caption_fixes: list[WordFix] = field(default_factory=list)
    # The rubric's six 0-10 scores (the two prompts averaged), and where the
    # moment ranked among all the video's scored moments: shown in the Control
    # Center so the user can judge the scoring, and learnt from with their ratings.
    rubric: dict[str, float] = field(default_factory=dict)
    pool: int | None = None
    pool_rank: int | None = None
    # From the "watch it" pass (signals/visual.py): what the picture adds.
    sees: str = ""
    visual_payoff: int | None = None

    @property
    def duration(self) -> float:
        return self.rendered_duration if self.rendered_duration is not None else self.plan.duration


def write_outputs(
    records: list[ClipRecord],
    *,
    info: SourceInfo,
    campaign: CampaignConfig,
    out_dir: Path,
    rejected: list[ClipRecord] | None = None,
    selection_note: str = "",
    signals_available: list[str] | None = None,
    weights_used: dict[str, float] | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Path]:
    """Write every output file for one run. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC).isoformat(timespec="seconds")

    rows = [_row(r, info=info, campaign=campaign, created_at=created) for r in records]

    manifest_csv = out_dir / "manifest.csv"
    with manifest_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    manifest_json = out_dir / "manifest.json"
    manifest_json.write_text(
        json.dumps(
            {
                "source_id": info.source_id,
                "source_title": info.title,
                "source_url": info.url,
                "campaign": campaign.name,
                "source_authorization": campaign.source_authorization,
                "created_at": created,
                "signals_available": signals_available or [],
                "weights_used": weights_used or {},
                "selection_note": selection_note,
                "timings_seconds": timings or {},
                "clips": rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    performance_log = perf.add_clips([
        perf.NewClip(source_id=info.source_id, clip_id=r.plan.clip_id,
                     candidate_id=r.plan.candidate_id, campaign=campaign.name,
                     source_title=info.title or "", file=r.file.name,
                     duration_s=r.duration, caption=full_caption(r.plan),
                     opening=opening_label(r.plan.layout), lead_in_s=r.plan.lead_in,
                     hook=r.plan.hook_text if r.plan.hook_shown else "")
        for r in records])

    # Every accepted clip is filed in the library (data/library/<campaign>/),
    # which the Control Center shows; the run's own folder is only a work area.
    if records:
        from ..studio import library

        try:
            library.register(records, info=info, campaign=campaign)
        except OSError as exc:
            log.warning("could not file the clips in the library: %s", exc)

    report_md = out_dir / "report.md"
    report_md.write_text(
        _render_report(
            records, rejected or [], info=info, campaign=campaign,
            selection_note=selection_note, signals_available=signals_available or [],
            weights_used=weights_used or {}, timings=timings or {}, created_at=created,
        ),
        encoding="utf-8",
    )

    return {
        "manifest_csv": manifest_csv,
        "manifest_json": manifest_json,
        "performance_log": performance_log,
        "report_md": report_md,
    }


def opening_label(layout) -> str:
    """How a clip's first shot is framed, in words for the performance log."""
    if layout is None:
        return ""
    first = layout.segments[0].layout if layout.kind == "per_shot" and layout.segments else layout
    if first.kind == "follow_crop":
        return "full-screen"
    if first.kind == "fit_crop" and first.crop_height:
        return "fit 4:5" if first.crop_width / first.crop_height <= 0.81 else "fit wide"
    if first.kind == "blurred_fit":
        return "letterbox"
    return first.kind


def _row(record: ClipRecord, *, info: SourceInfo, campaign: CampaignConfig,
         created_at: str) -> dict:
    plan = record.plan
    return {
        "clip_id": plan.clip_id,
        "candidate_id": plan.candidate_id,
        "source_id": info.source_id,
        "source_title": info.title,
        "campaign": campaign.name,
        "start": round(plan.start, 3),
        "end": round(plan.end, 3),
        "duration": round(record.duration, 3),
        "composite": round(plan.composite, 5),
        "llm_a": _round(record.llm_a_total),
        "llm_b": _round(record.llm_b_total),
        "audio": _round(record.components.get("audio")),
        "heatmap": _round(record.components.get("heatmap")),
        "text": _round(record.components.get("text")),
        "layout": plan.layout.describe if plan.layout else "",
        "caption_style": plan.caption_style,
        "hook_text": plan.hook_text,
        "suggested_caption": full_caption(plan),
        "hashtags": " ".join(plan.hashtags),
        "credit_text": campaign.required_credit_text,
        "qa_status": record.qa.status,
        "compliance_status": record.compliance.status,
        "file": record.file.name,
        "created_at": created_at,
        "caption_fixes": "; ".join(f"{f.original} -> {f.replacement}"
                                   for f in record.caption_fixes),
    }


def _round(value: float | None, digits: int = 5) -> str:
    return "" if value is None else str(round(value, digits))


def _render_report(
    records: list[ClipRecord],
    rejected: list[ClipRecord],
    *,
    info: SourceInfo,
    campaign: CampaignConfig,
    selection_note: str,
    signals_available: list[str],
    weights_used: dict[str, float],
    timings: dict[str, float],
    created_at: str,
) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# {info.title or info.source_id}")
    add("")
    add(f"- **Source**: `{info.source_id}`  ({format_duration(info.media.duration)}, "
        f"{info.media.width}x{info.media.height})")
    if info.url:
        add(f"- **URL**: {info.url}")
    add(f"- **Campaign**: {campaign.name}")
    add(f"- **Authorization**: {campaign.source_authorization}")
    add(f"- **Generated**: {created_at}")
    add(f"- **Signals used**: {', '.join(signals_available) or 'none'}")
    if weights_used:
        add("- **Weights**: " + ", ".join(f"{k}={v:.2f}" for k, v in weights_used.items()))
    if timings:
        add("- **Timings**: " + ", ".join(f"{k} {v:.1f}s" for k, v in timings.items()))
    add("")

    if not records:
        add("## No clips")
        add("")
        add(f"Nothing met the quality bar. {selection_note}")
        add("")
        add("This is deliberate: returning nothing is better than returning filler, "
            "which earns no views and risks originality flags.")
        return "\n".join(lines) + "\n"

    add(f"## {len(records)} clip(s)")
    add("")
    add(f"{selection_note}")
    add("")

    for record in records:
        plan = record.plan
        add(f"### {plan.clip_id}: {plan.hook_text or '(no hook)'}")
        add("")
        add(f"- **File**: `{record.file.name}`")
        add(f"- **Source range**: {to_ffmpeg(plan.start)} to {to_ffmpeg(plan.end)} "
            f"({format_duration(record.duration)})")
        add(f"- **Composite**: {plan.composite:.3f}"
            + (f"  (LLM {record.raw['llm']:.2f}/10)" if "llm" in record.raw else ""))
        if record.components:
            add("- **Components**: "
                + ", ".join(f"{k} {v:.2f}" for k, v in record.components.items()))
        add(f"- **Layout**: {plan.layout.kind if plan.layout else 'unknown'}"
            + (f": {plan.layout.reason}" if plan.layout and plan.layout.reason else ""))
        add(f"- **QA**: {record.qa.status}")
        for check in record.qa.checks:
            if check.status != "pass":
                add(f"  - {check.status}: {check.name}, {check.detail}")
        add(f"- **Compliance**: {record.compliance.summary()}")
        add("")
        add("**Suggested caption**")
        add("")
        add(f"> {full_caption(plan)}")
        add("")
        if plan.refine_notes:
            add("<details><summary>Boundary refinement</summary>")
            add("")
            for note in plan.refine_notes:
                add(f"- {note}")
            add("")
            add("</details>")
            add("")
        add("**Transcript**")
        add("")
        add(f"> {plan.text}")
        add("")
        if record.caption_fixes:
            add("**Caption corrections** (misheard words fixed from context)")
            add("")
            for fix in record.caption_fixes:
                add(f"- {fix.time - plan.start:.1f}s: *{fix.original}* -> "
                    f"**{fix.replacement}** ({fix.reason})")
            add("")

    if rejected:
        add(f"## {len(rejected)} rejected")
        add("")
        add("These were rendered and then failed the automated QA gate. The files "
            "are kept in `rejected/` with a `.reason.json` beside each one.")
        add("")
        for record in rejected:
            reasons = "; ".join(c.detail for c in record.qa.failures) \
                or record.compliance.summary()
            add(f"- **{record.plan.clip_id}**: {reasons}")
        add("")

    add("## Next step")
    add("")
    add("Post the clips you like, then fill in their rows in the performance log "
        "(`data/performance.xlsx`) -- views, and above all average watch time -- and run:")
    add("")
    add("```powershell")
    add("clipper learn")
    add("```")
    add("")
    add("With fewer than about 20 logged clips it will only report descriptive "
        "statistics; it needs more than that before proposing new weights.")
    return "\n".join(lines) + "\n"


def write_rejection_reason(record: ClipRecord, path: Path) -> Path:
    """Write `<clip>.reason.json` beside a rejected clip (section 12)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "clip_id": record.plan.clip_id,
        "file": record.file.name,
        "start": round(record.plan.start, 3),
        "end": round(record.plan.end, 3),
        "qa_status": record.qa.status,
        "qa_failures": [
            {"check": c.name, "detail": c.detail,
             "measured": c.measured, "limit": c.limit}
            for c in record.qa.failures
        ],
        "compliance_status": record.compliance.status,
        "compliance_failures": [
            {"rule": r.name, "detail": r.detail} for r in record.compliance.failures
        ],
        "text": record.plan.text,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
