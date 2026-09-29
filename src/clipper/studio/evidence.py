"""Dispute packs: proof of what a campaign asked for and what the clip delivered.

Clippers' most-cited loss is a clip rejected after it has earned views, and on
Whop a brand "may only reject a submission for failing a requirement written in
the campaign requirements" (D63). So when a clip is made, Clipper keeps a
snapshot of the campaign's rules exactly as they were, the compliance checks
the clip passed, and the permission the user recorded. The pack adds what
happened after: each post's link, the caption as it was actually posted checked
against the saved rules, when it was marked submitted, and its views over time.

A clip made before snapshots were kept gets one from the campaign as it is now,
marked `late` so the pack says so rather than overstating it.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

import yaml

from ..utils.logging import get_logger

log = get_logger(__name__)

RULE_FIELDS = ("required_hashtags", "required_caption_text", "required_credit_text",
               "forbidden_terms", "only_required_hashtags")


def _campaign_part(campaign) -> dict:
    dumped = campaign.model_dump(mode="json")
    return {"name": campaign.name, "title": campaign.title or campaign.name,
            "marketplace": campaign.marketplace, "campaign_url": campaign.campaign_url,
            "rules": {k: dumped.get(k) for k in RULE_FIELDS},
            "seconds": [campaign.duration.min_seconds, campaign.duration.max_seconds],
            "brief_yaml": yaml.safe_dump(dumped, sort_keys=False, allow_unicode=True, width=100)}


def snapshot(record, *, info, campaign, caption: str) -> dict:
    """The evidence kept when a clip is made (a manifest.ClipRecord)."""
    plan = record.plan
    return {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "late": False,
        "campaign": _campaign_part(campaign),
        "permission": campaign.source_authorization,
        "source": {"title": info.title or "", "id": info.source_id, "url": info.url or "",
                   "start_s": round(plan.start, 2), "end_s": round(plan.end, 2)},
        "duration_s": round(record.duration, 2),
        "caption": caption,
        "checks": [{"name": r.name, "passed": r.passed, "detail": r.detail}
                   for r in record.compliance.rules],
        "quality": [{"name": c.name, "status": c.status, "detail": c.detail}
                    for c in record.qa.checks],
    }


def late_snapshot(clip: dict, campaign) -> dict:
    """Evidence for a clip made before snapshots existed, from today's campaign file."""
    return {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "late": True,
        "campaign": _campaign_part(campaign) if campaign else {"name": clip["campaign"], "rules": {}},
        "permission": campaign.source_authorization if campaign else "",
        "source": {"title": clip.get("source_title") or "", "id": clip.get("source_id") or "",
                   "start_s": clip.get("start_s"), "end_s": clip.get("end_s")},
        "duration_s": clip.get("duration_s"), "caption": clip.get("caption") or "",
        "checks": [], "quality": [],
    }


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"#\w+", text or "")}


def caption_checks(caption: str, rules: dict) -> list[dict]:
    """The posted caption against the saved rules: what a reviewer would look for."""
    out = []
    tags = _words(caption)
    for tag in rules.get("required_hashtags") or []:
        out.append({"name": f"Has {tag}", "passed": tag.lower() in tags})
    required = (rules.get("required_caption_text") or "").strip()
    if required:
        out.append({"name": f"Includes “{required}”", "passed": required.lower() in (caption or "").lower()})
    credit = (rules.get("required_credit_text") or "").strip()
    if credit:
        out.append({"name": f"Credits “{credit}”", "passed": credit.lower() in (caption or "").lower()})
    for term in rules.get("forbidden_terms") or []:
        out.append({"name": f"Doesn't mention “{term}”", "passed": term.lower() not in (caption or "").lower()})
    if rules.get("only_required_hashtags"):
        # A tag the required text itself contains (e.g. "#ad") is required, not extra.
        allowed = ({t.lower() for t in rules.get("required_hashtags") or []}
                   | _words(required) | _words(credit))
        extra = sorted(tags - allowed)
        out.append({"name": "No hashtags beyond the required ones", "passed": not extra,
                    "detail": ", ".join(extra)})
    return out


def summary(evidence: dict | None, posts: list[dict]) -> dict:
    """What the clip sheet shows: when the brief was saved, and how many checks passed."""
    if not evidence:
        return {"saved_at": None, "late": False, "passed": 0, "total": 0, "posted_ok": None}
    made = evidence.get("checks") or []
    rules = (evidence.get("campaign") or {}).get("rules") or {}
    posted = [c for p in posts if p.get("posted_caption") for c in caption_checks(p["posted_caption"], rules)]
    return {"saved_at": evidence.get("saved_at"), "late": bool(evidence.get("late")),
            "passed": sum(1 for c in made if c["passed"]), "total": len(made),
            "posted_ok": (all(c["passed"] for c in posted) if posted else None)}


