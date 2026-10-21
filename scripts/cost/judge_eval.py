"""Which model should judge the moments? Prompt A on one source's cached candidates, by several models, against
the owner's own ratings of that source's clips (D232). Spends AI: each model scores --limit candidates.

    python scripts/cost/judge_eval.py <source_id> --models claude-opus-5-5,claude-sonnet-5-5 --effort low --limit 45
        [--campaign <name>]

Ratings are the studio library's (`clips.rating`, Good / Not good, a posted clip counting as good), read the way
Learning reads them (learn/feedback.py `moment`). A candidate takes the rating of the clip it mostly lies in.
Rated candidates go first, the rest fill up to --limit so the batches are the size a real run sends. Prints, per
model: calls, tokens and cost (from usage.sqlite), the rank correlation of its totals with the ratings, and how
many of the top k rated moments it also put in its top k. The prompt carries no taste block: that is made from
these same ratings.
"""

from __future__ import annotations

import argparse
import contextlib
import time

#: A candidate takes a clip's rating when this much of the candidate lies inside the clip.
OVERLAP = 0.5


def match_ratings(candidates: list[tuple[str, float, float]], rated: list[tuple[float, float, float]]
                  ) -> dict[str, float]:
    """candidate id -> rating, for candidates (id, start, end) lying mostly inside a rated clip (start, end, rating);
    the clip it shares the most with wins."""
    out = {}
    for cid, start, end in candidates:
        if end <= start:
            continue
        best = max(((min(end, b) - max(start, a), r) for a, b, r in rated), default=(0.0, None))
        if best[1] is not None and best[0] / (end - start) >= OVERLAP:
            out[cid] = best[1]
    return out


def top_k_overlap(scores: dict[str, float], ratings: dict[str, float], k: int) -> float | None:
    """Share of the k best-rated moments that are also among the model's k best (of the rated ones). None when it
    can't tell anything: every rating the same (only tie order), or no more rated moments than k."""
    both = [c for c in ratings if c in scores]
    if len(both) <= k or len({ratings[c] for c in both}) < 2:
        return None
    by_rating = set(sorted(both, key=lambda c: ratings[c], reverse=True)[:k])
    by_model = set(sorted(both, key=lambda c: scores[c], reverse=True)[:k])
    return round(len(by_rating & by_model) / k, 3)


def pairwise(scores: dict[str, float], ratings: dict[str, float]) -> float | None:
    """The chance a better-rated moment outscores a worse-rated one (ties half): steadier than rho on a few ratings."""
    both = [c for c in ratings if c in scores]
    pairs = [(a, b) for a in both for b in both if ratings[a] > ratings[b]]
    if not pairs:
        return None
    return round(sum(1.0 if scores[a] > scores[b] else 0.5 if scores[a] == scores[b] else 0.0
                     for a, b in pairs) / len(pairs), 3)


def agreement(scores: dict[str, float], ratings: dict[str, float], k: int = 5) -> tuple[float | None, float | None, int]:
    """(Spearman rho, top-k overlap, n) of a model's totals against the ratings."""
    from clipper.learn.feedback import spearman

    both = [c for c in ratings if c in scores]
    return spearman([scores[c] for c in both], [ratings[c] for c in both]), top_k_overlap(scores, ratings, k), len(both)


def _ratings(source_id: str) -> list[tuple[float, float, float]]:
    from clipper.learn import feedback
    from clipper.studio import db

    with db.connect() as con:
        rows = [r for r in db.clips(con) if r.get("source_id") == source_id]
    by_id = {r["id"]: r for r in rows}
    out = []
    for clip in feedback.from_rows(rows):
        row = by_id[clip.id]
        if clip.moment is not None and row.get("start_s") is not None and row.get("end_s") is not None:
            out.append((float(row["start_s"]), float(row["end_s"]), clip.moment))
    return out


