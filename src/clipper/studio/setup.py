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
    "TIKTOK_CLIENT_KEY": "Your TikTok developer app's client key",
    "TIKTOK_CLIENT_SECRET": "Your TikTok developer app's client secret",
    "TAVILY_API_KEY": "Tavily (free): live web search for the Ask chat",
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


class TikTokConnect:
    """One TikTok login at a time, in the background; the page polls its state."""

    def __init__(self, publish) -> None:
        self.publish = publish
        self.state = "idle"         # idle | waiting | done | failed
        self.message = ""
        self.url = ""
        self._lock = threading.Lock()

    def view(self) -> dict:
        return {"state": self.state, "message": self.message, "url": self.url}

    def start(self) -> dict:
        from ..tiktok import api

        with self._lock:
            if self.state == "waiting":
                return self.view()
            ready = threading.Event()
            self.state, self.message, self.url = "waiting", "", ""

            def capture(url: str) -> None:  # the page opens it, not the server
                self.url = url
                ready.set()

            def run() -> None:
                try:
                    token = api.login(open_browser=capture)
                    self.state = "done"
                    self.message = token.get("display_name") or "TikTok account"
                except Exception as exc:  # shown on the page
                    self.state, self.message = "failed", str(exc)
                    log.info("TikTok connect failed: %s", exc)
                finally:
                    ready.set()
                    self.publish("accounts.changed")

            threading.Thread(target=run, daemon=True, name="tiktok-login").start()
        ready.wait(10)
        return self.view()


def system_check() -> list[dict]:
    from ..doctor import run_checks

    return [{"name": r.name, "status": str(r.status), "detail": r.detail, "fix": r.fix}
            for r in run_checks()]
