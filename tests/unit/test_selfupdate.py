"""`clipper studio` pulls the latest code first, only when that's safe (D112)."""

from __future__ import annotations

import subprocess

import pytest

from clipper import selfupdate


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repos(tmp_path, monkeypatch):
    origin, mine, theirs = tmp_path / "origin.git", tmp_path / "mine", tmp_path / "theirs"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    for path in (mine, theirs):
        subprocess.run(["git", "clone", "-q", str(origin), str(path)], check=True, capture_output=True)
        git(path, "config", "user.email", "t@t")
        git(path, "config", "user.name", "t")
        git(path, "checkout", "-q", "-B", "master")
    (theirs / "a.py").write_text("1")
    git(theirs, "add", "a.py")
    git(theirs, "commit", "-qm", "one")
    git(theirs, "push", "-q", "origin", "master")
    git(mine, "pull", "-q", "origin", "master")
    monkeypatch.setattr(selfupdate, "REPO_ROOT", mine)
    monkeypatch.delenv("CLIPPER_NO_UPDATE", raising=False)
    return mine, theirs


def push_change(theirs, text="2"):
    (theirs / "a.py").write_text(text)
    git(theirs, "commit", "-qam", "two")
    git(theirs, "push", "-q", "origin", "master")


def test_nothing_new_says_nothing_and_new_commits_are_pulled(repos):
    mine, theirs = repos
    assert selfupdate.pull() == ""
    push_change(theirs)
    assert selfupdate.pull() == "updated to the latest version (1 new change)"
    assert (mine / "a.py").read_text() == "2"


def test_local_edits_are_never_touched(repos):
    mine, theirs = repos
    push_change(theirs)
    (mine / "a.py").write_text("my edit")
    assert selfupdate.pull().startswith("not updated: you have local changes")
    assert (mine / "a.py").read_text() == "my edit"


def test_its_own_commits_or_another_branch_or_turned_off_skip_it(repos, monkeypatch):
    mine, theirs = repos
    push_change(theirs)
    (mine / "b.py").write_text("x")
    git(mine, "add", "b.py")
    git(mine, "commit", "-qm", "mine")
    assert "pull by hand" in selfupdate.pull()
    git(mine, "checkout", "-q", "-b", "experiment")
    assert "on branch experiment" in selfupdate.pull()
    monkeypatch.setenv("CLIPPER_NO_UPDATE", "1")
    assert selfupdate.pull() == ""


def test_changed_dependencies_are_installed_with_uv_when_there_is_no_pip(repos, monkeypatch):
    _, theirs = repos
    (theirs / "pyproject.toml").write_text("x")
    git(theirs, "add", "pyproject.toml")
    git(theirs, "commit", "-qm", "dep")
    git(theirs, "push", "-q", "origin", "master")
    ran = []
    real = subprocess.run
    monkeypatch.setattr(selfupdate.shutil, "which", lambda name: "/bin/uv")
    monkeypatch.setattr(selfupdate.subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd) or real(["git", "--version"], **kw) if cmd[0] == "/bin/uv" else real(cmd, **kw))
    assert selfupdate.pull().startswith("updated")
    assert ran and ran[0][:3] == ["/bin/uv", "pip", "install"] and "--python" in ran[0]
