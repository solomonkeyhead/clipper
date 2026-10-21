"""The hosted plan meter (D237): footage minutes, Create Shorts and redraws, credits, the 402s, /api/usage, the
watermark, retention, accounts per platform, the queue, and the gateway's side. On one's own computer: nothing."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import meter
from clipper.create import meterhooks
from clipper.create.ai import CreateError
from clipper.studio import db

DAY = 86400


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_file(root: Path, *, plan: str = "free", credits: dict | None = None, start: datetime | None = None,
              **limits) -> dict:
    """A plan.json as the gateway writes it (hosted/catalog.plan_json), Free's limits unless given."""
    start = start or datetime.now(UTC) - timedelta(days=1)
    doc = {"plan": plan, "status": "active", "period_start": iso(start), "period_end": iso(start + timedelta(days=30)),
           "limits": {**meter.FREE["limits"], **limits}, "credits": credits or {"minutes": 0, "shorts": 0},
           "ai_budget_usd": 2.0, "billing_url": "/billing"}
    (root / "plan.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def next_month(root: Path, **kw) -> None:
    """A new period from a minute from now: what was charged so far is last month's."""
    plan_file(root, start=datetime.now(UTC) + timedelta(minutes=1), **kw)


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("CLIPPER_HOSTED", "1")


@pytest.fixture
def local(monkeypatch):
    for name in ("CLIPPER_HOSTED", "CLIPPER_HOST"):
        monkeypatch.delenv(name, raising=False)


def old(path: Path, days: float = 8) -> Path:
    stamp = time.time() - days * DAY
    os.utime(path, (stamp, stamp))
    return path


# ---------- one's own computer

def test_on_ones_own_computer_nothing_is_limited_counted_or_deleted(data_root, local):
    from clipper.studio import plans

    assert meter.plan() is None
    for i in range(20):
        meter.charge_source(f"s{i}", 3 * 3600)
        meterhooks.on_first_build(i)
        meterhooks.on_redraw(i, 50)
        assert meter.watermark(f"clip:s:{i}") is False
    meter.check_sources([str(data_root / "nothing.mp4")])
    assert meter.render_workers(3) == 3 and meter.accounts_per_platform() is None
    assert meter.usage() == {"hosted": False}
    clip = data_root / "library" / "c" / "x.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"x")
    old(clip)
    assert meter.retention() == {} and clip.exists()
    assert not (data_root / "meter.sqlite").exists() and not (data_root / "meter.json").exists()
    assert plans.current() == "pro"                      # the Settings tier, as before


def test_usage_says_not_hosted_locally(data_root, local):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    assert TestClient(server.create_app()).get("/api/usage").json() == {"hosted": False}


# ---------- minutes

def test_a_source_is_charged_once_in_whole_minutes_at_least_ten(data_root):
    plan_file(data_root, minutes=60)
    meter.charge_source("a", 125)            # 3 minutes: billed 10
    meter.charge_source("a", 125)            # a re-run or re-cut: free
    meter.charge_source("b", 30 * 60 + 1)    # rounded up: 31
    assert meter.usage()["minutes"] == {"used": 41, "limit": 60, "credits_left": 0}
    with pytest.raises(meter.PlanLimit, match="counts as 20 footage minutes, and your plan has 19 left"):
        meter.charge_source("c", 20 * 60)
    assert meter.usage()["minutes"]["used"] == 41    # refused: nothing charged
    assert json.loads((data_root / "meter.json").read_text()) == {
        "period_start": meter.plan()["period_start"], "minutes_used": 41, "shorts_used": 0,
        "credits_used": {"minutes": 0, "shorts": 0}}


def test_the_allowance_goes_first_then_credits(data_root):
    plan_file(data_root, minutes=60, credits={"minutes": 120, "shorts": 0})
    meter.charge_source("a", 50 * 60)
    meter.charge_source("b", 30 * 60)        # 10 from the month, 20 from credits
    assert meter.usage()["minutes"] == {"used": 60, "limit": 60, "credits_left": 100}
    assert json.loads((data_root / "meter.json").read_text())["credits_used"] == {"minutes": 20, "shorts": 0}


def test_a_new_month_starts_the_allowance_again_and_credits_used_stay_used(data_root):
    plan_file(data_root, minutes=60, credits={"minutes": 30, "shorts": 0})
    meter.charge_source("a", 80 * 60)        # 60 + 20 credits
    next_month(data_root, minutes=60, credits={"minutes": 30, "shorts": 0})
    assert meter.usage()["minutes"] == {"used": 0, "limit": 60, "credits_left": 10}
    meter.charge_source("a", 80 * 60)        # charged once ever
    assert meter.usage()["minutes"]["used"] == 0


def test_ingest_charges_once_its_length_is_known_before_any_transcription(data_root, tmp_path, monkeypatch):
    from clipper.ingest import download
    from clipper.models import MediaInfo

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 100)
    monkeypatch.setattr(download, "probe", lambda p: MediaInfo(path=str(p), duration=1200, width=1920, height=1080,
                                                               fps=30, has_audio=True))
    monkeypatch.setattr(download, "extract_audio", lambda src, dest, **k: dest.write_bytes(b"") and dest)
    plan_file(data_root, minutes=10)
    with pytest.raises(meter.PlanLimit, match="20 footage minutes"):
        download.ingest(str(video))
    plan_file(data_root, minutes=60)
    download.ingest(str(video))
    download.ingest(str(video))              # reused: not charged again
    assert meter.usage()["minutes"]["used"] == 20


