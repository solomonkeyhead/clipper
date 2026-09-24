"""The performance log: one CSV for every clip, across every run and campaign.

Each run appends a row per accepted clip, with the identifying columns filled
in; the user fills in the numbers for the ones they post. One file, not one per
source: per-source templates were written once and never refreshed, so after a
re-run they listed clips that no longer existed.

Only rows with some result logged are analysed; unposted clips stay blank.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ..paths import data_root, ensure

#: Written by the tool.
ID_COLUMNS = ["source_id", "clip_id", "candidate_id", "campaign", "source_title",
              "file", "duration_s"]
#: Filled in by the user. Watch-through (avg_watch_s, watched_full_pct) is the
#: measure that matters on a new account: views mostly reflect how many people
#: TikTok showed the post to, which is set by the account, not the clip.
RESULT_COLUMNS = ["platform", "account", "url", "posted_at",
                  "views_24h", "views_7d", "views_30d",
                  "likes", "comments", "shares", "saves",
                  "avg_watch_s", "watched_full_pct", "new_followers",
                  "verified_views", "payout_usd", "notes"]
COLUMNS = ID_COLUMNS + RESULT_COLUMNS
NUMERIC = ["views_24h", "views_7d", "views_30d", "likes", "comments", "shares", "saves",
           "avg_watch_s", "watched_full_pct", "new_followers", "verified_views", "payout_usd"]


def log_path() -> Path:
    return data_root() / "performance.csv"


@dataclass(frozen=True)
class NewClip:
    """What a run knows about a clip when it adds it to the log."""

    source_id: str
    clip_id: str
    candidate_id: str
    campaign: str
    source_title: str
    file: str
    duration_s: float


def read(path: Path | None = None) -> list[dict[str, str]]:
    path = path or log_path()
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:  # -sig: Excel adds a BOM
        return [dict(row) for row in csv.DictReader(handle)]


def write(rows: list[dict[str, str]], path: Path | None = None) -> Path:
    path = path or log_path()
    ensure(path.parent)
    extra = sorted({k for r in rows for k in r} - set(COLUMNS) - {None})
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS + extra, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") or "" for k in COLUMNS + extra})
    tmp.replace(path)
    return path


def add_clips(clips: list[NewClip], path: Path | None = None) -> Path:
    """Append rows for clips not in the log yet. Existing rows are never changed."""
    rows = read(path)
    known = {(r.get("source_id", ""), r.get("clip_id", "")) for r in rows}
    for clip in clips:
        if (clip.source_id, clip.clip_id) in known:
            continue
        rows.append({"source_id": clip.source_id, "clip_id": clip.clip_id,
                     "candidate_id": clip.candidate_id, "campaign": clip.campaign,
                     "source_title": clip.source_title, "file": clip.file,
                     "duration_s": f"{clip.duration_s:.1f}"})
        known.add((clip.source_id, clip.clip_id))
    return write(rows, path)


def number(value: str | None) -> float | None:
    """A logged number, tolerating what people type: '1,234', '12.5%', '3.4K', '1.2M'."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "").replace("$", "")
    if not text:
        return None
    scale = 1.0
    if text[-1:].lower() in ("k", "m"):
        scale = 1_000.0 if text[-1].lower() == "k" else 1_000_000.0
        text = text[:-1]
    try:
        return float(text) * scale
    except ValueError:
        return None


def has_results(row: dict[str, str]) -> bool:
    return any(number(row.get(k)) is not None for k in NUMERIC)
