"""`clipper learn`: compare what the tool predicted with what posts actually did.

BUILD_BRIEF.md section 14.2, with one deliberate change: the primary outcome is
watch-through (average watch time / clip length), not views. On a new account
views mostly measure how many people TikTok chose to show the post to -- the
account, not the clip -- while watch-through measures what the people who saw
it did. Views are still reported alongside.

With fewer than `MIN_ROWS` logged clips this only describes; it proposes and
changes nothing. With enough, it reports rank correlations with bootstrap
intervals and proposes weights moved a small step (`STEP`) toward what the
evidence favours. Nothing is written without `--apply`.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ..paths import REPO_ROOT, data_root, ensure, examples_dir
from . import log as perf

MIN_ROWS = 20
STEP = 0.2  # fraction of the way toward the evidence-implied weights, per run
SIGNALS = ["llm", "audio", "text", "heatmap"]
RUBRIC = ["hook_strength", "standalone_clarity", "payoff", "emotional_intensity",
          "quotability", "ending_completeness"]
HISTORY = REPO_ROOT / "config" / "weights_history.json"


@dataclass
class Joined:
    """One posted clip: what it did and what the tool thought of it."""

    row: dict[str, str]
    duration: float | None
    scores: dict[str, float] = field(default_factory=dict)
    text: str = ""
    hook: str = ""

    def get(self, key: str) -> float | None:
        return perf.number(self.row.get(key))

    @property
    def label(self) -> str:
        """The start of the caption: how the user knows the post (and how Studio lists it)."""
        caption = " ".join((self.row.get("caption") or "").split())
        if caption:
            return caption if len(caption) <= 26 else caption[:24].rstrip() + ".."
        return f"{self.row.get('source_title') or self.row.get('source_id', '')[:8]} {self.row.get('clip_id', '')}".strip()

    @property
    def views(self) -> float | None:
        for key in ("views_7d", "views_30d", "views_24h"):
            if (v := self.get(key)) is not None:
                return v
        return None

    @property
    def watch_through(self) -> float | None:
        avg = self.get("avg_watch_s")
        if avg is None or not self.duration:
            return None
        return avg / self.duration

    @property
    def watched_full(self) -> float | None:
        pct = self.get("watched_full_pct")
        return None if pct is None else pct / 100.0

    @property
    def engagement(self) -> float | None:
        views = self.views
        parts = [self.get(k) for k in ("likes", "comments", "shares", "saves")]
        if not views or all(p is None for p in parts):
            return None
        return sum(p or 0.0 for p in parts) / views


@dataclass
class Correlation:
    name: str
    target: str
    n: int
    rho: float
    low: float
    high: float


@dataclass
class Proposal:
    current: dict[str, float]
    proposed: dict[str, float]
    evidence: list[Correlation]


@dataclass
class Analysis:
    joined: list[Joined]
    unmatched: list[dict[str, str]]
    correlations: list[Correlation] = field(default_factory=list)
    proposal: Proposal | None = None
    note: str = ""


# ---- joining -------------------------------------------------------------------


class _Scores:
    """Per-source score artifacts, loaded once."""

    def __init__(self, work_root: Path):
        self.work_root = work_root
        self.cache: dict[str, tuple[dict, dict, dict]] = {}

    def load(self, source_id: str) -> tuple[dict, dict, dict]:
        if source_id not in self.cache:
            work = self.work_root / source_id
            scored = _read_json(work / "scored.json").get("scored", [])
            signals = _read_json(work / "signals.json").get("values", [])
            cands = _read_json(work / "candidates.json").get("candidates", [])
            self.cache[source_id] = ({s["candidate_id"]: s for s in scored},
                                     {v["candidate_id"]: v for v in signals},
                                     {c["candidate_id"]: c for c in cands})
        return self.cache[source_id]

    def for_row(self, row: dict[str, str]) -> Joined | None:
        source_id, candidate_id = row.get("source_id", ""), row.get("candidate_id", "")
        if not source_id or not candidate_id:
            return None
        scored, signals, cands = self.load(source_id)
        s = scored.get(candidate_id)
        if s is None:
            return None
        scores = {"composite": float(s["composite"])}
        scores.update({k: float(v) for k, v in s.get("components", {}).items()})
        sig = signals.get(candidate_id, {})
        if sig.get("llm_total") is not None:
            scores["llm_total"] = float(sig["llm_total"])
        rubrics = [r for r in (sig.get("llm_a"), sig.get("llm_b")) if isinstance(r, dict)]
        for item in RUBRIC:
            vals = [float(r[item]) for r in rubrics if isinstance(r.get(item), (int, float))]
            if vals:
                scores[item] = sum(vals) / len(vals)
        hook = next((r.get("hook_text", "") for r in rubrics if r.get("hook_text")), "")
        return Joined(row=row, duration=perf.number(row.get("duration_s")), scores=scores,
                      text=cands.get(candidate_id, {}).get("text", ""), hook=hook)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---- statistics ------------------------------------------------------------------


def _ranks(x: np.ndarray) -> np.ndarray:
    order = x.argsort(kind="mergesort")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=float)
    for value in np.unique(x):  # ties share their mean rank
        tied = x == value
        ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(x: list[float], y: list[float]) -> float:
    a, b = _ranks(np.asarray(x, float)), _ranks(np.asarray(y, float))
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def bootstrap_ci(x: list[float], y: list[float], *, reps: int = 2000,
                 seed: int = 7) -> tuple[float, float]:
    """95% interval for Spearman's rho by resampling clips."""
    rng = np.random.default_rng(seed)
    xs, ys = np.asarray(x, float), np.asarray(y, float)
    n = len(xs)
    rhos = []
    for _ in range(reps):
        idx = rng.integers(0, n, n)
        rhos.append(spearman(xs[idx].tolist(), ys[idx].tolist()))
    return float(np.percentile(rhos, 2.5)), float(np.percentile(rhos, 97.5))


