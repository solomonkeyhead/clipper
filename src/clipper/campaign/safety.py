"""Words in a post's text that get it flagged, masked just enough (D82).

TikTok, Instagram and YouTube read captions, titles and on-screen text, and a
handful of words in them get a post limited, kept off the For You feed or taken
down: explicit sexual terms, slurs, self-harm and hard drugs. Vyro's content
requirements ban the same ground (pornography and sexually explicit material,
hate speech, self-harm, promoting drugs). Clippers dodge it with a masked letter
("d*ck", "s*icide"), and the joke still reads.

So this masks only that short list, one letter where it's enough, and leaves
ordinary swearing alone: the user wants posts as close to the line as they can
go, and "fuck" or "shit" in a caption doesn't get an account banned. Slurs get
all but their first letter masked. A hashtag made of a flagged word is dropped
(a masked tag finds nothing), @mentions and links are never touched, and words
the campaign itself uses -- its required text, hashtags, title, search words,
e.g. a show called "Sex Education" -- are left as the brief has them.

The spoken words and the burned-in subtitles are separate: the audio can't
change, and subtitles follow the campaign's own `mask_profanity_in_captions`.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..config import CampaignConfig

#: Masked with one letter: still readable, no longer the flagged word.
LIGHT = frozenset("""
penis penises dick dicks dickhead cock cocks pussy pussies vagina vaginas
porn porno porns pornhub pornography sex sexual sexually sexting
cum cumming cumshot orgasm orgasms masturbate masturbating masturbation
blowjob blowjobs handjob handjobs boner boners erection erections horny
boobs boobies tits titties nude nudes nudity dildo dildos vibrator onlyfans
anal slut sluts whore whores hooker hookers nsfw
suicide suicidal kys
cocaine meth heroin fentanyl weed
""".split())  # noqa: SIM905 -- a word list reads better as words

#: Slurs: all but the first letter.
HEAVY = frozenset("""
nigga niggas nigger niggers faggot faggots fag fags retard retards retarded
tranny trannies dyke dykes chink chinks spic spics kike kikes
""".split())  # noqa: SIM905 -- a word list reads better as words

#: Flagged phrases whose words are fine alone.
PHRASES = ("kill myself", "kill yourself", "killing myself", "self harm", "self-harm", "jerk off", "jerking off")

_PHRASES = [re.compile(rf"(?<!\w){re.escape(p)}(?!\w)", re.IGNORECASE) for p in PHRASES]
_WORD = re.compile(r"(?<![@#\w/.])[A-Za-z]+(?:'[A-Za-z]+)?")
_VOWELS = "aeiouAEIOU"


def _light(word: str) -> str:
    """One vowel masked: penis -> p*nis, sex -> s*x, cum -> c*m."""
    for i, ch in enumerate(word):
        if i and ch in _VOWELS:
            return word[:i] + "*" + word[i + 1:]
    return word[0] + "*" + word[2:] if len(word) > 2 else word[0] + "*"


def _heavy(word: str) -> str:
    return word[0] + "*" * (len(word) - 1)


def exempt(campaign: CampaignConfig | None) -> frozenset[str]:
    """Words the campaign itself uses, which stay as the brief writes them."""
    if campaign is None:
        return frozenset()
    return _words((campaign.title, campaign.name.replace("-", " "), campaign.required_caption_text,
                   campaign.required_credit_text, *campaign.required_hashtags, *campaign.description_keywords,
                   *(r.text for r in campaign.caption_rules if r.must == "include")))


@lru_cache(maxsize=64)
def _words(texts: tuple[str, ...]) -> frozenset[str]:
    return frozenset(w.lower() for t in texts for w in re.findall(r"[A-Za-z]+", t or ""))


def flagged(word: str) -> bool:
    w = word.lower()
    return w in LIGHT or w in HEAVY


def clean(text: str, campaign: CampaignConfig | None = None) -> str:
    """`text` with flagged words masked and flagged hashtags dropped."""
    if not text:
        return text
    keep = exempt(campaign)

    def phrase(match: re.Match[str]) -> str:
        words = match.group(0).split(" ")
        return " ".join([_light(words[0]), *words[1:]]) if words[0].lower() not in keep else match.group(0)

    for pattern in _PHRASES:
        text = pattern.sub(phrase, text)

    def word(match: re.Match[str]) -> str:
        w = match.group(0)
        base = w.split("'")[0].lower()
        if base in keep or not flagged(base):
            return w
        return _heavy(w) if base in HEAVY else _light(w)

    text = _WORD.sub(word, text)
    # A hashtag of a flagged word: masked it finds nothing, so it goes.
    return re.sub(r"(?<!\S)#(\w+)[ \t]?", lambda m: "" if m.group(1).lower() not in keep
                  and flagged(m.group(1)) else m.group(0), text).strip()
