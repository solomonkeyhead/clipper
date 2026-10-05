"""Several accounts per platform, kept manageable (D89).

Each connected account has a key, "<platform>:<handle>" (lower case), the same
one its posts carry. Accounts can be gathered into groups -- "Movies" with a
TikTok, an Instagram and a YouTube account -- and a group can be given the
campaigns it posts for.

The Control Center's "viewing" switcher narrows the dashboard, clips and stats to
one group or one account (`scope`): posts on its accounts, and clips ready to post
for its campaigns (a group's own campaigns, or those posting on the account's
platform).

More than one account on a platform is part of the Pro plan (studio/plans.py).
A sign-in that would go over the plan's limit is undone, so reconnecting an
account you already have always works.
"""

from __future__ import annotations

import json

from .. import platforms
from ..utils.logging import get_logger
from . import db

log = get_logger(__name__)

#: A post's platform -> the campaign platform it posts for (config.PLATFORMS).
CAMPAIGN_PLATFORM = {p: t for p, t in platforms.TARGET_OF.items() if p in ("tiktok", "instagram", "youtube", "x")}


def key(platform: str, handle: str) -> str:
    return f"{platform}:{(handle or '').strip().lstrip('@').lower()}"


def token_files(platform: str):
    """The function listing `platform`'s connected accounts' files."""
    if platform == "tiktok":
        from ..tiktok import api
        return api.token_files
    if platform == "instagram":
        from ..instagram import api
        return api.token_files
    if platform == "youtube":
        from ..youtube import api
        return api.token_files
    if platform == "x":
        from ..x import api
        return api.account_files
    raise ValueError(f"unknown platform {platform!r}")


def over_limit_message(platform: str) -> str:
    from ..config import PLATFORM_NAMES
    from . import plans

    return (f"Your plan connects one {PLATFORM_NAMES.get(platform, platform)} account. More than one per platform "
            f"is part of the {plans.PLAN_NAMES[plans.FEATURES['multi_account']]} plan.")


def undo_if_over(platform: str, before: set) -> str | None:
    """After a sign-in: remove a newly added account beyond the plan's limit.
    Returns the message to show, or None when it's allowed."""
    from . import plans

    added = set(token_files(platform)()) - before
    if not added or plans.has("multi_account") or not before:
        return None
    for path in added:
        path.unlink(missing_ok=True)
    log.info("removed %s account(s) over the plan's limit: %s", platform, ", ".join(p.stem for p in added))
    return over_limit_message(platform)


# -- groups ---------------------------------------------------------------------

def groups() -> list[dict]:
    with db.connect() as con:
        rows = con.execute("SELECT id, name, members, campaigns FROM account_groups ORDER BY name COLLATE NOCASE")
        return [{"id": r["id"], "name": r["name"], "members": json.loads(r["members"]),
                 "campaigns": json.loads(r["campaigns"])} for r in rows]


def save_group(name: str, members: list[str], campaigns: list[str], group_id: int | None = None) -> int:
    name = name.strip()
    if not name:
        raise ValueError("give the group a name")
    if not members:
        raise ValueError("pick at least one account for the group")
    members = list(dict.fromkeys(m.lower() for m in members))
    campaigns = list(dict.fromkeys(campaigns))
    with db.connect() as con:
        clash = con.execute("SELECT id FROM account_groups WHERE name = ? COLLATE NOCASE AND id IS NOT ?",
                            (name, group_id)).fetchone()
        if clash:
            raise ValueError(f"there's already a group called {name!r}")
        if group_id is None:
            cur = con.execute("INSERT INTO account_groups (name, members, campaigns, created_at) VALUES (?, ?, ?, ?)",
                              (name, json.dumps(members), json.dumps(campaigns), db.now()))
            return int(cur.lastrowid)
        cur = con.execute("UPDATE account_groups SET name=?, members=?, campaigns=? WHERE id=?",
                          (name, json.dumps(members), json.dumps(campaigns), group_id))
        if not cur.rowcount:
            raise LookupError("no such group")
        return group_id


def delete_group(group_id: int) -> bool:
    with db.connect() as con:
        return bool(con.execute("DELETE FROM account_groups WHERE id=?", (group_id,)).rowcount)


# -- scope ------------------------------------------------------------------------

class Scope:
    """What the viewing switcher narrows to: "all", "group:<id>" or "account:<platform>:<handle>"."""

    def __init__(self, value: str | None, all_groups: list[dict] | None = None) -> None:
        self.value = (value or "all").strip()
        self.accounts: set[str] | None = None     # None: every account
        self.campaigns: set[str] | None = None    # None: every campaign
        self.platforms: set[str] | None = None    # campaign platforms its ready clips must post for
        if self.value.startswith("group:"):
            gid = self.value.split(":", 1)[1]
            group = next((g for g in (all_groups if all_groups is not None else groups()) if str(g["id"]) == gid), None)
            if group is None:
                self.value = "all"
                return
            self.accounts = set(group["members"])
            self.campaigns = set(group["campaigns"]) or None
            self.platforms = {CAMPAIGN_PLATFORM.get(m.split(":", 1)[0], "") for m in group["members"]}
        elif self.value.startswith("account:"):
            account = self.value.split(":", 1)[1].lower()
            self.accounts = {account}
            self.platforms = {CAMPAIGN_PLATFORM.get(account.split(":", 1)[0], "")}

    @property
    def everything(self) -> bool:
        return self.accounts is None

    def has_post(self, platform: str, account: str) -> bool:
        return self.accounts is None or key(platform, account) in self.accounts

    def wants_campaign(self, name: str, targets: tuple[str, ...] | list[str]) -> bool:
        """A campaign whose ready clips this scope posts."""
        if self.campaigns is not None:
            return name in self.campaigns
        return self.platforms is None or bool(self.platforms & set(targets))
