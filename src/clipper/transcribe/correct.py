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
* Whisper re-listens to the audio around the word (`recheck.py`); the fix
  applies only if the replacement is what was said. Captions must show what
  was said, not what is true: "from the bot" stays, even though "bite" is the
  medically correct word.
* Fixes the user has ruled wrong are listed in the config and never applied.

What was tried and dropped, measured on the real proposals (docs/VERIFIED.md):
five text-only judges -- comparing the two sentences, a blind "could this have
been said?" vote, and a "why would it change?" classifier -- each let a wrong
fix through or missed a right one depending on how the question was framed.

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
from .recheck import AudioRecheck

log = get_logger(__name__)

PROMPT_VERSION = "correct-v6"
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
- Only flag a word if, as transcribed, the sentence does not make sense as
  something a person would say, AND a word that sounds like it would make it
  make sense. If you are not sure, leave it.
- Caption what was SAID, not what is true. Speakers get facts wrong, misname
  things and misremember; a sentence that makes sense but is factually wrong is
  not a transcription error. Never change a word to make a statement accurate.
- Never replace an unfamiliar word, nickname, slang, local or foreign term with
  a more familiar one. If the speaker used an odd word, keep it.
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
    recheck: AudioRecheck | None,
    cache: LLMCache | None = None,
    rejected: frozenset[tuple[str, str]] = frozenset(),
) -> tuple[list[Word], list[WordFix]]:
    """Return `words` with misheard words fixed, plus a list of what changed.

    `backend` may be a list, tried in order: a stronger model first and the
    default as a fallback, since the stronger free-tier models are often
    overloaded or out of quota.

    Every proposal must then be *heard*: `recheck` re-listens to the audio and
    the fix applies only if the replacement is what was said. Without audio
    nothing is applied -- every text-only judge tried (five variants, on the
    real proposals) let a factual "correction" through in some runs, and
    captions must show what was said. `rejected` holds fixes the user has
    ruled wrong; they are never applied.

    Any failure -- no backend, a network error, an unparseable reply -- returns
    the words unchanged. A caption with one wrong word is better than no clip.
    """
    if not words or recheck is None:
        return words, []
    backends = backend if isinstance(backend, list) else [backend]
    answered = _ask(backends, SYSTEM, _prompt(words, before, after), list[_Edit],
                    cache=cache, prompt_key=PROMPT_VERSION)
    if answered is None:
        return words, []
    text, _ = answered

    edits = []
    for edit in _parse(text):
        if _rejection(words, edit):
            continue
        if (_bare(edit.original), _bare(edit.replacement)) in rejected:
            log.info("caption fix is on the rejected list: %r -> %r",
                     edit.original, edit.replacement)
            continue
        edits.append(edit)

    context = [*before, *words, *after]
    heard = []
    for edit in edits:
        if recheck.said(words[edit.index], edit.replacement, context):
            heard.append(edit)
        else:
            log.info("caption fix not applied, the audio says %r: %r -> %r",
                     words[edit.index].text.strip(), edit.original, edit.replacement)
    return apply_edits(words, heard)


def _ask(backends: list[LLMBackend], system: str, user: str, schema, *,
         cache: LLMCache | None, prompt_key: str) -> tuple[str, LLMBackend] | None:
    """The first answer from the backends in order, cached. None if all fail."""
    for backend in backends:
        key = None
        if cache is not None:
            key = cache.key(backend=backend.name, model=backend.cache_model(),
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
    if _bare(replacement) == _bare(edit.original):
        # Seen on a real clip ("smokes" -> "smokes"); not worth re-listening to.
        return "replacement is the same word"
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