def _backend(config, model: str):
    from clipper import pipeline
    from clipper.llm.base import create

    if model.startswith("claude-"):
        backend = pipeline.judge_backend(config.model_copy(update={"llm": config.llm.model_copy(
            update={"judge_model": model})}))
        if backend is None:
            raise SystemExit(f"{model}: Claude isn't set up here (no API key allowed for judging, no Claude Code)")
        return backend
    return create(config.llm.backend, model=model, max_retries=config.llm.max_retries,
                  requests_per_minute=config.llm.requests_per_minute)


def _spent(ref: str) -> dict:
    from clipper.llm import usage

    with contextlib.closing(usage._connect()) as con:
        row = con.execute("SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0), "
                          "COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0), COALESCE(SUM(usd),0) FROM ai_calls "
                          "WHERE ref=?", (ref,)).fetchone()
    return dict(zip(("calls", "input", "output", "cache_read", "cache_write", "usd"), row, strict=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source_id")
    parser.add_argument("--models", default="claude-opus-5-5,claude-sonnet-5-5")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--limit", type=int, default=45)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--campaign", help="apply this campaign's focus and rules, as a real run would")
    args = parser.parse_args()

    from clipper.config import CampaignConfig, Config
    from clipper.llm import anthropic_backend, claude_code, usage
    from clipper.llm.cache import LLMCache
    from clipper.models import Candidates
    from clipper.paths import campaigns_dir, work_dir
    from clipper.runner import campaign_config
    from clipper.signals.llm import combine_totals, score_candidates

    config = Config.load()
    if args.campaign:
        config = campaign_config(config, CampaignConfig.load(campaigns_dir() / f"{args.campaign}.yaml"))
    config = config.model_copy(update={"llm": config.llm.model_copy(
        update={"use_second_opinion": False, "user_taste": ""})})
    # Every judge_a call at --effort, whatever Settings say (the backends read it per job).
    for module in (anthropic_backend, claude_code):
        module.effort = lambda job: args.effort
    candidates = [c for c in Candidates.load(work_dir(args.source_id) / "candidates.json").candidates
                  if not c.quiet]
    ratings = match_ratings([(c.candidate_id, c.start, c.end) for c in candidates], _ratings(args.source_id))
    if not ratings:
        raise SystemExit(f"no rated clips of {args.source_id} line up with its cached candidates")
    chosen = sorted(candidates, key=lambda c: c.candidate_id not in ratings)[:args.limit]
    print(f"{len(chosen)} candidates, {sum(c.candidate_id in ratings for c in chosen)} of them rated")

    run = f"eval:{int(time.time())}"
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        backend, ref = _backend(config, model), f"{run}:{model}"
        with usage.tag(ref):   # nothing cached: every model answers every candidate
            result = score_candidates(chosen, backend, config.llm, cache=LLMCache(enabled=False))
        # A single opinion's "needs earlier context" drops differ by model, so compare on every answered moment too:
        # otherwise each model is scored on a different set (package M found Sonnet dropping 0-70% run to run).
        weights = config.llm.rubric_weights.as_dict()
        answered = {cid: combine_totals(a, None, weights, 0.0) for cid, (a, _b) in result.scores.items() if a is not None}
        rho, top, n = agreement(result.totals, ratings, args.k)
        all_rho, _, all_n = agreement(answered, ratings, args.k)
        spent = _spent(ref)
        print(f"{model}: {spent['calls']} calls, {spent['input']} in / {spent['output']} out tokens "
              f"({spent['cache_read']} cache read, {spent['cache_write']} cache write), ${spent['usd']:.3f}; "
              f"scored {len(result.totals)} (dropped {len(result.drops)}), rho {rho} over {n} rated, "
              f"top-{args.k} overlap {top}; every answered moment: rho {all_rho} over {all_n} rated, "
              f"good beats bad {pairwise(answered, ratings)}")


if __name__ == "__main__":
    main()
