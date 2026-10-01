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

import itertools
import re

from ..config import CandidatesConfig
from ..models import Candidate, Candidates, Scene, Scenes, Sentence, Sentences, Transcript
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

# The lines leading into a wordless stretch belong to it (its setup), back to a
# real pause and at most this far; the reaction after it, if this close.
SETUP_SECONDS = 15.0
SETUP_PAUSE = 3.0
REACTION_SECONDS = 8.0

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
    scenes: Scenes | None = None,
) -> Candidates:
    """Produce the candidate set for one source."""
    items = sentences.sentences
    if not items:
        log.warning("no sentences; no candidates can be generated")
        return Candidates(source_id=transcript.source_id, candidates=[])

    raw = list(_enumerate_windows(items, cfg))
    log.debug("enumerated %d raw windows", len(raw))
    if scenes is not None and scenes.scenes:
        before = len(raw)
        raw = _within_scenes(raw, items, scenes, cfg)
        log.info("scene-aware: %d of %d windows lie within one scene and start "
                 "and end on a scene edge or a beat", len(raw), before)

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

    # 60 is plenty for an episode; a two-hour source needs a pool its size (D71).
    pool = max(cfg.max_candidates, round(source_duration / 60 * CANDIDATES_PER_MINUTE))
    capped = spread_cap(deduped, pool, MAX_COVERAGE)
    capped.sort(key=lambda c: c.start)

    for position, candidate in enumerate(capped):
        candidate.candidate_id = f"c{position:03d}"

    if cfg.quiet_moments:
        quiet = quiet_windows(items, transcript, cfg, source_duration=source_duration, scenes=scenes)
        if quiet:
            log.info("%d moment(s) with no dialogue, to be judged by watching", len(quiet))
        capped += quiet

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


def _within_scenes(windows: list[Candidate], sentences: list[Sentence],
                   scenes: Scenes, cfg: CandidatesConfig) -> list[Candidate]:
    """Keep windows inside one scene that start and end on a natural edge.

    A window may begin at its scene's first line or at a line after a pause of
    at least `beat_gap`, and end at the scene's last line or before a longer
    one (`beat_end_gap`) -- never mid-exchange, the "ends weirdly in the middle of a scene"
    reported on real clips. At a scene's own edges the window snaps to the
    scene's camera cut, so a scene's opening (often wordless) is included and
    nothing of the neighbouring scene is.
    """
    by_sentence: dict[int, Scene] = {}
    for scene in scenes.scenes:
        for i in range(scene.first_sentence, scene.last_sentence + 1):
            by_sentence[i] = scene

    kept: list[Candidate] = []
    for window in windows:
        lo, hi = window.sentence_indices
        scene = by_sentence.get(lo)
        if scene is None or by_sentence.get(hi - 1) is not scene:
            continue
        opens = lo == scene.first_sentence
        closes = hi - 1 == scene.last_sentence
        if not opens and sentences[lo].gap_before < cfg.beat_gap:
            continue
        if not opens and _continues(sentences[lo].text):
            # "a threesome on my bed?" after 13s with no words: the
            # transcription missed "why the fuck were you having", which is
            # still in the audio. Starting there begins mid-sentence.
            continue
        if not closes and hi < len(sentences) and sentences[hi].gap_before < cfg.beat_end_gap:
            continue
        start = scene.start if opens else window.start
        end = scene.end if closes else window.end
        if not cfg.min_seconds <= end - start <= cfg.max_seconds:
            continue
        kept.append(window.model_copy(update={
            "start": start, "end": end,
            "scene_start": scene.start, "scene_end": scene.end,
        }))
    return kept


def quiet_windows(sentences: list[Sentence], transcript: Transcript, cfg: CandidatesConfig, *,
                  source_duration: float, scenes: Scenes | None = None) -> list[Candidate]:
    """Windows around the longest stretches with no dialogue.

    Every other window is built from sentences, so a scene that plays out in
    looks -- the Chad Powers brief's Episode 4 field scene, some 40 seconds
    without a line -- never became a candidate. Each wordless gap of at least
    `quiet_gap` seconds gets one window: the line before it (the setup) through
    the line after it (the payoff), when each is close, kept inside its scene
    and within the length bounds. Longest gaps first, at most `max_quiet`.
    """
    trim = source_duration >= cfg.edge_trim_min_source_seconds
    head = cfg.edge_trim_seconds if trim else 0.0
    tail = source_duration - cfg.edge_trim_seconds if trim else source_duration
    # Between two lines only: before the first and after the last are titles,
    # logos and credits far more often than a scene.
    gaps = [(after.start - before.end, i, before, after)
            for i, (before, after) in enumerate(itertools.pairwise(sentences), start=1)
            if after.start - before.end >= cfg.quiet_gap]
    out: list[Candidate] = []
    for _, i, before, after in sorted(gaps, key=lambda g: -g[0]):
        if len(out) >= cfg.max_quiet:
            break
        g_start, g_end = before.end, after.start
        lo = _setup_start(sentences, i, g_start)
        hi = i + 1 if after.end - g_end <= REACTION_SECONDS else i
        start = sentences[lo].start if lo < i else g_start
        end = sentences[hi - 1].end if hi > i else g_end
        scene = _scene_at((g_start + g_end) / 2, scenes)
        if scene is not None:
            start, end = max(start, scene.start), min(end, scene.end)
        end = min(end, start + cfg.max_seconds)
        if end - start < cfg.min_seconds or start < head or end > tail:
            continue
        window = Candidate(
            candidate_id=f"q{len(out):03d}", start=round(start, 3), end=round(end, 3),
            sentence_indices=(lo, max(lo, hi)), quiet=True,
            text=" ".join(w.text for w in transcript.words_between(start, end)).strip(),
            scene_start=scene.start if scene else None, scene_end=scene.end if scene else None)
        if any(window.overlaps(o) for o in out):
            continue
        out.append(window)
    return sorted(out, key=lambda c: c.start)


