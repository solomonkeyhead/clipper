"""Reading the user's own Discord server through their own bot, over Discord's API.

Campaign marketplaces and clipping communities announce new campaigns in
Discord announcement channels, and Discord lets anyone "Follow" such a channel
into a server where they can manage webhooks. So the user makes a small private
server, follows the campaign channels into it, and adds a bot they created;
Clipper reads that server with the bot's token. Nothing here visits a
marketplace's site, logs in as the user, or reads a server the user doesn't own
(D65).

Plain REST calls, polled: no gateway connection, so the bot needn't be kept
"online". Reading message text needs the bot's Message Content intent, which a
bot in fewer than 100 servers turns on with a switch in the developer portal.
"""

from __future__ import annotations

import time

import httpx

API = "https://discord.com/api/v10"
PORTAL = "https://discord.com/developers/applications"
#: View Channel + Read Message History: all the bot needs.
PERMISSIONS = (1 << 10) | (1 << 16)
#: Application flags: the Message Content intent, verified or not.
CONTENT_INTENT = (1 << 18) | (1 << 19)
TEXT_CHANNELS = {0: "text", 5: "announcement"}


class DiscordError(RuntimeError):
    """Discord refused or couldn't be reached; the message is for the user."""


def _get(token: str, path: str, params: dict | None = None, *, client: httpx.Client | None = None):
    own = client is None
    client = client or httpx.Client(timeout=20)
    try:
        for _ in range(3):
            try:
                r = client.get(API + path, params=params,
                               headers={"Authorization": f"Bot {token}",
                                        "User-Agent": "Clipper (campaign alerts, 1.0)"})
            except httpx.HTTPError as exc:
                raise DiscordError(f"couldn't reach Discord: {exc}") from exc
            if r.status_code == 429:  # rate limited: wait as told, briefly
                time.sleep(min(float(r.json().get("retry_after", 1)), 10))
                continue
            if r.status_code == 401:
                raise DiscordError("Discord refused the bot token. Copy it again from the Bot tab "
                                   "(Reset Token), and paste all of it.")
            if r.status_code == 403:
                raise DiscordError("The bot isn't allowed to read that channel. Give it View Channel "
                                   "and Read Message History there.")
            if r.status_code == 404:
                raise DiscordError("That channel is gone, or the bot was removed from its server.")
            if r.status_code >= 400:
                raise DiscordError(f"Discord answered {r.status_code}")
            return r.json()
        raise DiscordError("Discord is rate-limiting; try again in a minute")
    finally:
        if own:
            client.close()


def bot(token: str) -> dict:
    """The bot's app: {id, name, content_intent, invite}."""
    app = _get(token, "/applications/@me")
    return {"id": app["id"], "name": app.get("name") or "your bot",
            "content_intent": bool(int(app.get("flags") or 0) & CONTENT_INTENT),
            "invite": invite_url(app["id"])}


def invite_url(app_id: str) -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            f"&scope=bot&permissions={PERMISSIONS}")


def channels(token: str) -> list[dict]:
    """Every text or announcement channel in the servers the bot is in."""
    out = []
    with httpx.Client(timeout=20) as client:
        for guild in _get(token, "/users/@me/guilds", client=client):
            listed = _get(token, f"/guilds/{guild['id']}/channels", client=client)
            for ch in sorted(listed, key=lambda c: (c.get("position") or 0)):
                if ch.get("type") in TEXT_CHANNELS:
                    out.append({"id": ch["id"], "name": ch.get("name") or "", "guild_id": guild["id"],
                                "guild": guild.get("name") or "", "kind": TEXT_CHANNELS[ch["type"]]})
    return out


def messages(token: str, channel_id: str, *, after: str | None = None, latest: int = 25) -> list[dict]:
    """Messages after `after`, oldest first; without it, the latest `latest`."""
    if after is None:
        got = _get(token, f"/channels/{channel_id}/messages", {"limit": max(1, min(latest, 100))})
        return sorted(got, key=lambda m: int(m["id"]))
    out: list[dict] = []
    with httpx.Client(timeout=20) as client:
        while True:
            got = _get(token, f"/channels/{channel_id}/messages", {"limit": 100, "after": after},
                       client=client)
            got = sorted(got, key=lambda m: int(m["id"]))
            out += got
            if len(got) < 100 or len(out) >= 500:  # a backlog beyond that is old news
                return out
            after = got[-1]["id"]


def text_of(message: dict) -> str:
    """Everything a person would read in a message: text and embeds."""
    parts = [message.get("content") or ""]
    for embed in message.get("embeds") or []:
        parts += [embed.get("title") or "", embed.get("url") or "", embed.get("description") or ""]
        for f in embed.get("fields") or []:
            parts.append(f"{f.get('name', '')}: {f.get('value', '')}")
        parts.append((embed.get("footer") or {}).get("text") or "")
    return "\n".join(p.strip() for p in parts if p and p.strip())


def snowflake_now() -> str:
    """A message id for this moment: reading "after" it returns only later posts."""
    return str((int(time.time() * 1000) - 1420070400000) << 22)


def link_to(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
