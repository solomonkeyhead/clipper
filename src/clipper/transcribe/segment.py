"""Words -> sentences -> paragraphs.

This is the foundation candidate generation stands on: BUILD_BRIEF.md section 8
requires every candidate window to begin and end on a sentence boundary, so a
bad boundary here caps the quality of everything downstream.

Two independent cues are used, because neither alone is reliable on speech:

* **Punctuation.** Whisper punctuates, but it drops terminal marks on trailing-off
  speech and inserts them mid-thought on hesitations.
* **Pauses.** A gap over `pause_boundary` forces a break even with no punctuation,
  which catches the trailing-off case.

A sentence records which cue ended it (`ends_with_terminal_punctuation`), because
refinement in section 10 prefers to *end* a clip on real punctuation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Sentence, Sentences, Transcript, Word

TERMINAL_PUNCTUATION = (".", "!", "?", "…")
# Trailing marks stripped before checking for a terminal one, e.g. `end."` or `end.)`
TRAILING_MARKS = '"\'”’)]}'  # noqa: RUF001

# Abbreviations whose full stop does not end a sentence.
ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "inc", "ltd",
    "co", "corp", "eg", "ie", "al", "approx", "dept", "est", "fig", "no", "vol",
    "a.m", "p.m", "u.s", "u.k", "e.g", "i.e",
})


@dataclass(frozen=True)
class SegmentConfig:
    """Thresholds for splitting. Defaults follow BUILD_BRIEF.md section 6."""

    pause_boundary: float = 0.6
    """A gap this long forces a sentence break regardless of punctuation."""

    paragraph_pause: float = 1.6
    """A gap this long starts a new paragraph (a topic-ish chunk)."""

    max_sentence_seconds: float = 22.0
    """Hard cap. Whisper sometimes never punctuates; an unbounded 'sentence'
    would make every candidate window that starts there far too long."""

    min_sentence_words: int = 2
    """Shorter fragments are merged forward rather than standing alone."""


def segment(transcript: Transcript, cfg: SegmentConfig | None = None) -> Sentences:
    """Group a transcript's words into sentences and paragraphs."""
    cfg = cfg or SegmentConfig()
    sentences = _merge_fragments(_split_into_sentences(transcript.words, cfg), cfg)
    _assign_paragraphs(sentences, cfg)
    return Sentences(source_id=transcript.source_id, sentences=sentences)


def ends_sentence(text: str) -> bool:
    """Whether `text` ends a sentence, ignoring abbreviations and decimals."""
    stripped = text.rstrip(TRAILING_MARKS)
    if not stripped.endswith(TERMINAL_PUNCTUATION):
        return False

    if stripped.endswith("."):
        body = stripped[:-1]
        # "Mr." / "e.g." -- an abbreviation, not a sentence end.
        if body.lower().strip(".") in ABBREVIATIONS or body.lower() in ABBREVIATIONS:
            return False
        # "3." in "3.5" -- a decimal split across word tokens.
        if body.isdigit() and len(body) <= 3:
            return False
        # A single initial, e.g. "J." in "J. R. R."
        if len(body) == 1 and body.isalpha() and body.isupper():
            return False
    return True


def _split_into_sentences(all_words: list[Word], cfg: SegmentConfig) -> list[Sentence]:
    """First pass: break on punctuation, long pauses, or the duration cap."""
    sentences: list[Sentence] = []
    start_index = 0

    for i, word in enumerate(all_words):
        is_last = i == len(all_words) - 1
        gap_after = (all_words[i + 1].start - word.end) if not is_last else 0.0
        span = word.end - all_words[start_index].start

        terminal = ends_sentence(word.text)
        long_pause = gap_after >= cfg.pause_boundary
        too_long = span >= cfg.max_sentence_seconds

        if terminal or long_pause or too_long or is_last:
            sentences.append(_make_sentence(
                all_words, start_index, i + 1, len(sentences),
                ends_with_terminal=terminal,
            ))
            start_index = i + 1

    return sentences


def _make_sentence(all_words: list[Word], lo: int, hi: int, index: int, *,
                   ends_with_terminal: bool) -> Sentence:
    span = all_words[lo:hi]
    gap_before = (span[0].start - all_words[lo - 1].end) if lo > 0 else 0.0
    return Sentence(
        index=index,
        start=span[0].start,
        end=span[-1].end,
        text=" ".join(w.text.strip() for w in span).strip(),
        word_indices=(lo, hi),
        gap_before=max(0.0, gap_before),
        ends_with_terminal_punctuation=ends_with_terminal,
    )


def _merge_fragments(sentences: list[Sentence], cfg: SegmentConfig) -> list[Sentence]:
    """Merge one- and two-word fragments into their neighbour.

    "Right." or "Yeah." standing alone is a useless clip boundary -- a window
    starting there begins on a filler acknowledgement rather than a thought.
    Fragments merge *forward* so the next real sentence keeps its own start.
    """
    if not sentences:
        return []

    merged: list[Sentence] = []
    pending: Sentence | None = None

    for sentence in sentences:
        current = _join(pending, sentence) if pending is not None else sentence
        pending = None
        if _word_count(current) < cfg.min_sentence_words:
            pending = current  # too short to stand alone; carry it forward
        else:
            merged.append(current)

    # A trailing fragment has no successor, so it joins the previous sentence.
    if pending is not None:
        if merged:
            merged[-1] = _join(merged[-1], pending)
        else:
            merged.append(pending)

    return [s.model_copy(update={"index": i}) for i, s in enumerate(merged)]


def _word_count(sentence: Sentence) -> int:
    lo, hi = sentence.word_indices
    return hi - lo


def _join(first: Sentence, second: Sentence) -> Sentence:
    return first.model_copy(update={
        "end": second.end,
        "text": f"{first.text} {second.text}".strip(),
        "word_indices": (first.word_indices[0], second.word_indices[1]),
        "ends_with_terminal_punctuation": second.ends_with_terminal_punctuation,
    })


def _assign_paragraphs(sentences: list[Sentence], cfg: SegmentConfig) -> None:
    """Number sentences by paragraph, breaking on long pauses. Mutates in place."""
    paragraph = 0
    for position, sentence in enumerate(sentences):
        if position > 0 and sentence.gap_before >= cfg.paragraph_pause:
            paragraph += 1
        sentences[position] = sentence.model_copy(update={"paragraph": paragraph})


def sentences_between(sentences: list[Sentence], start: float, end: float) -> list[Sentence]:
    """Sentences fully inside [start, end]."""
    return [s for s in sentences if s.start >= start - 1e-6 and s.end <= end + 1e-6]


def text_for_range(sentences: list[Sentence], lo: int, hi: int) -> str:
    """Joined text for the sentence slice [lo, hi)."""
    return " ".join(s.text for s in sentences[lo:hi]).strip()