def _setup_start(sentences: list[Sentence], i: int, g_start: float) -> int:
    """The first line of the exchange leading into a wordless stretch at `i`.

    Walks back over lines said without a real pause (`SETUP_PAUSE`), at most
    `SETUP_SECONDS` before the silence: in Chad Powers ep. 4 the 38s look follows
    "Tell me to walk away. Right now. Tell me. I will walk away. I'll disappear.
    I promise you I will." -- the silence means nothing without all of it.
    """
    lo = i
    while (lo > 0 and g_start - sentences[lo - 1].start <= SETUP_SECONDS
           and (lo == i or sentences[lo].gap_before < SETUP_PAUSE)):
        lo -= 1
    return lo


def _scene_at(t: float, scenes: Scenes | None) -> Scene | None:
    if scenes is None:
        return None
    return next((s for s in scenes.scenes if s.start <= t < s.end), None)


def _continues(text: str) -> bool:
    """A line starting in lower case continues something said before it."""
    first = text.lstrip(" \"'([-—….")[:1]
    return first.islower()


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
    # every window begins at a sentence's own start index -- or, for scripted
    # TV, at its scene's opening or closing camera cut. Asserted rather than
    # filtered, so a future change to enumeration cannot break the invariant
    # silently.
    for candidate in kept:
        lo, hi = candidate.sentence_indices
        assert candidate.start in (sentences[lo].start, candidate.scene_start), (
            "window does not start on a sentence or a scene edge")
        assert candidate.end in (sentences[hi - 1].end, candidate.scene_end), (
            "window does not end on a sentence or a scene edge")

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


# No instant of the source may be covered by more than this many candidates.
MAX_COVERAGE = 3
#: Candidate windows per minute of source, once past `max_candidates`.
CANDIDATES_PER_MINUTE = 2.5


def spread_cap(candidates: list[Candidate], limit: int, max_coverage: int) -> list[Candidate]:
    """The best `limit` candidates by pre-score, spread across the source.

    A plain top-`limit` by pre-score let near-duplicates of one strong stretch
    take the whole budget. Measured on a 13-minute TV episode: 43 of 48
    candidates were variations of one two-minute scene, the LLM never saw the
    other eleven minutes, and one clip came out of a request for four. Dedupe
    at IoU 0.8 does not prevent it -- windows sharing a start sentence overlap
    heavily without crossing that line.

    So candidates are taken in pre-score order, skipping any that would put a
    moment under more than `max_coverage` candidates. A few boundary variants
    of each moment survive, which is what the LLM needs to choose the best
    cut; the rest of the budget goes elsewhere. Anything skipped fills in
    afterwards if the budget is not yet spent.
    """
    ordered = sorted(candidates, key=lambda c: c.pre_score, reverse=True)
    chosen: list[Candidate] = []
    skipped: list[Candidate] = []
    for candidate in ordered:
        if len(chosen) >= limit:
            break
        if _peak_coverage(candidate, chosen) < max_coverage:
            chosen.append(candidate)
        else:
            skipped.append(candidate)
    for candidate in skipped:
        if len(chosen) >= limit:
            break
        chosen.append(candidate)
    return chosen


def _peak_coverage(candidate: Candidate, chosen: list[Candidate]) -> int:
    """Most chosen candidates overlapping any single instant of `candidate`."""
    overlapping = [c for c in chosen if c.start < candidate.end and candidate.start < c.end]
    if not overlapping:
        return 0
    # Coverage only changes at window edges, so checking those is exact.
    points = {max(candidate.start, c.start) for c in overlapping} | {candidate.start}
    return max(sum(1 for c in overlapping if c.start <= t < c.end) for t in points)


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
