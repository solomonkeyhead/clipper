"""Clips edited by hand in the Control Center's editor (docs/DECISIONS.md D103).

An edit is the pieces of the source kept, in order (source seconds), each with
how far it's zoomed in; the on-screen hook; and fixes to caption words. It is
rendered through the same pipeline as every clip (runner._render_plan): the
pieces are joined, captions follow the cuts, and QA and the brief's checks run.

What the brief allows still decides (campaign/edits.py). The editor is a
person's choice, so an edit class that's only off *by default* (Clipper doesn't
cut scripted scenes on its own) is open to them; one the brief or the campaign
forbids is not, whoever asks.
"""

from __future__ import annotations

from itertools import pairwise

from pydantic import BaseModel, Field

from .campaign.edits import Permissions
from .config import CampaignConfig
from .models import Word

#: Zoom steps a piece can take: none, a punch-in, a tight punch-in.
ZOOMS = (1.0, 1.1, 1.2)
MIN_PIECE = 0.3          # seconds; shorter pieces are dropped as slips
FIX_REACH = 0.25         # a fix applies to the word starting within this of it
HOOK_MAX = 120


class Piece(BaseModel):
    start: float
    end: float
    zoom: float = 1.0

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


class WordFix(BaseModel):
    """A caption word said as `text` instead; empty hides it from the captions."""

    at: float
    text: str


class ClipEdit(BaseModel):
    pieces: list[Piece] = Field(default_factory=list)
    hook: str = ""
    fixes: list[WordFix] = Field(default_factory=list)

    @property
    def start(self) -> float:
        return self.pieces[0].start

    @property
    def end(self) -> float:
        return self.pieces[-1].end

    @property
    def length(self) -> float:
        return sum(p.length for p in self.pieces)

    @property
    def cuts(self) -> int:
        """Places inside the clip where something was taken out (a zoom change
        between touching pieces isn't one)."""
        return sum(1 for a, b in pairwise(self.pieces) if b.start - a.end > 0.02)

    @property
    def zoomed(self) -> bool:
        return any(p.zoom != 1.0 for p in self.pieces)

    def tidy(self, duration: float) -> ClipEdit:
        """In order, inside the video, overlaps merged, slips dropped, zooms on a step."""
        pieces: list[Piece] = []
        for p in sorted(self.pieces, key=lambda p: p.start):
            start, end = max(0.0, p.start), min(duration, p.end)
            if end - start < MIN_PIECE:
                continue
            zoom = min(ZOOMS, key=lambda z: abs(z - p.zoom))
            if pieces and start <= pieces[-1].end + 0.02 and zoom == pieces[-1].zoom:
                pieces[-1] = pieces[-1].model_copy(update={"end": max(end, pieces[-1].end)})
            elif pieces and start < pieces[-1].end:
                start = pieces[-1].end
                if end - start >= MIN_PIECE:
                    pieces.append(Piece(start=round(start, 3), end=round(end, 3), zoom=zoom))
            else:
                pieces.append(Piece(start=round(start, 3), end=round(end, 3), zoom=zoom))
        return ClipEdit(pieces=pieces, hook=" ".join(self.hook.split())[:HOOK_MAX],
                        fixes=[f for f in self.fixes if pieces and pieces[0].start - 1 <= f.at <= pieces[-1].end + 1])


def blocked(perms: Permissions) -> dict[str, str]:
    """Edit classes the brief or the campaign forbids -> why (not mere defaults)."""
    return {cls: why for cls, why in perms.reasons.items()
            if not perms.allowed[cls] and not why.startswith("default")}


def problems(edit: ClipEdit, perms: Permissions, campaign: CampaignConfig) -> list[str]:
    """Why this edit can't be rendered for this campaign; empty when it can."""
    out: list[str] = []
    if not edit.pieces:
        return ["nothing is kept: set where the clip starts and ends"]
    no = blocked(perms)
    if edit.cuts and "internal_cuts" in no:
        out.append(f"no cuts inside the clip: {no['internal_cuts']}")
    if edit.zoomed and "visual_effects" in no:
        out.append(f"no zooms: {no['visual_effects']}")
    if edit.hook and "added_text" in no:
        out.append(f"no on-screen text: {no['added_text']}")
    low, high = campaign.duration.min_seconds, campaign.duration.max_seconds
    if low and edit.length < low:
        out.append(f"it's {edit.length:.0f}s; the campaign wants at least {low:.0f}s")
    if high and edit.length > high:
        out.append(f"it's {edit.length:.0f}s; the campaign allows at most {high:.0f}s")
    return out


def kept_words(words: list[Word], edit: ClipEdit) -> list[Word]:
    """The words whose middle falls in a kept piece, in source time."""
    return [w for w in words
            if any(p.start <= (w.start + w.end) / 2 < p.end for p in edit.pieces)]


def apply_fixes(words: list[Word], fixes: list[WordFix]) -> list[Word]:
    """`words` with each fix applied to the word starting nearest it (within reach)."""
    if not fixes:
        return words
    out = list(words)
    hidden: set[int] = set()
    for fix in fixes:
        near = min(range(len(out)), key=lambda i: abs(out[i].start - fix.at), default=None)
        if near is None or abs(out[near].start - fix.at) > FIX_REACH:
            continue
        text = " ".join(fix.text.split())
        if text:
            out[near] = out[near].model_copy(update={"text": text})
        else:
            hidden.add(near)
    return [w for i, w in enumerate(out) if i not in hidden]
