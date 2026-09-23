"""Fixing words speech recognition heard wrong, using the sentence around them.

Reported on real output: a caption read "I heard the *picture* from Los Angeles
Dodgers" -- a baseball pitcher. Whisper transcribes sound, and "picture" and
"pitcher" sound alike; which one was said is only obvious from context. The same
clip had "monkey never *clap*" for "cramp".

An LLM reads each clip's words with a sentence of context either side and names
the words it believes were misheard. It is **not** trusted to rewrite anything:

* It returns edits, not text: which word, what it was, what it should be. The
  words, their timings and everything it did not name are left alone.
* Each edit is checked in code before it is applied. The named word must be the
  word at that position, and the replacement must *sound like* it
  (`sounds_alike`). That is what keeps this a transcription fix: it cannot
  change "bananas" to "apples", or tidy a non-native speaker's grammar into
  something he did not say.
* At most `MAX_FIX_SHARE` of a clip's words can change. A response wanting to
  change more than that is rewriting, and none of it is applied.

Every applied edit is reported, so a wrong one can be spotted and undone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from pydantic import BaseModel, ValidationError

from ..llm.base import LLMBackend, LLMRequest
from ..llm.cache import LLMCache
from ..models import Word
from ..utils.logging import get_logger

log = get_logger(__name__)

PROMPT_VERSION = "correct-v2"
"""Bump on any edit to the prompt below; it is part of the cache key."""

# Sound-alike thresholds, chosen on 20 real homophone pairs against 15 content
# swaps (docs/VERIFIED.md, 2026-09-22). All 20 homophones pass; all 15 swaps fail.
MIN_SOUND_SIMILARITY = 0.5
# Spellings this close pass even with a different opening sound ("not"/"don't").
SPELLING_OVERRIDE = 0.8

# More edits than this share of a clip's words means the model is rewriting.
MAX_FIX_SHARE = 0.10
MIN_FIX_ALLOWANCE = 2

# Replacements longer than this are rewrites, not a misheard word.
MAX_REPLACEMENT_WORDS = 3

SYSTEM = """\
You proofread automatic speech-recognition transcripts. The recogniser writes
what words SOUND like, so it sometimes picks a word that sounds the same or
nearly the same as what was said but is wrong in context -- "picture" for
"pitcher" when the topic is baseball, "their" for "there", a misspelled name.

Your only job is to find those misheard words.

Rules:
- Only flag a word if the context makes it clearly wrong AND the correct word
  sounds like it. If you are not sure, leave it.
- Never fix grammar, word order, dialect, slang, filler words or style. People
  speak informally and some speakers are not native English speakers: "monkey
  every day bananas" is how that person talks, not a transcription error.
- Never change meaning, add words that were not said, or remove words.
- Only words in the CLIP section may be flagged; the context is there to help
  you understand it.
- Keep the replacement in the same form: a single word for a single word,
  matching capitalisation. Proper nouns should be spelled correctly.

Return a JSON array (possibly empty). Each element:
  index        integer, the [n] number of the word in the CLIP
  original     string, that word exactly as shown
  replacement  string, what the speaker actually said
  reason       string, a few words on the context that shows it"""


VERIFY_SYSTEM = """You check proposed corrections to a speech-recognition transcript. For each
numbered item you get the sentence twice: version A as transcribed, version B
with one word changed. Decide which one the speaker actually said.

Answer "B" only if B is clearly right and A is clearly wrong in context.
Answer "A" if the transcription was already right, including when B is merely
an alternative spelling or a grammatical "improvement" the speaker did not say.
Answer "unsure" if either could be right -- the text alone cannot settle it.

