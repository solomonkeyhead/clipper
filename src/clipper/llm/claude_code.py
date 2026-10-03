"""Claude through Claude Code, on the user's own Claude plan -- no API key, no bill (D113).

`claude -p` answers one prompt and exits, logged in as the user, so a Pro or Max
plan's usage covers it (it counts toward that plan's limits; nothing is charged
per call). Run from an empty temporary folder with only the Read tool, so it can
look at the images handed to it and can do nothing else; no settings files, no
saved session. The schema goes to --json-schema, and the answer comes back as
`structured_output`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel

from ..utils.logging import get_logger
from .base import (
    LLMBackend,
    LLMConfigError,
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimited,
    register,
)

log = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
#: The API-price value of every call made so far (Claude Code reports it; on a plan it is
#: not billed), for measuring what a video costs per model.
spent_usd = 0.0
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}


def cli() -> str | None:
    """The `claude` command, if Claude Code is installed: on PATH, or where its Windows
    installers put it (a desktop icon's Clipper may not have the terminal's PATH, D116)."""
    import os

    found = shutil.which("claude")
    if found:
        return found
    home, appdata = Path.home(), os.environ.get("APPDATA", "")
    for candidate in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude",
                      Path(appdata) / "npm" / "claude.cmd" if appdata else None,
                      home / ".claude" / "local" / "claude"):
        if candidate and candidate.is_file():
            return str(candidate)
    return None


def _shim(command: str) -> bool:
    """Whether `claude` is a Windows batch shim (npm's claude.cmd) rather than a program."""
    return command.lower().endswith((".cmd", ".bat"))


def _json_in(text: str) -> str:
    """The JSON object in an answer that may wrap it in words or a ``` fence."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMError(f"Claude Code's answer had no JSON in it: {text[:200]}")
    return text[start:end + 1]


@register
class ClaudeCodeBackend(LLMBackend):
    name: ClassVar[str] = "claude_code"
    supports_schema: ClassVar[bool] = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.command = cli()
        if not self.command:
            raise LLMConfigError("Claude Code isn't installed (no `claude` command)")
        self.model = self.model or DEFAULT_MODEL

    def _complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        schema = request.response_schema
        with tempfile.TemporaryDirectory(prefix="clipper-claude-") as tmp:
            names = []
            for k, (data, mime) in enumerate(request.media, start=1):
                if mime not in EXT:
                    raise LLMConfigError(f"Claude Code can't take {mime}; images only")
                name = f"{k}{EXT[mime]}"
                (Path(tmp) / name).write_bytes(data)
                names.append(name)
            prompt = request.user
            if names:
                prompt += (f"\n\nThe images, in order, are the files {', '.join(names)} in the current folder: "
                           "read every one of them before you answer.")
            # The instructions go in a file, never on the command line: on Windows `claude` is
            # often a .cmd shim, and cmd.exe cuts an argument at its first line break (D114).
            (Path(tmp) / "instructions.txt").write_text(request.system, encoding="utf-8")
            args = [self.command, "-p", "--output-format", "json", "--model", self.model,
                    "--no-session-persistence", "--setting-sources", "",
                    "--tools", "Read" if names else "", *(["--allowedTools", "Read"] if names else []),
                    "--system-prompt-file", "instructions.txt"]
            wanted = schema if isinstance(schema, type) and issubclass(schema, BaseModel) else None
            if wanted and _shim(self.command):
                # No JSON on a .cmd command line either (cmd.exe and quotes): asked for in words.
                prompt += ("\n\nAnswer with only a JSON object matching this JSON Schema, nothing else:\n"
                           + json.dumps(wanted.model_json_schema()))
            elif wanted:
                args += ["--json-schema", json.dumps(wanted.model_json_schema(), separators=(",", ":"))]
            try:
                done = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                      cwd=tmp, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"Claude Code took over {self.timeout:.0f}s") from exc
            except OSError as exc:
                raise LLMConfigError(f"Claude Code couldn't start: {exc}") from exc
        try:
            reply = json.loads(done.stdout)
        except ValueError as exc:
            raise LLMError(f"Claude Code answered oddly: {(done.stderr or done.stdout)[-300:]}") from exc
        text = reply.get("result") or ""
        if reply.get("is_error") or done.returncode:
            if "limit" in text.lower():  # the plan's usage limit: the next model answers instead
                raise RateLimited(f"Claude plan limit: {text[:200]}")
            if "login" in text.lower() or "auth" in text.lower():
                raise LLMConfigError(f"Claude Code isn't logged in: {text[:200]}")
            raise LLMError(f"Claude Code failed: {text[:300] or done.stderr[-300:]}")
        if reply.get("structured_output") is not None:
            text = json.dumps(reply["structured_output"])
        elif wanted:
            text = _json_in(text)
        if not text.strip():
            raise LLMError("Claude Code returned nothing")
        global spent_usd
        spent_usd += float(reply.get("total_cost_usd") or 0)  # what the call would cost at API prices
        usage = reply.get("usage") or {}
        return LLMResponse(text=text, model=self.model, prompt_tokens=usage.get("input_tokens", 0),
                           output_tokens=usage.get("output_tokens", 0), latency=time.perf_counter() - started)
