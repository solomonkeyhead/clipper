"""The brief's own lines -- on-screen hooks, approved captions -- spread across a campaign (D85).

They used to rotate by a clip's rank within its video, which starts at 1 for
every video: a batch of 40 short Please Like Me videos made 39 clips with the
same hook (and title) and the same caption. Now each clip gets the line its
campaign has used least, counting the clips already in the library and those
handed out since, so a batch walks through all of them. Ties go to the brief's
order. Two jobs can run at once, so the count is shared under a lock.
"""

from __future__ import annotations

import threading
from collections import Counter

from ..config import CampaignConfig
from ..utils.logging import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_used: dict[tuple[str, str], Counter] = {}


def pick(campaign: CampaignConfig, kind: str, lines: tuple[str, ...] | list[str]) -> str:
    """The `kind` ("hook" or "caption") line of `lines` this campaign has used least."""
    lines = [line for line in lines if line.strip()]
    if not lines:
        return ""
    with _lock:
        key = (campaign.name, kind)
        if key not in _used:
            _used[key] = used_in_library(campaign.name, kind, lines)
        used = _used[key]
        line = min(lines, key=lambda candidate: (used[candidate], lines.index(candidate)))
        used[line] += 1
        return line


def used_in_library(campaign: str, kind: str, lines: list[str], clips: list[dict] | None = None) -> Counter:
    """How often each line already appears on the campaign's clips: a hook as the
    clip's title, a caption as the start of its caption."""
    if clips is None:
        try:
            from ..studio import db

            with db.connect() as con:
                clips = db.clips(con, campaign)
        except Exception as exc:  # no library yet: start the rotation from the top
            log.debug("brief lines: couldn't count the library: %s", exc)
            return Counter()
    count: Counter = Counter()
    for clip in clips:
        text = ((clip.get("title") if kind == "hook" else clip.get("caption")) or "").strip()
        for line in lines:
            if (text == line.strip()) if kind == "hook" else text.startswith(line.strip()):
                count[line] += 1
    return count


def forget() -> None:
    """Drop the counts (tests; a library changed under the process)."""
    with _lock:
        _used.clear()