@pytest.fixture
def client(data_root, tmp_path, monkeypatch, valid_campaign_dict):
    import yaml
    from fastapi.testclient import TestClient

    from clipper.studio import server

    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    (campaigns / "test-campaign.yaml").write_text(yaml.safe_dump(valid_campaign_dict), encoding="utf-8")
    monkeypatch.setattr(server, "campaigns_dir", lambda: campaigns)
    monkeypatch.setattr(server, "source_folders", lambda: [data_root / "downloads"])
    return TestClient(server.create_app())


def test_new_clips_answers_402_at_submit_when_the_minutes_are_short(client, data_root, monkeypatch):
    from clipper.ingest import probe

    video = data_root / "downloads" / "episode.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"x" * 100)
    monkeypatch.setattr(probe, "probe", lambda p: SimpleNamespace(duration=2 * 3600))
    plan_file(data_root, minutes=60)
    r = client.post("/api/jobs", json={"campaign": "test-campaign", "source": str(video)})
    assert r.status_code == 402
    detail = r.json()["detail"]
    assert detail["code"] == "plan_limit" and detail["billing_url"] == "/billing"
    assert "120 footage minutes" in detail["message"] and "60 left" in detail["message"]
    assert client.get("/api/jobs").json() == []


def test_the_ai_budget_reaching_an_endpoint_is_a_402(data_root):
    from fastapi.testclient import TestClient

    from clipper.llm.usage import BudgetExceeded
    from clipper.studio import server

    app = server.create_app()

    @app.post("/api/test-budget")
    def spend() -> dict:
        raise BudgetExceeded("Your plan's AI allowance for this period is used up.")

    r = TestClient(app).post("/api/test-budget")
    assert r.status_code == 402 and r.json()["detail"] == {
        "code": "ai_budget", "message": "Your plan's AI allowance for this period is used up.", "billing_url": "/billing"}


