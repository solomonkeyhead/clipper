"""Which campaign each video belongs to, so New clips can list footage by campaign (D73).

Files are never moved -- some live in the user's own Downloads folder. A
video's campaign comes from, in order of trust:

1. "clipped": a job was run on it for a campaign, now or before (the
   `footage` table, backfilled from clips already in the library);
2. "added": it was uploaded or imported while that campaign was picked;
3. "name": its file name names the campaign -- a distinctive word of the
   title ("ADULTS 205" -> FX Adults S2, "ChadPowers_204" -> Chad Powers S2)
   or its initials ("PLM_s01_ep1" -> Please Like Me);
4. "like": named like its neighbours -- 204.mov beside 201.mov, 207.mov and
   208.mov, all clipped for one campaign, belongs to it too.

Clipper's own downloaded clips ("<title> - <campaign>.mp4", the clip
download's file name) are clips, not footage, and are left out of the list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..paths import data_root
from . import db

#: Words too common in titles and file names to say which campaign a file is.
GENERIC = {"the", "and", "movie", "show", "season", "series", "episode", "growth", "test", "local",
           "clips", "clip", "campaign", "official", "trailer", "extended", "scene", "final", "edit",
           "please", "like", "with", "from", "this", "that", "your", "what", "when", "camp", "south"}


def remember(paths: list[str], campaign: str, how: str) -> None:
    """Note that these videos belong to `campaign` ("clipped" outranks "added")."""
    with db.connect() as con:
        for path in paths:
            con.execute(
                "INSERT INTO footage (path, campaign, how, at) VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE "
                "SET campaign=excluded.campaign, how=excluded.how, at=excluded.at "
                "WHERE excluded.how='clipped' OR footage.how!='clipped'",
                (str(Path(path).resolve()), campaign, how, db.now()))


def known() -> dict[str, tuple[str, str]]:
    """Path -> (campaign, how): what's been recorded, plus clips already made from it."""
    out: dict[str, tuple[str, str]] = {}
    with db.connect() as con:
        made = {r["source_id"]: r["campaign"] for r in con.execute(
            "SELECT source_id, campaign FROM clips WHERE deleted_at IS NULL GROUP BY source_id")}
        rows = [dict(r) for r in con.execute("SELECT path, campaign, how FROM footage")]
    for source_id, campaign in made.items():
        info = data_root() / "work" / source_id / "info.json"
        try:
            path = json.loads(info.read_text(encoding="utf-8"))["media"]["path"]
        except (OSError, ValueError, KeyError):
            continue
        out[str(Path(path).resolve())] = (campaign, "clipped")
    for r in rows:
        if r["how"] == "clipped" or r["path"] not in out:
            out[r["path"]] = (r["campaign"], r["how"])
    return out


def _words(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)  # ChadPowers -> Chad Powers
    return re.findall(r"[a-z0-9]+", text.lower())


def keys(campaigns: dict) -> dict[str, tuple[set[str], str]]:
    """Campaign -> (distinctive title/name words, initials of the title)."""
    out = {}
    for name, c in campaigns.items():
        title = _words(c.title or name)
        words = {w for w in title + _words(name.replace("-", " "))
                 if len(w) >= 4 and w not in GENERIC and not w.isdigit()}
        initials = "".join(w[0] for w in title if not re.fullmatch(r"s\d+|\d+", w))
        out[name] = (words, initials if len(initials) >= 3 else "")
    return out


def by_name(filename: str, campaign_keys: dict[str, tuple[set[str], str]]) -> str | None:
    """The one campaign a file name points at, if exactly one does."""
    words = set(_words(Path(filename).stem))
    scores = {}
    for name, (distinct, initials) in campaign_keys.items():
        score = len(distinct & words) + (2 if initials and initials in words else 0)
        if score:
            scores[name] = score
    if not scores:
        return None
    best = max(scores.values())
    top = [n for n, s in scores.items() if s == best]
    return top[0] if len(top) == 1 else None


def is_clip_download(filename: str, campaign_names: set[str]) -> bool:
    """A clip Clipper downloaded ("<title> - <campaign>.mp4"), not footage."""
    stem = Path(filename).stem
    return any(stem.endswith(f" - {name}") for name in campaign_names)


def sort(sources: list[dict], campaigns: dict) -> list[dict]:
    """`sources` (studio/server.list_sources) with each one's campaign and why."""
    recorded, ckeys, names = known(), keys(campaigns), set(campaigns)
    out = []
    for s in sources:
        if is_clip_download(s["name"], names):
            continue
        campaign, how = recorded.get(str(Path(s["path"]).resolve()), (None, ""))
        if campaign is None or campaign not in campaigns:
            campaign = by_name(s["name"], ckeys)
            how = "name" if campaign else ""
        out.append({**s, "campaign": campaign, "sorted_by": how})
    _by_siblings(out)
    return out


def _shape(path: str) -> tuple[str, str]:
    """Folder and name pattern: 204.mov and 201.mov share ("...", "###.mov")."""
    p = Path(path)
    return str(p.parent), re.sub(r"\d", "#", p.name.lower())


def _by_siblings(rows: list[dict]) -> None:
    groups: dict[tuple[str, str], set[str]] = {}
    for r in rows:
        if r["campaign"]:
            groups.setdefault(_shape(r["path"]), set()).add(r["campaign"])
    for r in rows:
        found = groups.get(_shape(r["path"]))
        if not r["campaign"] and found and len(found) == 1:
            r["campaign"], r["sorted_by"] = next(iter(found)), "like"
