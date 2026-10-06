"""The text structure signal (docs/BUILD_BRIEF.md section 9.4).

Transparent rules plus a small weighted sum -- deliberately not a model. The
point of this signal is that when it disagrees with the LLM you can read the
feature values and see exactly why, which is what makes `clipper explain`
useful.

Every feature is in [0, 1] so the weights in `FEATURE_WEIGHTS` are directly
comparable and can be tuned by hand.
"""

from __future__ import annotations

import re

from ..models import Candidate

# A first sentence opening with one of these is self-contained enough to land
# without setup.
QUESTION_OPENERS = ("why", "what", "how", "when", "where", "who", "which",
                    "is", "are", "do", "does", "did", "can", "could", "should",
                    "would", "will", "have", "has")

CONTRAST_WORDS = ("but", "however", "although", "though", "instead", "actually",
                  "except", "yet", "despite", "whereas", "unless")

DIRECT_ADDRESS = ("you", "your", "you're", "yourself", "we", "our")

# Pronouns with no referent inside the clip -- the clearest textual signal that
# a window needs context that was cut away.
DANGLING_PRONOUNS = ("this", "that", "these", "those", "it", "they", "them",
                     "he", "she", "him", "her", "his", "hers", "their")

FILLER_WORDS = frozenset({
    "um", "uh", "er", "ah", "like", "so", "well", "yeah", "okay", "right",
    "basically", "actually", "literally", "anyway", "kinda", "sorta", "just",
    "you", "know", "i", "mean",
})

SETUP_PAYOFF_MARKERS = ("here's", "here is", "the reason", "the problem",
                        "the point", "turns out", "the truth", "what happened",
                        "the trick", "the answer", "which means", "so that",
                        "because", "the result")

_NUMBER_RE = re.compile(r"\b\d[\d,.]*\s*(?:%|percent|dollars?|years?|months?|days?|hours?|x|k|million|billion)?\b",
                        re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[a-z']+")

# Weights over the features below. Sum is normalised, so these are relative.
FEATURE_WEIGHTS: dict[str, float] = {
    "opens_with_question": 0.14,
    "has_numeric_claim": 0.12,
    "has_contrast": 0.08,
    "direct_address": 0.08,
    "first_sentence_self_contained": 0.20,
    "low_opening_filler": 0.16,
    "setup_payoff": 0.12,
    "sentence_rhythm": 0.10,
}


def first_sentence(text: str) -> str:
    parts = _SENTENCE_SPLIT_RE.split(text.strip(), maxsplit=1)
    return parts[0] if parts else text.strip()


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def features_for(candidate: Candidate, *, opening_words: int = 14) -> dict[str, float]:
    """Compute every text feature for one candidate. All values in [0, 1]."""
    text = candidate.text.strip()
    if not text:
        return dict.fromkeys(FEATURE_WEIGHTS, 0.0)

    lowered = text.lower()
    opener = first_sentence(text)
    opener_words = _words(opener)
    all_words = _words(text)

    return {
        "opens_with_question": _opens_with_question(opener, opener_words),
        "has_numeric_claim": 1.0 if _NUMBER_RE.search(text) else 0.0,
        "has_contrast": 1.0 if any(f" {w} " in f" {lowered} " for w in CONTRAST_WORDS) else 0.0,
        "direct_address": _direct_address(all_words),
        "first_sentence_self_contained": _self_contained(opener_words),
        "low_opening_filler": _low_opening_filler(all_words[:opening_words]),
        "setup_payoff": _setup_payoff(lowered),
        "sentence_rhythm": _sentence_rhythm(text),
    }


def _opens_with_question(opener: str, opener_words: list[str]) -> float:
    if opener.rstrip().endswith("?"):
        return 1.0
    if opener_words and opener_words[0] in QUESTION_OPENERS:
        return 0.6  # question-shaped without the mark
    return 0.0


def _direct_address(all_words: list[str]) -> float:
    """Density of second-person address, saturating quickly."""
    if not all_words:
        return 0.0
    hits = sum(1 for w in all_words if w in DIRECT_ADDRESS)
    return min(1.0, hits / max(4.0, len(all_words) * 0.04))


def _self_contained(opener_words: list[str]) -> float:
    """Whether the first sentence can be understood alone.

    A dangling pronoun in the first few words -- "that was the moment" -- points
    at something the viewer never saw. This is the single strongest textual
    predictor that a window was cut out of its context.
    """
    if not opener_words:
        return 0.0
    leading = opener_words[:3]
    if any(w in DANGLING_PRONOUNS for w in leading):
        return 0.0
    if any(w in DANGLING_PRONOUNS for w in opener_words[3:6]):
        return 0.5
    return 1.0


def _low_opening_filler(opening: list[str]) -> float:
    """1.0 when the opening is filler-free, falling to 0 as filler dominates."""
    if not opening:
        return 0.0
    filler = sum(1 for w in opening if w in FILLER_WORDS)
    return max(0.0, 1.0 - (filler / len(opening)) * 2.0)


def _setup_payoff(lowered: str) -> float:
    """Whether the clip contains a visible setup-payoff or question-answer turn."""
    markers = sum(1 for m in SETUP_PAYOFF_MARKERS if m in lowered)
    has_question_then_more = "?" in lowered and lowered.rstrip().index("?") < len(lowered) * 0.6
    score = min(1.0, markers / 2.0)
    return min(1.0, score + (0.3 if has_question_then_more else 0.0))


def _sentence_rhythm(text: str) -> float:
    """Variation in sentence length.

    Uniform sentence lengths read as monotone; some variation is what makes a
    delivery feel like speech rather than a list. Normalised so a coefficient of
    variation around 0.6 scores best.
    """
    lengths = [len(_words(s)) for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if len(lengths) < 2:
        return 0.3
    mean = sum(lengths) / len(lengths)
    if mean <= 0:
        return 0.0
    variance = sum((n - mean) ** 2 for n in lengths) / len(lengths)
    cv = (variance ** 0.5) / mean
    return max(0.0, 1.0 - abs(cv - 0.6) / 0.6)


def raw_score(features: dict[str, float]) -> float:
    """Weighted sum of the features, normalised to [0, 1]."""
    total = sum(FEATURE_WEIGHTS.values())
    if total <= 0:  # pragma: no cover - constant table
        return 0.0
    return round(
        sum(FEATURE_WEIGHTS[k] * features.get(k, 0.0) for k in FEATURE_WEIGHTS) / total, 5
    )


def score_candidates(
    candidates: list[Candidate],
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """Raw text scores and per-candidate feature detail."""
    raws: dict[str, float] = {}
    details: dict[str, dict[str, float]] = {}
    for candidate in candidates:
        features = features_for(candidate)
        details[candidate.candidate_id] = features
        raws[candidate.candidate_id] = raw_score(features)
    return raws, details