def test_usage_follows_the_contract(client, data_root):
    plan_file(data_root, minutes=60, credits={"minutes": 120, "shorts": 5})
    meter.charge_source("a", 42 * 60)
    meter.charge_first_build(1)
    meter.watermark("clip:a:001")
    got = client.get("/api/usage").json()
    assert got == {"hosted": True, "plan": "free", "status": "active", "period_end": meter.plan()["period_end"],
                   "minutes": {"used": 42, "limit": 60, "credits_left": 120},
                   "shorts": {"used": 1, "limit": 1, "credits_left": 5, "once": True},
                   "redraws_per_short": 3, "retention_days": 7, "watermark_after": 3, "clips_this_period": 1,
                   "ai": {"spent_usd": 0.0, "budget_usd": 2.0}, "billing_url": "/billing", "logout_url": "/logout"}


# ---------- Shorts

def test_free_has_one_short_ever_with_its_redraws(data_root):
    plan_file(data_root)                     # 3 redraws a Short (plan.json, D236)
    meterhooks.on_first_build(1)
    meterhooks.on_first_build(1)             # a first build that failed, tried again: the same Short
    meterhooks.on_redraw(1, 2)
    meterhooks.on_redraw(1, 1)               # 3: what the Short includes
    with pytest.raises(CreateError, match="These drawings need 1 more Short"):
        meterhooks.on_redraw(1, 1)
    with pytest.raises(CreateError, match="no Create Shorts left"):
        meterhooks.on_first_build(2)
    next_month(data_root)                    # once ever, not a month's
    with pytest.raises(CreateError):
        meterhooks.on_first_build(2)
    assert meter.usage()["shorts"] == {"used": 1, "limit": 1, "credits_left": 0, "once": True}


def test_redraws_past_a_shorts_own_use_one_more_short_each_batch(data_root):
    plan_file(data_root, plan="starter", shorts=1, shorts_once=False, redraws_per_short=3,
              credits={"minutes": 0, "shorts": 5})
    meter.charge_first_build(1)              # the month's
    meter.charge_redraws(1, 5)               # 3 included, 2 more: one Short from credits
    meter.charge_redraws(1, 1)               # 6 of the 6 its two Shorts give
    assert meter.usage()["shorts"]["credits_left"] == 4
    meter.charge_redraws(1, 7)               # 3 more Shorts
    assert meter.usage()["shorts"] == {"used": 1, "limit": 1, "credits_left": 1, "once": False}