Return a JSON array; each element: {"item": <number>, "choice": "A" | "B" | "unsure"}."""


class _Verdict(BaseModel):
    item: int
    choice: str


class _Edit(BaseModel):
    index: int
    original: str
    replacement: str
    reason: str = ""


@dataclass(frozen=True)
class WordFix:
    """One applied correction, for the report."""

    time: float
    original: str
    replacement: str
    reason: str


def correct_words(
    words: list[Word],
    *,
    before: list[Word],
    after: list[Word],
    backend: LLMBackend | list[LLMBackend],
    cache: LLMCache | None = None,
) -> tuple[list[Word], list[WordFix]]:
    """Return `words` with misheard words fixed, plus a list of what changed.

    `backend` may be a list, tried in order: a stronger model first and the
    default as a fallback, since the stronger free-tier models are often
    overloaded or out of quota. Whichever answers the first question also
    answers the verification, so one model's proposals are not judged by
    another's standards.

    Any failure -- no backend, a network error, an unparseable reply -- returns
    the words unchanged. A caption with one wrong word is better than no clip.
    """
    if not words:
        return words, []
    backends = backend if isinstance(backend, list) else [backend]
    answered = _ask(backends, SYSTEM, _prompt(words, before, after), list[_Edit],
                    cache=cache, prompt_key=PROMPT_VERSION)
    if answered is None:
        return words, []
    text, used = answered

    edits = [e for e in _parse(text) if not _rejection(words, e)]
    if edits:
        edits = _verified(words, edits, backend=used, cache=cache)
    return apply_edits(words, edits)


def _ask(backends: list[LLMBackend], system: str, user: str, schema, *,
         cache: LLMCache | None, prompt_key: str) -> tuple[str, LLMBackend] | None:
    """The first answer from the backends in order, cached. None if all fail."""
    for backend in backends:
        key = None
        if cache is not None:
            key = cache.key(backend=backend.name, model=backend.model or "auto",
                            prompt_key=prompt_key, payload=user)
            entry = cache.get(key)
            if entry is not None:
                return entry.text, backend
        try:
            response = backend.complete(LLMRequest(
                system=system, user=user, temperature=0.0, response_schema=schema))
        except Exception as exc:  # a caption fix must never fail a render
            log.warning("caption correction: %s did not answer (%s)",
                        backend.describe(), str(exc)[:160])
            continue
        if cache is not None and key is not None:
            cache.put(key, text=response.text, model=response.model)
        return response.text, backend
    return None


def _verified(words: list[Word], edits: list[_Edit], *, backend: LLMBackend,
              cache: LLMCache | None) -> list[_Edit]:
    """Keep only the edits a second, focused question confirms.

    The first pass reads the whole clip and over-proposes. Measured on real
    clips with the default model: it changed "lay bot flies on *there*" (on the
    shirt -- already right) to "their". The sound-alike check cannot stop that;
    a true homophone passes by definition. Asking about each swap on its own,
    with both versions of the sentence side by side and an explicit "unsure",
    is what catches it. Anything but a clear "B" keeps the original: a missed
    fix costs less than a wrong one.
    """
    items = []
    for n, edit in enumerate(edits):
        lo, hi = max(0, edit.index - 12), min(len(words), edit.index + 13)
        before = [w.text.strip() for w in words[lo:hi]]
        after = list(before)
        after[edit.index - lo] = _carry_form(words[edit.index].text, edit.replacement).strip()
        items.append(f"[{n}]\nA: {' '.join(before)}\nB: {' '.join(after)}")
    answered = _ask([backend], VERIFY_SYSTEM, "\n\n".join(items), list[_Verdict],
                    cache=cache, prompt_key=PROMPT_VERSION + ":verify")
    if answered is None:
        return []  # unverified edits are not applied
    text, _ = answered

    confirmed: set[int] = set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    for item in data if isinstance(data, list) else []:
        try:
            verdict = _Verdict.model_validate(item)
        except ValidationError:
            continue
        if verdict.choice.strip().upper() == "B":
            confirmed.add(verdict.item)
    for n, edit in enumerate(edits):
        if n not in confirmed:
            log.info("caption fix not confirmed, kept original: %r -> %r",
                     edit.original, edit.replacement)
    return [e for n, e in enumerate(edits) if n in confirmed]


def apply_edits(words: list[Word], edits: list[_Edit]) -> tuple[list[Word], list[WordFix]]:
    """Validate and apply edits. Exposed separately so it can be tested offline."""
    allowance = max(MIN_FIX_ALLOWANCE, int(len(words) * MAX_FIX_SHARE))
    valid: list[tuple[int, str, str]] = []
    for edit in edits:
        reason = _rejection(words, edit)
        if reason:
            log.debug("correction rejected (%s): %r -> %r", reason,
                      edit.original, edit.replacement)
            continue
        valid.append((edit.index, edit.replacement.strip(), edit.reason.strip()))

    if len(valid) > allowance:
        log.warning("transcript correction proposed %d edits for %d words; "
                    "that is rewriting, not correcting -- none applied",
                    len(valid), len(words))
        return words, []

    out = list(words)
    fixes: list[WordFix] = []
    for index, replacement, reason in valid:
        word = out[index]
        new_text = _carry_form(word.text, replacement)
        if _bare(new_text) == _bare(word.text):
            continue
        out[index] = word.model_copy(update={"text": new_text})
        fixes.append(WordFix(time=word.start, original=word.text.strip(),
                             replacement=new_text.strip(), reason=reason))
    for fix in fixes:
        log.info("caption fix at %.2fs: %r -> %r (%s)",
                 fix.time, fix.original, fix.replacement, fix.reason)
    return out, fixes


def _rejection(words: list[Word], edit: _Edit) -> str:
    """Why an edit may not be applied, or '' if it may."""
    if not 0 <= edit.index < len(words):
        return "index out of range"
    if _bare(words[edit.index].text) != _bare(edit.original):
        return f"word {edit.index} is {words[edit.index].text.strip()!r}"
    replacement = edit.replacement.strip()
    if not replacement or len(replacement.split()) > MAX_REPLACEMENT_WORDS:
        return "replacement is empty or too long"
    if not sounds_alike(edit.original, replacement):
        return "does not sound alike"
    return ""


def sounds_alike(heard: str, meant: str) -> bool:
    """Whether `meant` could plausibly have been heard as `heard`.

    Two measures, the better of which must reach `MIN_SOUND_SIMILARITY`: plain
    spelling similarity, and similarity of a consonant skeleton that folds
    spellings of one sound together ("ph"/"f", "ck"/"k", silent "gh"...). On
    top of that the opening sound must match -- rhymes like "pitcher"/"catcher"
    score high on both measures yet are different words, and homophones almost
    never differ in their first consonant. Near-identical spellings are let
    through regardless, which admits "not" -> "don't".
    """
    a, b = _letters(heard), _letters(meant)
    if not a or not b:
        return False
    spelling = SequenceMatcher(None, a, b).ratio()
    if spelling >= SPELLING_OVERRIDE:
        return True
    ka, kb = _skeleton(heard), _skeleton(meant)
    if not ka or not kb or not _same_onset(ka, kb):
        return False
    return max(spelling, SequenceMatcher(None, ka, kb).ratio()) >= MIN_SOUND_SIMILARITY


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


_FOLDS = [("ph", "f"), ("tch", "ch"), ("ck", "k"), ("wh", "w"), ("wr", "r"),
          ("kn", "n"), ("gh", ""), ("dg", "j"), ("q", "k"), ("x", "ks"), ("z", "s")]


def _skeleton(text: str) -> str:
    """The word's consonant skeleton: first letter, then consonants, folded."""
    w = _letters(text)
    for old, new in _FOLDS:
        w = w.replace(old, new)
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    head, tail = w[:1], re.sub(r"[aeiouy]", "", w[1:])
    return re.sub(r"(.)\1+", r"\1", head + tail)


