"""Generating candidate windows.

BUILD_BRIEF.md section 8 opens by saying this stage "determines the ceiling of
everything else", and that is literally true: no amount of clever scoring can
select a moment that was never proposed. So generation is generous, and the hard
filters that follow are the only thing standing between it and an unbounded LLM
bill.

Order matters and is deliberate:

1. Enumerate windows that start and end on sentence boundaries.
2. Apply hard filters (silence, edges, sponsor reads) -- cheap, and they remove
   the windows most likely to waste an LLM call.
3. Dedupe near-identical windows by IoU.
4. Cap at `max_candidates` by a cheap pre-score.
"""

from __future__ import annotations

import re

from ..config import CandidatesConfig
from ..models import Candidate, Candidates, Sentence, Sentences, Transcript
from ..utils.logging import get_logger

log = get_logger(__name__)

# Phrases that mark a sponsor read. Matched case-insensitively against the
# window text. The LLM's `is_sponsor_or_ad` flag is the authority; this is the
# cheap pre-filter that stops obvious ads reaching it at all.
SPONSOR_PATTERNS = (
    r"\bthis (?:video|episode) is (?:sponsored|brought to you)\b",
    r"\bsponsored by\b",
    r"\bbrought to you by\b",
    r"\bthanks? to (?:our|today'?s) sponsor\b",
    r"\buse (?:my |the |our )?(?:code|promo code|discount code)\b",
    r"\bpromo code\b",
    r"\blink in (?:the )?(?:description|bio)\b",
    r"\bsmash (?:that )?like button\b",
    r"\b(?:like and )?subscribe to (?:the|my|our) channel\b",
    r"\bring the bell\b",
    r"\bpatreon\.com\b",
    r"\bfirst \d+ (?:people|listeners|viewers)\b",
    r"\bgo to [a-z0-9-]+\.com\/[a-z0-9-]+\b",
    r"\b\d{1,2}% off\b",
    r"\bfree trial\b",
)
_SPONSOR_RE = re.compile("|".join(SPONSOR_PATTERNS), re.IGNORECASE)

# A window must carry at least this much of a sponsor signal to be dropped.
# One "free trial" in an hour of conversation is not an ad read.
SPONSOR_HIT_THRESHOLD = 2

# Hook words that open a window strongly. Used only in the cheap pre-score;
# the real judgement is the LLM's.
HOOK_OPENERS = (
    "why", "how", "what", "when", "where", "who", "the reason", "here's",
    "i think", "the problem", "nobody", "everyone", "most people", "the truth",
    "i was", "i got", "imagine", "listen", "look", "the thing",
)

FILLER_WORDS = frozenset({
    "um", "uh", "er", "ah", "like", "so", "well", "yeah", "okay", "right",
    "basically", "actually", "literally", "anyway", "kinda", "sorta",
})


def generate(
    transcript: Transcript,
    sentences: Sentences,
    cfg: CandidatesConfig,
    *,
    source_duration: float,
) -> Candidates:
    """Produce the candidate set for one source."""
    items = sentences.sentences
    if not items:
        log.warning("no sentences; no candidates can be generated")
        return Candidates(source_id=transcript.source_id, candidates=[])

    raw = list(_enumerate_windows(items, cfg))
    log.debug("enumerated %d raw windows", len(raw))

    kept, dropped = _apply_hard_filters(raw, items, transcript, cfg,
                                        source_duration=source_duration)
    log.debug("hard filters kept %d, dropped %d", len(kept), sum(dropped.values()))
    for reason, count in sorted(dropped.items(), key=lambda kv: -kv[1]):
        if count:
            log.debug("  dropped %d: %s", count, reason)

    for candidate in kept:
        candidate.pre_score = pre_score(candidate, transcript)

    deduped = dedupe(kept, cfg.dedupe_iou)
    log.debug("dedupe kept %d of %d", len(deduped), len(kept))

    capped = sorted(deduped, key=lambda c: c.pre_score, reverse=True)[: cfg.max_candidates]
    capped.sort(key=lambda c: c.start)

    for position, candidate in enumerate(capped):
        candidate.candidate_id = f"c{position:03d}"

    log.info(
        "generated %d candidates from %d sentences (%d enumerated, %d after filters)",
        len(capped), len(items), len(raw), len(kept),
    )
    return Candidates(source_id=transcript.source_id, candidates=capped)


def _enumerate_windows(sentences: list[Sentence], cfg: CandidatesConfig):
    """Every sentence-aligned window whose duration is in bounds.

    For each starting sentence, walk forward and emit a window each time the
    running duration enters `[min_seconds, max_seconds]`. That naturally yields
    the short/medium/long variants section 8 asks for, without a separate pass.
    """
    for lo in range(len(sentences)):
        start = sentences[lo].start
        for hi in range(lo + 1, len(sentences) + 1):
            end = sentences[hi - 1].end
            duration = end - start

            if duration < cfg.min_seconds:
                continue
            if duration > cfg.max_seconds:
                break  # every longer window from this start is also too long

            yield Candidate(
                candidate_id=f"raw{lo:04d}_{hi:04d}",
                start=start,
                end=end,
                sentence_indices=(lo, hi),
                text=" ".join(s.text for s in sentences[lo:hi]).strip(),
            )