def test_create_answers_402_before_a_build_the_plan_cant_pay_for(data_root, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.create import store
    from clipper.studio import create_api, server

    from .test_create_costs import _sketched

    client = TestClient(server.create_app())
    client.post("/api/create/channels", json={"name": "Physics Lab", "pack": "physics"})
    started = []
    monkeypatch.setattr(create_api, "_start", lambda video_id, publish: started.append(video_id))
    first = store.add_video(None, _sketched("a").model_dump(), "")
    store.update_video(first, status="failed", voice="voice.wav")
    plan_file(data_root, shorts=0)
    r = client.post(f"/api/create/videos/{first}/build")
    assert r.status_code == 402 and r.json()["detail"]["code"] == "plan_limit" and not started
    built = store.add_video(None, _sketched("a", "b").model_dump(), "")
    store.update_video(built, status="built", voice="voice.wav", clip_id=1)
    r = client.post(f"/api/create/videos/{built}/build")    # two drawings to make again, no Short to cover them
    assert r.status_code == 402 and "These drawings need 1 more Short" in r.json()["detail"]["message"]
    plan_file(data_root, shorts=1)
    assert client.post(f"/api/create/videos/{first}/build").status_code == 200 and started == [first]


# ---------- the watermark and the queue

def test_clips_past_frees_first_three_carry_the_watermark_and_keep_what_they_got(data_root):
    plan_file(data_root, watermark_after=3)
    assert [meter.watermark(f"clip:s:{i}") for i in range(5)] == [False, False, False, True, True]
    assert meter.watermark("clip:s:0") is False and meter.watermark("clip:s:4") is True   # made again
    assert meter.usage()["clips_this_period"] == 5
    next_month(data_root, watermark_after=3)
    assert meter.usage()["clips_this_period"] == 0 and meter.watermark("clip:t:0") is False


def test_paid_plans_never_watermark(data_root):
    plan_file(data_root, plan="pro", watermark_after=None)
    assert not any(meter.watermark(f"clip:s:{i}") for i in range(10))


def test_the_watermark_is_drawn_only_when_asked_and_ffmpeg_reads_it(tmp_path):
    from clipper.models import LayoutPlan
    from clipper.render import graph
    from clipper.render.ffmpeg import FFmpegNotFound, bundled_fonts_dir, ffmpeg_path

    spec = graph.RenderSpec(source=Path("in.mp4"), output=Path("out.mp4"), start=0, duration=1,
                            layout=LayoutPlan(kind="blurred_fit"), ass_path=None, fonts_dir=bundled_fonts_dir(),
                            width=1080, height=1920, fps=30, encoder="libx264", loudness_lufs=-14,
                            true_peak_dbtp=-1.5, crf=20, nvenc_cq=23, x264_preset="veryfast", audio_bitrate="192k",
                            audio_rate=48000)
    assert "drawtext" not in graph.build_video_filter(spec)
    marked = graph.build_video_filter(graph.RenderSpec(**{**spec.__dict__, "watermark": graph.WATERMARK}))
    assert "drawtext" in marked and "Made with Clipper" in marked and marked.endswith("[v]")
    try:
        exe = ffmpeg_path()
    except FFmpegNotFound:
        pytest.skip("no FFmpeg here")
    r = subprocess.run([str(exe), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=gray:s=1920x1080:d=0.2", "-filter_complex", marked, "-map", "[v]", "-frames:v", "1",
                        "-y", str(tmp_path / "frame.png")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_queue_sets_how_many_clips_render_at_once(data_root):
    for queue, workers in (("normal", 1), ("fast", 2), ("fastest", 3)):
        plan_file(data_root, queue=queue)
        assert meter.render_workers(3) == workers


# ---------- hosted without a readable plan.json: Free, and nothing deleted

def test_hosted_without_a_readable_plan_frees_limits_apply(data_root, hosted):
    assert meter.plan()["fallback"] and meter.plan()["limits"]["minutes"] == 60
    with pytest.raises(meter.PlanLimit):
        meter.charge_source("long", 3 * 3600)
    (data_root / "plan.json").write_text("{not json", encoding="utf-8")
    assert meter.plan()["fallback"] and meter.plan()["limits"]["watermark_after"] == 3
    clip = data_root / "downloads" / "old.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"x")
    old(clip)
    assert meter.retention() == {} and clip.exists()


def test_frees_stand_in_is_the_gateways_free_plan():
    from clipper.hosted import catalog

    free = catalog.PLANS["free"]
    assert meter.FREE["limits"] == {
        "minutes": free.minutes, "shorts": free.shorts, "shorts_once": free.shorts_once,
        "redraws_per_short": catalog.REDRAWS_PER_SHORT, "retention_days": free.retention_days,
        "accounts_per_platform": free.accounts_per_platform, "seats": free.seats,
        "watermark_after": free.watermark_after, "queue": free.queue}


# ---------- retention, one kind at a time

def test_retention_deletes_clip_files_older_than_the_plan_keeps_and_keeps_their_rows(data_root, hosted):
    plan_file(data_root, retention_days=7)
    ids = {}
    for name, days in (("old", 8), ("new", 2)):
        path = data_root / "library" / "c" / f"{name}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        old(path, days)
        with db.connect() as con:
            ids[name] = db.upsert_clip(con, {"campaign": "c", "source_id": "s", "clip_id": name, "title": name,
                                             "file": f"c/{name}.mp4", "caption": ""})
            stamp = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
            con.execute("UPDATE clips SET created_at = ? WHERE id = ?", (stamp, ids[name]))
    outside = data_root.parent / "outside.mp4"         # a row pointing out of the library is never followed
    outside.write_bytes(b"x")
    old(outside)
    with db.connect() as con:
        db.upsert_clip(con, {"campaign": "c", "source_id": "s", "clip_id": "x", "title": "x",
                             "file": "../../outside.mp4", "caption": ""})
        con.execute("UPDATE clips SET created_at = '2020-01-01 00:00' WHERE clip_id = 'x'")
    assert meter.retention()["clips"] == 1
    assert not (data_root / "library" / "c" / "old.mp4").exists() and (data_root / "library" / "c" / "new.mp4").exists()
    assert outside.exists()
    with db.connect() as con:
        assert db.clip(con, ids["old"])["file"] == "" and db.clip(con, ids["new"])["file"] == "c/new.mp4"


def test_retention_deletes_sources_older_than_the_plan_keeps(data_root, hosted):
    plan_file(data_root, retention_days=30)
    folder = data_root / "downloads"
    folder.mkdir()
    for name, days in (("old.mp4", 31), ("new.mp4", 29)):
        (folder / name).write_bytes(b"x")
        old(folder / name, days)
    assert meter.retention()["sources"] == 1
    assert sorted(p.name for p in folder.iterdir()) == ["new.mp4"]


def test_retention_deletes_work_folders_not_used_within_the_plans_days(data_root, hosted):
    plan_file(data_root, retention_days=7)
    work = data_root / "work"
    for folder, days in ((work / "oldsource", 9), (work / "newsource", 1), (work / "runs" / "oldsource", 9)):
        folder.mkdir(parents=True)
        (folder / "info.json").write_text("{}")
        old(folder / "info.json", days)
        old(folder, days)
    recent = work / "usedsource"                        # old, but a file in it was written yesterday
    recent.mkdir()
    for name, days in (("info.json", 20), ("taste.json", 1)):
        (recent / name).write_text("{}")
        old(recent / name, days)
    old(recent, 20)
    assert meter.retention()["work"] == 2
    assert sorted(p.name for p in work.iterdir()) == ["newsource", "runs", "usedsource"]
    assert list((work / "runs").iterdir()) == []


# ---------- accounts and the older feature tiers

def test_hosted_an_account_over_the_plans_limit_is_undone(data_root):
    from clipper.studio import accounts
    from clipper.youtube import api as yt_api

    plan_file(data_root, accounts_per_platform=1)
    folder = yt_api.accounts_dir()
    folder.mkdir(parents=True)
    (folder / "first.json").write_text("{}", encoding="utf-8")
    assert accounts.undo_if_over("youtube", set()) is None                         # the first
    assert accounts.undo_if_over("youtube", set(yt_api.token_files())) is None     # reconnecting it
    before = set(yt_api.token_files())
    (folder / "second.json").write_text("{}", encoding="utf-8")
    assert "Your plan connects 1 YouTube account" in accounts.undo_if_over("youtube", before)
    assert [p.stem for p in yt_api.token_files()] == ["first"]
    plan_file(data_root, plan="pro", accounts_per_platform=5)
    (folder / "second.json").write_text("{}", encoding="utf-8")
    assert accounts.undo_if_over("youtube", before) is None


def test_hosted_the_feature_tier_is_the_accounts_plan(data_root):
    from clipper.studio import plans

    with db.connect() as con:
        db.set_setting(con, "plan", "free")      # a Settings value from before: not what counts hosted
    plan_file(data_root, plan="starter")
    assert plans.current() == "pro" and plans.batch_limit() is None
    plan_file(data_root, plan="free")
    assert plans.current() == "free" and plans.batch_limit() == 3 and plans.clip_count(None, None) == plans.CLIP_CAP


# ---------- the gateway

def test_billing_shows_what_is_left(tmp_path, monkeypatch):
    from clipper.hosted import catalog, data_dir, users

    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path / "root"))
    user = users.create_user("someone@example.com", "a long password", age_confirmed=True, terms_version="1",
                             period=catalog.first_period())
    users.add_credits(user["id"], minutes=120, shorts=5, source="pack:minutes")
    doc = catalog.plan_json(user)
    data_dir(user["id"]).mkdir(parents=True, exist_ok=True)
    (data_dir(user["id"]) / "meter.json").write_text(json.dumps(
        {"period_start": doc["period_start"], "minutes_used": 42, "shorts_used": 1,
         "credits_used": {"minutes": 20, "shorts": 1}}), encoding="utf-8")
    assert catalog.left(user) == {"minutes_used": 42, "shorts_used": 1, "credits": {"minutes": 100, "shorts": 4}}
    (data_dir(user["id"]) / "meter.json").write_text(json.dumps(
        {"period_start": "2020-01-01T00:00:00Z", "minutes_used": 42, "shorts_used": 0,
         "credits_used": {"minutes": 20, "shorts": 0}}), encoding="utf-8")
    assert catalog.left(user)["minutes_used"] == 0           # last month's use


def test_billing_page_shows_use_and_credits_left(gw_client):
    from clipper.hosted import catalog, data_dir, users

    from .test_hosted_accounts import signup

    signup(gw_client)
    user = users.by_email("someone@example.com")
    users.add_credits(user["id"], minutes=120, source="pack:minutes")
    doc = catalog.plan_json(user)
    (data_dir(user["id"]) / "meter.json").write_text(json.dumps(
        {"period_start": doc["period_start"], "minutes_used": 42, "shorts_used": 0,
         "credits_used": {"minutes": 20, "shorts": 0}}), encoding="utf-8")
    page = gw_client.get("/billing").text
    assert "Used: 42 of 60 minutes" in page and "Credits left from packs: 100 minutes" in page


@pytest.fixture
def gw_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.hosted.app import create_app
    from clipper.hosted.procs import Procs

    from .test_hosted_proxy import fake_command

    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path / "root"))
    for name in ("CLIPPER_PUBLIC_URL", "SMTP_URL", "CLIPPER_ADMINS"):
        monkeypatch.delenv(name, raising=False)
    procs = Procs(fake_command(tmp_path), ready_timeout=15)
    with TestClient(create_app(procs), headers={"origin": "http://testserver"}, follow_redirects=False) as client:
        yield client
    procs.stop_all()


