"""Setting Clipper up from the Control Center: keys, account connections, a system check.

Everything a new user would otherwise do in a terminal or a text editor:

* API keys (the AI model's, TikTok's app keys) are written to .env, git-ignored,
  and applied at once -- no restart. The page is told only whether a key is set,
  never its value.
* TikTok connects through its own consent page (`tiktok.api.login`), run in the
  background while the page waits; Instagram through a token pasted into the page.
  Each connection is one more account: several per platform are fine.
* The system check is `clipper doctor`'s checks (ffmpeg, GPU, fonts, disk, ...).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from ..paths import REPO_ROOT
from ..utils.logging import get_logger

log = get_logger(__name__)

#: The keys the page may set, with what each is for.
KEYS = {
    "GEMINI_API_KEY": "Google Gemini (free): picks the moments, writes captions",
    "ANTHROPIC_API_KEY": "Anthropic Claude (paid, optional)",
    "PIXABAY_API_KEY": "Pixabay (free): stock footage for Create",
    "PEXELS_API_KEY": "Pexels (free): stock footage for Create",
    "COVERR_API_KEY": "Coverr (free): stock footage for Create",
    "TIKTOK_CLIENT_KEY": "Your TikTok developer app's client key",
    "TIKTOK_CLIENT_SECRET": "Your TikTok developer app's client secret",
    "TAVILY_API_KEY": "Tavily (free): live web search for the Ask chat",
    "DISCORD_BOT_TOKEN": "Your Discord bot's token: reads the campaign channels you follow",
    "NTFY_TOPIC": "Your ntfy topic: campaign alerts on your phone",
    "YOUTUBE_CLIENT_ID": "Your Google app's client ID: reads your YouTube Shorts' stats",
    "YOUTUBE_CLIENT_SECRET": "Your Google app's client secret",
    "WHOP_CLIENT_ID": "Your Whop app's ID (app_...): reads campaign feeds you've joined",
    "WHOP_CLIENT_SECRET": "Your Whop app's API key, used as its sign-in secret",
    "INSTAGRAM_APP_ID": "Your Meta app's ID: one-click Instagram sign-in",
    "INSTAGRAM_APP_SECRET": "Your Meta app's Instagram secret",
    "X_BEARER_TOKEN": "Your X app's Bearer Token: reads your X posts' views (paid per post read)",
}


def env_path() -> Path:
    return REPO_ROOT / ".env"


def key_set(name: str) -> bool:
    return bool(os.environ.get(name, "").strip())


def set_keys(values: dict[str, str]) -> list[str]:
    """Write keys to .env and this process's environment. Returns the names changed."""
    from dotenv import set_key

    unknown = set(values) - set(KEYS)
    if unknown:
        raise ValueError(f"unknown setting: {', '.join(sorted(unknown))}")
    path = env_path()
    if not path.exists():
        path.write_text("# Clipper's keys, written by the Control Center. Never commit this file.\n",
                        encoding="utf-8")
    changed = []
    for name, value in values.items():
        value = (value or "").strip()
        if any(ch in value for ch in "\r\n\"'"):
            raise ValueError(f"{name} contains characters a key can't have")
        set_key(str(path), name, value, quote_mode="never")
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
        changed.append(name)
    return changed


def ai_status() -> dict:
    """Whether the configured AI model can be used: {"ready", "backend", "detail"}."""
    from ..config import Config

    backend = Config.load().llm.backend
    needed = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(backend)
    if needed and not key_set(needed):
        return {"ready": False, "backend": backend,
                "detail": f"Add a {'Gemini' if backend == 'gemini' else 'Claude'} API key"}
    return {"ready": True, "backend": backend, "detail": ""}


def test_ai() -> tuple[bool, str]:
    """One tiny request to the configured model: does the key work?"""
    from ..config import Config
    from ..llm.base import LLMRequest
    from ..pipeline import build_backend

    try:
        backend = build_backend(Config.load())
        backend.complete(LLMRequest(system="Answer with one word.", user="Say OK.",
                                    max_output_tokens=20))
    except Exception as exc:
        message = str(exc).splitlines()[0][:200]
        if "API key not valid" in message or "API_KEY_INVALID" in message:
            message = "the key was refused; check you copied all of it"
        return False, message
    return True, f"{backend.describe()} answered"


class BrowserConnect:
    """One sign-in at a time through a platform's consent page, in the background;
    the page polls its state. `login(open_browser=...)` hands over the consent URL
    (the page opens it, not the server) and returns the token."""

    def __init__(self, publish, login, *, name: str, describe=lambda token: "",
                 platform: str | None = None) -> None:
        self.publish, self.login, self.name, self.describe = publish, login, name, describe
        self.platform = platform  # checked against the plan's accounts-per-platform (D89)
        self.state = "idle"         # idle | waiting | done | failed
        self.message = ""
        self.url = ""
        self._lock = threading.Lock()

    def view(self) -> dict:
        return {"state": self.state, "message": self.message, "url": self.url}

    def start(self) -> dict:
        with self._lock:
            if self.state == "waiting":
                return self.view()
            ready = threading.Event()
            self.state, self.message, self.url = "waiting", "", ""

            def capture(url: str) -> None:  # the page opens it, not the server
                self.url = url
                ready.set()

            def run() -> None:
                from . import accounts

                before = set(accounts.token_files(self.platform)()) if self.platform else set()
                try:
                    token = self.login(open_browser=capture)
                    refused = accounts.undo_if_over(self.platform, before) if self.platform else None
                    if refused:
                        raise RuntimeError(refused)
                    self.state = "done"
                    self.message = self.describe(token) or f"{self.name} account"
                except Exception as exc:  # shown on the page
                    self.state, self.message = "failed", str(exc)
                    log.info("%s connect failed: %s", self.name, exc)
                finally:
                    ready.set()
                    self.publish("accounts.changed")

            threading.Thread(target=run, daemon=True, name=f"{self.name.lower()}-login").start()
        ready.wait(10)
        return self.view()


def tiktok_connect(publish) -> BrowserConnect:
    from ..tiktok import api

    return BrowserConnect(publish, lambda **kw: api.login(**kw), name="TikTok", platform="tiktok",
                          describe=lambda token: token.get("display_name") or "")


def youtube_connect(publish) -> BrowserConnect:
    from ..youtube import api

    return BrowserConnect(publish, lambda **kw: api.login(**kw), name="YouTube", platform="youtube",
                          describe=lambda token: token.get("display_name") or "")


def instagram_connect(publish) -> BrowserConnect:
    from ..instagram import api

    return BrowserConnect(publish, lambda **kw: api.login_browser(**kw), name="Instagram", platform="instagram",
                          describe=lambda token: token.get("display_name") or "")


def whop_connect(publish) -> BrowserConnect:
    from ..watch import whop

    return BrowserConnect(publish, lambda **kw: whop.login(**kw), name="Whop")


def system_check() -> list[dict]:
    from ..doctor import run_checks

    return [{"name": r.name, "status": str(r.status), "detail": r.detail, "fix": r.fix}
            for r in run_checks()]
