"""The hosted site's backup and disk-check scripts (D-NEW)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sqlite3
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / "ops" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backup = _load("backup")
check_disk = _load("check_disk")


def _db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("create table if not exists t(v)")
    c.execute("delete from t")
    c.execute("insert into t values (?)", (value,))
    c.commit()
    c.close()


def _val(path: Path) -> str:
    c = sqlite3.connect(path)
    try:
        return c.execute("select v from t").fetchone()[0]
    finally:
        c.close()


def test_backup_restore_and_prune(tmp_path):
    root, dest = tmp_path / "root", tmp_path / "bk"
    _db(root / "gateway.sqlite", "gw")
    _db(root / "users" / "1" / "data" / "library.db", "lib")
    _db(root / "users" / "1" / "data" / "usage.sqlite", "use")
    (root / "users" / "1" / "data" / "plan.json").write_text('{"plan": "free"}')
    dest.mkdir()
    for i in range(1, 20):  # 19 old days: only the newest 13 survive beside today
        (dest / f"2026-09-{i:02d}").mkdir()

    out = backup.backup(root, dest, dt.date(2026, 10, 20))

    assert _val(out / "gateway.sqlite") == "gw"
    assert _val(out / "users/1/data/library.db") == "lib"
    assert (out / "users/1/data/plan.json").read_text() == '{"plan": "free"}'
    assert len([p for p in dest.iterdir() if p.is_dir()]) == backup.KEEP_DAYS
    assert not (dest / "2026-10-20.partial").exists()

    _db(root / "gateway.sqlite", "changed")
    assert backup.main(["--root", str(root), "--dest", str(dest), "--restore", "2026-10-20"]) == 0
    assert _val(root / "gateway.sqlite") == "gw"


def test_restore_refuses_unknown_day_and_running_app(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        backup.restore(tmp_path, tmp_path, "2026-01-01")
    (tmp_path / "2026-01-01").mkdir()
    monkeypatch.setattr(backup, "app_running", lambda port: True)
    with pytest.raises(SystemExit):
        backup.restore(tmp_path, tmp_path, "2026-01-01")
    assert backup.restore(tmp_path, tmp_path, "2026-01-01", force=True) == 0


def test_backup_failure_exits_nonzero(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "gateway.sqlite").write_text("not a database")
    assert backup.main(["--root", str(root), "--dest", str(tmp_path / "bk")]) == 1
    assert not list((tmp_path / "bk").glob("*.partial"))


def test_check_disk(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(check_disk, "send", lambda url, to, text: sent.append(to))
    monkeypatch.setenv("SMTP_URL", "smtp://u:p@host:587")
    assert check_disk.main(["--path", str(tmp_path), "--min-free", "0.0"]) == 0
    assert check_disk.main(["--path", str(tmp_path), "--min-free", "1.01", "--to", "a@b.c"]) == 1
    assert sent == ["a@b.c"]
