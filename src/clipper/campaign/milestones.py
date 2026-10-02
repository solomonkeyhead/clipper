"""What a brief asks the poster to do once a post reaches a number of views (D98).

Briefs set tasks that only start mattering later: Please Like Me's "Wait until
your video passes 2,000 views, then screen-record your video analytics (watch
time, all view graphs, and locations) and submit". Missed, a submission is
rejected after its views are in. So each sentence of the brief that ties an
action to a view count becomes a milestone, and each post gets the task once its
views pass it (studio/server.py). Read from the brief as pasted and the
campaign's notes and posting rules, so a brief pasted before this existed counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: "passes 2,000 views", "hits 10k views", "after 1.5k views", "at 500 views".
_COUNT = re.compile(r"(?<![\w.,])(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?\s*[kKmM]?)\s*(?:\+\s*)?views?\b")
#: Something to do, not a payout rate or a minimum to qualify ("$1 per 1,000 views").
_ACTION = re.compile(r"\b(screen-?record|record|screenshot|submit|send|upload|post|share|pin|reply|"
                     r"verify|show|provide|dm|message)\b", re.IGNORECASE)
_RATE = re.compile(r"(\$|€|£|\bper\b|\bcpm\b|\bminimum\b|\bat least\b|\bmax(imum)?\b|\bcap\b|\bqualif|\beligib)",
                   re.IGNORECASE)


@dataclass(frozen=True)
class Milestone:
    views: int
    task: str      # the brief's sentence, as written


def _number(text: str) -> int | None:
    text = text.strip().lower().replace(" ", "")
    scale = 1000 if text.endswith("k") else 1_000_000 if text.endswith("m") else 1
    text = text.rstrip("km")
    if scale == 1:
        text = text.replace(",", "").replace(".", "") if re.fullmatch(r"\d{1,3}([,.]\d{3})+", text) else text
    try:
        return int(float(text) * scale)
    except ValueError:
        return None


def find(text: str) -> list[Milestone]:
    """Each sentence of `text` that asks for an action at a view count."""
    out: dict[int, Milestone] = {}
    # A line wrapped mid-sentence ("needs 5,000 views to\nqualify") reads as one.
    text = re.sub(r"(?<![.!?:])\n(?=[a-z])", " ", text or "")
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip(" -•*\t")
        found = _COUNT.search(sentence)
        if not found or not _ACTION.search(sentence) or _RATE.search(sentence):
            continue
        views = _number(found.group(1))
        # The fullest wording of each, to say exactly what to do.
        if views and views >= 50 and len(sentence) > len(out.get(views, Milestone(0, "")).task):
            out[views] = Milestone(views, sentence)
    return sorted(out.values(), key=lambda m: m.views)


def of(campaign, brief: str | None = None) -> list[Milestone]:
    """A campaign's milestones, from its pasted brief, notes and posting rules."""
    texts = [brief or "", campaign.notes or "", *campaign.posting_rules]
    found: dict[int, Milestone] = {}
    for text in texts:
        for m in find(text):
            if len(m.task) > len(found.get(m.views, Milestone(0, "")).task):
                found[m.views] = m
    return sorted(found.values(), key=lambda m: m.views)