def test_the_gateway_runs_the_daily_sweeps_for_studios_that_are_not_running(tmp_path, monkeypatch):
    from clipper.hosted import data_dir, procs

    monkeypatch.setenv("CLIPPER_HOSTED_ROOT", str(tmp_path / "root"))
    script = ("import os, pathlib; d = pathlib.Path(os.environ['CLIPPER_DATA_DIR']); "
              "(d / 'swept').write_text(os.environ['CLIPPER_HOSTED'] + str(len(list(d.glob('swept*')))))")
    monkeypatch.setattr(procs, "DAILY", (["-c", script],))
    for user_id in (1, 2):
        data_dir(user_id).mkdir(parents=True)
    prepared = []
    p = procs.Procs(prepare=prepared.append)
    p.procs[2] = SimpleNamespace(alive=lambda: True)        # running: its own syncs and task do it
    assert asyncio.run(p.daily([1, 2, 3])) == 1             # 3 has no folder yet
    assert (data_dir(1) / "swept").read_text() == "10" and not (data_dir(2) / "swept").exists()
    assert prepared == [1]


def test_the_daily_sweep_modules_run(tmp_path):
    """The modules the gateway runs exist and run on an empty folder (hosted, so nothing local is touched)."""
    from clipper.hosted import procs

    env = {**os.environ, "CLIPPER_HOSTED": "1", "CLIPPER_DATA_DIR": str(tmp_path),
           "PYTHONPATH": str(Path(meter.__file__).parents[1])}
    for args in procs.DAILY:
        r = subprocess.run([sys.executable, *args], env=env, capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
