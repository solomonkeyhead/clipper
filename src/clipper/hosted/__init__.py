"""The hosted website's gateway (D228): accounts, one Clipper per signed-in user, and a proxy to it.

Clipper itself stays one person's app. The gateway (`python -m clipper.hosted`) signs people in, starts a
Clipper process for each with its own data folder (`<root>/users/<id>/data`), writes the plan that process
must keep to (`plan.json`, see `catalog.py`) and passes the user's requests through to it (`proxy.py`).
"""

from __future__ import annotations

import os
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def port() -> int:
    """The gateway's port: users' Clippers reach its connect service there (D239)."""
    return int(env("PORT", "8080"))


def root() -> Path:
    """Everything the gateway keeps: its database and every user's folder."""
    return Path(env("CLIPPER_HOSTED_ROOT", "/srv/clipper"))


def user_dir(user_id: int) -> Path:
    return root() / "users" / str(int(user_id))


def data_dir(user_id: int) -> Path:
    """The user's Clipper data folder (its CLIPPER_DATA_DIR)."""
    return user_dir(user_id) / "data"
