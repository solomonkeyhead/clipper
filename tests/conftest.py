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
def _no_ratings_from_the_real_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs learn from the user's ratings in data/clipper.db; tests must not."""
    from clipper import runner

    monkeypatch.setattr(runner, "_learning", lambda config, campaign: (None, ""))


@pytest.fixture(autouse=True)
def _no_watching_unless_asked(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """The mock backend can take video, so every scoring run would cut and "watch"
    a dozen moments; only tests marked `watch` do (tests/unit/test_visual.py)."""
    if request.node.get_closest_marker("watch"):
        return
    from clipper import pipeline

    monkeypatch.setattr(pipeline, "_watch", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_background_rule_checks(monkeypatch: pytest.MonkeyPatch) -> list:
    """Saving a campaign or finishing a run starts the rule check in a thread
    (studio/rulecheck.py), which could outlive the test's temporary data dir and
    reach the real library. Tests call `rulecheck.recheck` directly instead;
    the names asked for are kept here."""
    from clipper.studio import rulecheck

    asked: list = []
    monkeypatch.setattr(rulecheck, "start", lambda names, publish=None: asked.append(names))
    return asked


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


@pytest.fixture(scope="module")
def module_data_root(tmp_path_factory) -> Path:
    """A data root shared by every test in a module.

    The per-test `data_root` fixture gives each test a clean tree, which means
    each one re-ingests and re-transcribes from scratch. For modules whose tests
    all exercise the same source that is pure waste -- it took the full suite
    past ten minutes -- so heavy end-to-end modules share one root and let the
    stage cache do its job.
    """
    root = Path(tmp_path_factory.mktemp("module_data"))
    previous = os.environ.get("CLIPPER_DATA_DIR")
    os.environ["CLIPPER_DATA_DIR"] = str(root)
    paths.data_root.cache_clear()
    yield root
    if previous is None:
        os.environ.pop("CLIPPER_DATA_DIR", None)
    else:
        os.environ["CLIPPER_DATA_DIR"] = previous
    paths.data_root.cache_clear()
