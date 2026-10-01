"""Post links pasted by hand: filed in the performance log like a synced post.

The sync finds posts by their caption; a clip posted with a rewritten caption,
or not found yet, can have its link pasted in the Control Center instead. The
link goes on the clip's row (TikTok, with its video id so the next sync fills
the numbers) or on a new row for that platform (Instagram is matched by its URL
on the next sync; YouTube has no stats sync yet, so it only records the link).
"""

from __future__ import annotations

import re

from ..learn import log as perf

CARRIED = ["caption", "duration_s", "opening", "lead_in_s", "hook", "campaign",
           "source_title", "file", "source_id", "clip_id", "candidate_id"]


class PostLinkError(ValueError):
    """Not a link Clipper can file; the message says why."""


#: Platforms a link can be filed for. TikTok, Instagram, YouTube and X sync
#: their numbers (X once an account is connected); the rest are filed for
#: submitting, without numbers -- they have no official API that gives them.
HOSTS = {"tiktok.com": "tiktok", "instagram.com": "instagram", "youtube.com": "youtube", "youtu.be": "youtube",
         "x.com": "x", "twitter.com": "x", "facebook.com": "facebook", "fb.watch": "facebook",
         "snapchat.com": "snapchat", "threads.net": "threads", "threads.com": "threads"}


def platform_of(url: str) -> str:
    host = re.sub(r"^https?://(www\.|m\.|vm\.|mobile\.)?", "", url.strip().lower()).split("/", 1)[0]
    for domain, platform in HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    raise PostLinkError("paste a post link from TikTok, Instagram, YouTube, X, Facebook, Snapchat or Threads")


def add_link(clip: dict, url: str) -> str:
    """File `url` as a post of `clip` (a db row). Returns the platform."""
    url = url.strip().split("?", 1)[0].rstrip("/")
    if not url.startswith("https://"):
        raise PostLinkError("paste the post's full link, starting with https://")
    platform = platform_of(url)
    if platform == "tiktok" and "/video/" not in url:
        raise PostLinkError("that's not a TikTok video link; open the post and copy its link "
                            "(tiktok.com/@you/video/…)")
    if platform == "x" and not re.search(r"/status/\d+", url):
        raise PostLinkError("that's not an X post link; open the post and copy its link "
                            "(x.com/you/status/…)")
    if platform == "x":  # one form, so the sync finds it by its id
        url = re.sub(r"^https://(www\.|mobile\.)?twitter\.com/", "https://x.com/", url)
    rows = perf.read()
    if any((r.get("url") or "").split("?", 1)[0].rstrip("/") == url for r in rows):
        raise PostLinkError("that link is already filed")
    key = (clip["campaign"], clip["source_id"], clip["clip_id"])
    own = [r for r in rows if (r.get("campaign"), r.get("source_id"), r.get("clip_id")) == key]
    template = own[0] if own else {
        "caption": clip.get("caption") or "", "campaign": clip["campaign"],
        "source_title": clip.get("source_title") or "", "file": clip.get("file") or "",
        "source_id": clip["source_id"], "clip_id": clip["clip_id"],
        "duration_s": str(clip.get("duration_s") or "")}
    video_id = (re.search(r"/video/(\d+)", url).group(1) if platform == "tiktok"
                else re.search(r"/status/(\d+)", url).group(1) if platform == "x" else "")
    main = next((r for r in own if (r.get("platform") or "tiktok") == "tiktok"
                 and not (r.get("url") or "").strip()), None)
    if platform == "tiktok" and main is not None:
        main.update(url=url, video_id=video_id, platform="tiktok")
    else:
        rows.append({**{c: template.get(c, "") for c in CARRIED},
                     "platform": platform, "url": url, "video_id": video_id})
    perf.write(rows)
    return platform