def _same_onset(a: str, b: str) -> bool:
    vowels = "aeiouy"
    if a[0] in vowels and b[0] in vowels:
        return True
    return a[0] == b[0]


def _bare(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def _carry_form(original: str, replacement: str) -> str:
    """Keep the original's leading space, capitalisation and punctuation."""
    lead = original[: len(original) - len(original.lstrip())]
    core = original.strip()
    trail = re.search(r"[^\w']+$", core)
    punctuation = trail.group(0) if trail else ""
    new = replacement.strip()
    if punctuation and not new.endswith(punctuation):
        new = re.sub(r"[^\w']+$", "", new) + punctuation
    if core[:1].isupper() and new[:1].islower():
        new = new[0].upper() + new[1:]
    return lead + new


def _prompt(words: list[Word], before: list[Word], after: list[Word]) -> str:
    def plain(ws: list[Word]) -> str:
        return " ".join(w.text.strip() for w in ws).strip() or "(none)"

    numbered = " ".join(f"[{i}]{w.text.strip()}" for i, w in enumerate(words))
    return (f"CONTEXT BEFORE (do not flag):\n{plain(before)}\n\n"
            f"CLIP (flag misheard words here, by [n]):\n{numbered}\n\n"
            f"CONTEXT AFTER (do not flag):\n{plain(after)}\n")


def _parse(text: str) -> list[_Edit]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("transcript correction reply was not JSON; ignoring it")
        return []
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [])
    edits: list[_Edit] = []
    for item in data if isinstance(data, list) else []:
        try:
            edits.append(_Edit.model_validate(item))
        except ValidationError:
            continue
    return edits