def correlate(joined: list[Joined], name: str, target: str) -> Correlation | None:
    pairs = [(j.scores[name], _target(j, target)) for j in joined
             if name in j.scores and _target(j, target) is not None]
    if len(pairs) < 3:
        return None
    x, y = [p[0] for p in pairs], [p[1] for p in pairs]
    low, high = bootstrap_ci(x, y)
    return Correlation(name, target, len(pairs), spearman(x, y), low, high)


def _target(j: Joined, target: str) -> float | None:
    if target == "watch_through":
        return j.watch_through
    if target == "views":
        v = j.views
        return None if v is None else math.log1p(v)
    raise ValueError(target)


# ---- the analysis ------------------------------------------------------------------


def analyse(rows: list[dict[str, str]], current_weights: dict[str, float], *,
            work_root: Path | None = None) -> Analysis:
    scores = _Scores(work_root or data_root() / "work")
    posted = [r for r in rows if perf.has_results(r)]
    joined, unmatched = [], []
    for row in posted:
        j = scores.for_row(row)
        (joined if j else unmatched).append(j or row)
    result = Analysis(joined=joined, unmatched=unmatched)
    usable = [j for j in joined if j.watch_through is not None]
    if len(usable) < MIN_ROWS:
        result.note = (f"{len(usable)} clip(s) with watch time logged; {MIN_ROWS} are needed "
                       "before any weight is changed. Descriptive statistics only.")
        return result
    for target in ("watch_through", "views"):
        for name in ["composite", *SIGNALS, "llm_total", *RUBRIC]:
            c = correlate(joined, name, target)
            if c:
                result.correlations.append(c)
    result.proposal = propose(current_weights, [c for c in result.correlations
                                                if c.target == "watch_through" and c.name in SIGNALS])
    return result


def propose(current: dict[str, float], evidence: list[Correlation]) -> Proposal | None:
    """Move present signals' weights a small step toward their share of positive rho."""
    present = {c.name: c for c in evidence}
    if not present:
        return None
    positive = {k: max(c.rho, 0.0) for k, c in present.items()}
    total = sum(positive.values())
    if total <= 0:
        return None
    budget = sum(current.get(k, 0.0) for k in present)
    proposed = dict(current)
    for k in present:
        target = budget * positive[k] / total
        proposed[k] = round(current.get(k, 0.0) + STEP * (target - current.get(k, 0.0)), 3)
    if all(abs(proposed[k] - current.get(k, 0.0)) < 0.005 for k in present):
        return None
    return Proposal(current=dict(current), proposed=proposed, evidence=list(present.values()))


# ---- applying ----------------------------------------------------------------------


def apply(proposal: Proposal, config_path: Path, *, n: int,
          history_path: Path = HISTORY) -> None:
    """Write the proposed weights into the YAML config, keeping its comments."""
    text = config_path.read_text(encoding="utf-8")
    block = re.search(r"(?ms)^weights:.*?(?=^\S)", text)
    if block is None:
        raise ValueError(f"no weights: section in {config_path}")
    section = block.group(0)
    for key, value in proposal.proposed.items():
        section = re.sub(rf"(?m)^(\s+{key}:\s*)[0-9.]+", rf"\g<1>{value:.3f}", section)
    config_path.write_text(text[:block.start()] + section + text[block.end():], encoding="utf-8")

    history = []
    if history_path.exists():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
    history.append({"at": datetime.now(UTC).isoformat(timespec="seconds"), "clips": n,
                    "from": proposal.current, "to": proposal.proposed,
                    "evidence": [c.__dict__ for c in proposal.evidence]})
    ensure(history_path.parent)
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def write_examples(joined: list[Joined], *, share: float = 0.25) -> Path | None:
    """The best-watched clips as few-shot examples (used only if the config flag is on)."""
    ranked = sorted((j for j in joined if j.watch_through is not None),
                    key=lambda j: j.watch_through or 0.0, reverse=True)
    if len(ranked) < MIN_ROWS:
        return None
    top = ranked[:max(3, int(len(ranked) * share))]
    path = ensure(examples_dir()) / "top_clips.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for j in top:
            # Keys as llm/prompts.py reads them.
            views = j.views
            handle.write(json.dumps({"text": j.text, "hook": j.hook,
                                     "views": None if views is None else int(views),
                                     "watch_through": j.watch_through},
                                    ensure_ascii=False) + "\n")
    return path
