"""Warn before posting a moment that's already posted.

Vyro bans "posting the same Clipper Content multiple times on the same Clipper
Account", and Instagram (April 2026) stops recommending accounts that mostly
post near-identical content (D63). So each clip lists the already-posted clips
it repeats, and on which accounts, before the user posts it:

* the same source file with at least half the shorter clip's time in common, or
* the same lines (a re-download of the same episode has a new source id):
  most of the shorter clip's meaningful words also appear in the other.
"""

from __future__ import annotations

import json
import re

MIN_TIME_SHARE = 0.5
MIN_WORD_SHARE = 0.6
MIN_WORDS = 12
#: Words too common to tell two scenes apart.
COMMON = set("""the and you that this was for are but not have with what just his her they
him she its it's i'm don't can't all out get got like know yeah okay well there their then
them from your one about would could should been were when who how why because really
right going gonna want think see come look said say here now some more very too also
did does doing make made take thing things time way even back still only into over
""".split())  # noqa: SIM905 (a word list reads better as text)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", (text or "").lower())
            if len(w) >= 3 and w not in COMMON}


def _time_share(a: dict, b: dict) -> float:
    if a.get("source_id") != b.get("source_id") or None in (a.get("start_s"), a.get("end_s"),
                                                            b.get("start_s"), b.get("end_s")):
        return 0.0
    shared = min(a["end_s"], b["end_s"]) - max(a["start_s"], b["start_s"])
    shortest = min(a["end_s"] - a["start_s"], b["end_s"] - b["start_s"])
    return max(0.0, shared) / shortest if shortest > 0 else 0.0


def _word_share(a: set[str], b: set[str]) -> float:
    """How much of the shorter clip's vocabulary the other one also has."""
    if len(a) < MIN_WORDS or len(b) < MIN_WORDS:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def find(rows: list[dict], posted: dict[int, list[str]]) -> dict[int, list[dict]]:
    """clip id -> the posted clips it repeats: [{id, title, how, posted_on}].

    `rows` are db clip rows; `posted` maps a clip id to where it's posted
    ("TikTok @me"), for the clips that are.
    """
    words = {r["id"]: _words(json.loads(r.get("scores") or "{}").get("text") or "") for r in rows}
    out: dict[int, list[dict]] = {}
    targets = [r for r in rows if posted.get(r["id"])]
    for row in rows:
        for other in targets:
            if other["id"] == row["id"]:
                continue
            how = ("same moment" if _time_share(row, other) >= MIN_TIME_SHARE
                   else "same lines" if _word_share(words[row["id"]], words[other["id"]]) >= MIN_WORD_SHARE
                   else "")
            if how:
                out.setdefault(row["id"], []).append({
                    "id": other["id"], "title": other.get("title") or other.get("clip_id") or "",
                    "how": how, "posted_on": posted[other["id"]]})
    return out