def _apply_hard_filters(
    candidates: list[Candidate],
    sentences: list[Sentence],
    transcript: Transcript,
    cfg: CandidatesConfig,
    *,
    source_duration: float,
) -> tuple[list[Candidate], dict[str, int]]:
    """Section 8's hard filters. Returns (kept, reason -> count)."""
    trim_edges = source_duration >= cfg.edge_trim_min_source_seconds
    head_limit = cfg.edge_trim_seconds if trim_edges else 0.0
    tail_limit = source_duration - cfg.edge_trim_seconds if trim_edges else source_duration

    kept: list[Candidate] = []
    dropped = {"intro_outro": 0, "silence": 0, "no_speech": 0, "sponsor": 0}

    for candidate in candidates:
        if trim_edges and (candidate.start < head_limit or candidate.end > tail_limit):
            dropped["intro_outro"] += 1
            continue

        words = transcript.words_between(candidate.start, candidate.end)
        if not words:
            dropped["no_speech"] += 1
            continue

        candidate.word_count = len(words)
        candidate.silence_ratio = silence_ratio(candidate, words)
        if candidate.silence_ratio > cfg.max_silence_ratio:
            dropped["silence"] += 1
            continue

        if is_sponsor_read(candidate.text):
            dropped["sponsor"] += 1
            continue

        kept.append(candidate)

    # Windows starting mid-sentence are impossible by construction here, since
    # every window begins at a sentence's own start index. Asserted rather than
    # filtered, so a future change to enumeration cannot break the invariant
    # silently.
    for candidate in kept:
        lo, hi = candidate.sentence_indices
        assert candidate.start == sentences[lo].start, "window does not start on a sentence"
        assert candidate.end == sentences[hi - 1].end, "window does not end on a sentence"

    return kept, dropped


def silence_ratio(candidate: Candidate, words: list) -> float:
    """Fraction of the window with no word sounding.

    Computed from word timings rather than audio, so it is free -- the audio
    signal in section 9.2 does the real acoustic work separately.
    """
    if candidate.duration <= 0:
        return 1.0
    speech = sum(max(0.0, min(w.end, candidate.end) - max(w.start, candidate.start))
                 for w in words)
    return max(0.0, min(1.0, 1.0 - speech / candidate.duration))


def is_sponsor_read(text: str) -> bool:
    """Whether a window looks like an ad read.

    Requires several distinct hits: publishers say "link in the description" in
    passing, and dropping every window containing one phrase would silently
    delete good moments.
    """
    hits = {m.group(0).lower() for m in _SPONSOR_RE.finditer(text)}
    return len(hits) >= SPONSOR_HIT_THRESHOLD


def pre_score(candidate: Candidate, transcript: Transcript) -> float:
    """Cheap ranking used only to cap how many candidates reach the LLM.

    Section 8 specifies "audio energy variance plus text hook heuristics". The
    audio half needs the decoded WAV, which this stage does not load, so this
    uses transcript-derived proxies: speech density, hook openers, and low filler
    at the start. Deliberately crude -- it decides what gets *considered*, not
    what gets picked.
    """
    words = transcript.words_between(candidate.start, candidate.end)
    if not words:
        return 0.0

    score = 0.0

    # Density: dead air is a bad sign, but so is a breathless wall of words.
    words_per_second = len(words) / candidate.duration
    score += 1.0 - min(1.0, abs(words_per_second - 2.6) / 2.6)

    lowered = candidate.text.lower().lstrip("\"'“ ")
    if any(lowered.startswith(opener) for opener in HOOK_OPENERS):
        score += 0.6

    # Filler density in the opening five seconds -- the part that has to hook.
    opening = [w for w in words if w.start < candidate.start + 5.0]
    if opening:
        filler = sum(1 for w in opening if w.normalized in FILLER_WORDS)
        score += 0.5 * (1.0 - filler / len(opening))

    # Questions and numbers both tend to mark a concrete claim.
    if "?" in candidate.text:
        score += 0.3
    if any(ch.isdigit() for ch in candidate.text):
        score += 0.2

    # Prefer the middle of the target duration band.
    score += 0.3 * (1.0 - min(1.0, abs(candidate.duration - 35.0) / 35.0))

    # Confidence: a window full of low-probability words is often noise or music.
    score += 0.4 * (sum(w.probability for w in words) / len(words))

    return round(score, 4)


def dedupe(candidates: list[Candidate], iou_threshold: float) -> list[Candidate]:
    """Drop windows that overlap an already-kept one above `iou_threshold`.

    Section 8 says to keep the higher-scoring of a near-identical pair, so this
    walks in descending pre-score order and keeps the first of each cluster.
    """
    ordered = sorted(candidates, key=lambda c: c.pre_score, reverse=True)
    kept: list[Candidate] = []
    for candidate in ordered:
        if any(candidate.iou(other) > iou_threshold for other in kept):
            continue
        kept.append(candidate)
    kept.sort(key=lambda c: c.start)
    return kept