def _changes(points: list[dict]) -> list[dict]:
    """The first and latest snapshot, and each one where the views moved."""
    kept = [p for i, p in enumerate(points)
            if i in (0, len(points) - 1) or p.get("views") != points[i - 1].get("views")]
    return kept


def _html(clip: dict, evidence: dict, posts: list[dict], history: dict[str, list[dict]]) -> str:
    e = html.escape
    camp = evidence.get("campaign") or {}
    rows = []

    def tick(ok):
        return "✅" if ok else "❌"

    rows.append(f"<h1>Proof pack: {e(clip['title'])}</h1>")
    rows.append(f"<p>Campaign: <b>{e(camp.get('title') or clip['campaign'])}</b>"
                + (f" on {e(camp['marketplace'])}" if camp.get("marketplace") else "")
                + (f" · <a href='{e(camp['campaign_url'])}'>campaign page</a>" if camp.get("campaign_url") else "")
                + "</p>")
    note = (" (saved later, from the campaign as it was then; this clip was made before Clipper kept "
            "snapshots)" if evidence.get("late") else "")
    rows.append(f"<p>Campaign rules saved: <b>{e(evidence.get('saved_at') or '')}</b>{e(note)}. "
                "The full brief is in <code>brief.yaml</code>.</p>")
    src = evidence.get("source") or {}
    rows.append(f"<h2>The clip</h2><p>{e(src.get('title') or '')} "
                f"{src.get('start_s') or 0:.1f}s–{src.get('end_s') or 0:.1f}s · "
                f"{evidence.get('duration_s') or 0:.1f}s long · file <code>clip.mp4</code></p>")
    if evidence.get("permission"):
        rows.append(f"<p>Permission recorded: {e(evidence['permission'])}</p>")
    if evidence.get("checks"):
        rows.append("<h2>Checks when the clip was made</h2><ul>" + "".join(
            f"<li>{tick(c['passed'])} {e(c['name'])}{(': ' + e(c['detail'])) if c.get('detail') else ''}</li>"
            for c in evidence["checks"]) + "</ul>")
    rules = camp.get("rules") or {}
    for post in posts:
        rows.append(f"<h2>Post on {e(post['platform'])}</h2><p><a href='{e(post['url'])}'>{e(post['url'])}</a>"
                    f"<br>Posted: {e(post.get('posted_at') or 'unknown')}"
                    f"<br>Marked submitted: {e(post.get('submitted_at') or 'not yet')}</p>")
        if post.get("posted_caption"):
            rows.append(f"<p>Caption as posted:</p><blockquote>{e(post['posted_caption'])}</blockquote>")
            checks = caption_checks(post["posted_caption"], rules)
            if checks:
                rows.append("<ul>" + "".join(f"<li>{tick(c['passed'])} {e(c['name'])}"
                                             f"{(': ' + e(c['detail'])) if c.get('detail') else ''}</li>"
                                             for c in checks) + "</ul>")
        points = _changes(history.get(post["url"]) or [])
        if points:
            def cell(v):
                return "" if v is None else f"{v:,}" if isinstance(v, int | float) else e(str(v))

            rows.append("<p>Views over time (each change; every sync is in <code>stats.csv</code>):</p>"
                        "<table><tr><th>When</th><th>Views</th><th>Likes</th></tr>" + "".join(
                            f"<tr><td>{e(p['at'])}</td><td>{cell(p.get('views'))}</td>"
                            f"<td>{cell(p.get('likes'))}</td></tr>" for p in points[-40:]) + "</table>")
    rows.append(f"<p class='muted'>Made with Clipper on {datetime.now():%Y-%m-%d %H:%M}.</p>")
    style = ("body{font-family:system-ui,sans-serif;max-width:760px;margin:2em auto;padding:0 1em;"
             "line-height:1.5;color:#111}blockquote{border-left:3px solid #ccc;margin:0;padding:.3em 1em;"
             "white-space:pre-wrap}table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:.2em .6em}"
             ".muted{color:#777;font-size:.85em}")
    return f"<!doctype html><meta charset='utf-8'><title>Proof pack</title><style>{style}</style>" + "".join(rows)


def build_pack(clip: dict, video: Path, evidence: dict, posts: list[dict],
               history: dict[str, list[dict]]) -> bytes:
    """The zip a clipper sends a campaign: proof.html, brief.yaml, clip.mp4, stats.csv."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("proof.html", _html(clip, evidence, posts, history))
        z.writestr("brief.yaml", (evidence.get("campaign") or {}).get("brief_yaml") or "")
        z.writestr("evidence.json", json.dumps({**evidence, "posts": posts}, indent=1, ensure_ascii=False))
        stats = io.StringIO()
        writer = csv.writer(stats)
        writer.writerow(["post", "at", "views", "likes", "comments", "shares"])
        for url, points in history.items():
            for p in points:
                writer.writerow([url, p["at"], p.get("views"), p.get("likes"), p.get("comments"), p.get("shares")])
        z.writestr("stats.csv", stats.getvalue())
        if video.exists():
            z.write(video, "clip.mp4", compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()
