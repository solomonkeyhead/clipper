"""What a run did with every moment it found, in words a user can act on.

After a job the Control Center shows: how many moments Clipper found, how
many cleared the quality bar, why each of the others was left out (grouped),
and the best near misses -- with their times, so one can be made anyway. The
research's advice (D63): a low clip count is only fair if the user can see why.
"""

from __future__ import annotations

from collections import Counter

#: Selection's internal reasons (select/pick.py, signals/llm.py) -> plain words.
PLAIN = [
    ("below the absolute minimum", "Scored below the quality bar"),
    ("below min_composite", "Among the weakest moments in the video"),
    ("overlaps", "Overlaps a better moment"),
    ("minimum gap", "Too close to a better moment"),
    ("third already has", "Too many from the same part of the video"),
    ("beyond the requested", "Past the number of clips you asked for"),
    ("same material as", "Repeats a moment already scored"),
    ("sponsor", "An ad or sponsor read"),
    ("policy risk", "Risky under platform rules"),
    ("prior context", "Needs earlier scenes to make sense"),
]
OTHER = "Didn't pass the first checks"
FAILED_QA = "Failed the final quality check"


def plain(reason: str) -> str:
    for marker, words in PLAIN:
        if marker in reason:
            return words
    return OTHER


def build(outcome, selection, result, *, bar: float, limit: int, near: int = 5) -> dict:
    """The report for one automatic run (runner.run)."""
    scored = outcome.scored.scored
    candidates = {c.candidate_id: c for c in outcome.candidates.candidates}
    made = {r.plan.candidate_id for r in result.accepted}
    reasons: Counter[str] = Counter(plain(r) for r in selection.rejections.values())
    if result.rejected:
        reasons[FAILED_QA] += len(result.rejected)
    cleared = sum(1 for s in scored if not s.dropped
                  and (s.raw.get("llm") is None or s.raw["llm"] >= bar))
    misses = sorted((s for s in scored if not s.dropped and s.candidate_id not in made
                     and s.candidate_id in candidates),
                    key=lambda s: (s.raw.get("llm") or 0, s.composite), reverse=True)[:near]
    return {
        "mode": "auto",
        "moments": len(scored),
        "cleared": cleared,
        "bar": bar,
        "limit": limit,
        "made": len(result.accepted),
        "reasons": [{"reason": r, "count": n} for r, n in reasons.most_common()],
        "near_misses": [{
            "start": round(candidates[s.candidate_id].start, 1),
            "end": round(candidates[s.candidate_id].end, 1),
            "score": round(s.raw["llm"], 1) if s.raw.get("llm") is not None else None,
            "why": plain(selection.rejections.get(s.candidate_id, "")),
            "text": " ".join(candidates[s.candidate_id].text.split())[:220],
        } for s in misses],
    }


def manual(ranges: list[tuple[float, float]], result) -> dict:
    """The report for a hand-picked run (runner.cut)."""
    return {"mode": "manual", "moments": len(ranges), "cleared": len(ranges), "bar": None,
            "limit": len(ranges), "made": len(result.accepted),
            "reasons": ([{"reason": FAILED_QA, "count": len(result.rejected)}]
                        if result.rejected else []),
            "near_misses": []}
