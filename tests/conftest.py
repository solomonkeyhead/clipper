"""Shared fixtures.

Tests must never touch the real ``data/`` tree or the user's ``.env``, so the
data root is redirected per-test and the API-key environment is cleared.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clipper import paths


@pytest.fixture
def data_root(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CLIPPER_DATA_DIR at a temp dir and clear the lru_cache behind it."""
    root = Path(tmp_path) / "data"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(root))
    paths.data_root.cache_clear()
    yield root
    paths.data_root.cache_clear()


@pytest.fixture(autouse=True)
def _no_ambient_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key in the developer's shell must not change test outcomes."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def valid_campaign_dict() -> dict:
    return {
        "name": "test-campaign",
        "source_authorization": "Whop Content Rewards campaign 'Test', official content bank",
        "duration": {"min_seconds": 15, "max_seconds": 60},
        "required_hashtags": ["#test"],
    }


def _ffmpeg_available() -> bool:
    from clipper.render import ffmpeg

    try:
        ffmpeg.ffmpeg_path()
    except ffmpeg.FFmpegNotFound:
        return False
    return True


needs_ffmpeg = pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not on this machine")

needs_windows = pytest.mark.skipif(os.name != "nt", reason="Windows-specific behaviour")


@pytest.fixture(scope="session")
def media_cache(tmp_path_factory) -> Path:
    """Session-scoped home for generated fixture media.

    Encoding a fixture costs a second or two, so they are generated once and
    reused across the whole run rather than per test.
    """
    from .fixtures import synthetic

    root = tmp_path_factory.mktemp("media")
    synthetic.set_cache_dir(root)
    return root
