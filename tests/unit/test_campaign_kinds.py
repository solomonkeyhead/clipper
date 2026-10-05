"""Campaigns that don't pay per view, don't take links, or aren't in English (D147)."""

from __future__ import annotations

from clipper import runner
from clipper.campaign import description
from clipper.config import CampaignConfig, Config
from clipper.llm.prompts import PROMPT_A, with_language
from clipper.studio import stats


def campaign(**kw) -> CampaignConfig:
    return CampaignConfig(name="c", source_authorization="authorized", **kw)


def test_each_pay_model_estimates_its_own_way():
    per_view = campaign(reward_per_1k_usd=2.0, min_payout_usd=1.0)
    assert stats.post_earnings(1000, per_view) == 2.0 and stats.post_earnings(100, per_view) == 0.0
    flat = campaign(pay_model="per_clip", flat_fee_usd=5.0)
    assert stats.post_earnings(0, flat) == 5.0 and stats.post_earnings(None, flat) == 5.0
    assert stats.post_earnings(9999, campaign(pay_model="none", reward_per_1k_usd=2.0)) is None
    assert stats.post_earnings(9999, campaign(own_channel=True, reward_per_1k_usd=2.0)) is None   # your own channel
    assert stats.post_earnings(100, None) is None


def test_which_campaigns_take_links():
    assert campaign().submits and not campaign(submit_links=False).submits and not campaign(own_channel=True).submits


def test_english_is_left_as_it_was_and_another_language_is_asked_for():
    assert with_language(PROMPT_A, "en") is PROMPT_A and with_language(PROMPT_A, "auto") is PROMPT_A
    es = with_language(PROMPT_A, "es")
    assert "Spanish" in es.system and es.key != PROMPT_A.key
    assert description._language_note(campaign()) == ""
    assert "German" in description._language_note(campaign(language="de"))


def test_a_spanish_campaign_transcribes_as_spanish_and_skips_english_caption_fixes():
    base = Config()
    spanish = runner.campaign_config(base, campaign(language="es"))
    assert spanish.transcription.language == "es" and spanish.llm.language == "es" and not spanish.llm.correct_captions
    english = runner.campaign_config(base, campaign())
    assert english.transcription.language == base.transcription.language and english.llm.correct_captions


def test_platforms_come_from_one_registry_and_the_page_has_the_same_list():
    from pathlib import Path

    from clipper import platforms
    from clipper.config import PLATFORMS
    from clipper.studio import posts

    assert PLATFORMS == platforms.TARGETS and "facebook_reels" in PLATFORMS
    assert posts.platform_of("https://fb.watch/abc") == "facebook" and posts.platform_of("https://youtu.be/x") == "youtube"
    generated = Path(__file__).parents[2] / "src" / "clipper" / "studio" / "web" / "src" / "api" / "platforms.gen.ts"
    assert generated.read_text(encoding="utf-8").replace("\r\n", "\n") == platforms.typescript()   # run `npm run gen:api` after changing it


def test_the_trash_and_clipboard_have_ways_on_a_mac_and_linux(tmp_path, monkeypatch):
    from clipper.utils import clipboard, recycle

    monkeypatch.setattr(recycle.sys, "platform", "linux")
    monkeypatch.setattr(recycle.shutil, "which", lambda name: None)       # no gio, no trash-put: by hand
    monkeypatch.setattr(recycle.Path, "home", classmethod(lambda cls: tmp_path))
    victim = tmp_path / "clip.mp4"
    victim.write_bytes(b"x")
    assert recycle.recycle(victim) and not victim.exists()
    kept = tmp_path / ".local" / "share" / "Trash"
    assert (kept / "files" / "clip.mp4").is_file() and "DeletionDate=" in (kept / "info" / "clip.mp4.trashinfo").read_text()

    said = {}
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name if name == "xclip" else None)
    monkeypatch.setattr(clipboard.subprocess, "run", lambda cmd, input, check: said.update(cmd=cmd, input=input))
    assert clipboard.copy_text("héllo") and said["cmd"][0] == "xclip" and said["input"] == "héllo".encode()
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    assert clipboard.copy_text("x") is False


def test_the_speech_model_follows_the_hardware():
    from clipper import hardware
    from clipper.hardware import Hardware

    assert hardware.recommend(Hardware(gpu="RTX 2070 SUPER", vram_gb=8.0, cpu_cores=16)).compute_type == "float16"
    small_gpu = hardware.recommend(Hardware(gpu="GTX 1650", vram_gb=4.0, cpu_cores=8))
    assert (small_gpu.model, small_gpu.compute_type) == ("large-v3", "int8_float16")
    laptop = hardware.recommend(Hardware(cpu_cores=4))
    assert (laptop.device, laptop.model) == ("cpu", "tiny") and laptop.minutes_per_hour > 3
    assert hardware.recommend(Hardware(cpu_cores=16)).model == "small"


def test_a_hosted_copy_wants_its_token_and_a_local_one_does_not(data_root, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    monkeypatch.setenv("CLIPPER_TOKEN", "s3cret")
    client = TestClient(server.create_app())
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    got = client.get("/?token=s3cret", follow_redirects=False)
    assert got.status_code == 303 and "clipper_token" in got.headers["set-cookie"]
    monkeypatch.delenv("CLIPPER_TOKEN")
    assert TestClient(server.create_app()).get("/api/status").status_code == 200


def test_who_does_what_is_saved_and_taken_back_out(data_root):
    from clipper.config import write_auto

    write_auto({"llm": {"job_providers": {"sketch": "claude_api", "script": "gemini"}}})
    write_auto({"transcription": {"model": "small"}})                      # another setting keeps these
    assert Config.load().llm.job_providers == {"sketch": "claude_api", "script": "gemini"}
    assert Config.load().transcription.model == "small"
    write_auto({"llm": {"job_providers": {"script": None}}})               # "Automatic" again
    assert Config.load().llm.job_providers == {"sketch": "claude_api"}
    (data_root / "config.yaml").write_text("transcription:\n  model: tiny\n", encoding="utf-8")
    assert Config.load().transcription.model == "tiny"                      # your own file wins


def test_a_chosen_provider_is_asked_first_and_the_usual_order_follows(monkeypatch):
    from types import SimpleNamespace

    from clipper.create import ai

    gem, claude = SimpleNamespace(name="gemini", describe=lambda: "gemini:g"), SimpleNamespace(name="claude_code", describe=lambda: "claude_code:c")
    monkeypatch.setattr(ai, "_default_backends", lambda config, model=None, job="": [claude, gem])
    import clipper.runner as runner

    monkeypatch.setattr(runner, "_correction_backends", lambda config, override: [gem])
    config = Config()
    assert [b.name for b in ai.backends(config, job="script")] == ["claude_code", "gemini"]
    picked = config.model_copy(update={"llm": config.llm.model_copy(update={"job_providers": {"script": "gemini"}})})
    assert [b.name for b in ai.backends(picked, job="script")] == ["gemini", "claude_code"]
    assert [b.name for b in ai.backends(picked, job="topics")] == ["claude_code", "gemini"]   # only that job


def test_a_taste_file_is_exported_without_clip_text_and_imported_as_the_starting_weights(data_root):
    from fastapi.testclient import TestClient

    from clipper.learn import feedback
    from clipper.studio import server

    client = TestClient(server.create_app())
    exported = client.get("/api/learning/taste").json()
    assert set(exported["weights"]) == set(feedback.RUBRIC) and "text" not in str(exported).lower().replace("context", "")
    assert client.put("/api/learning/taste", json={"weights": {"hook_strength": 0.5}}).status_code == 400
    mine = {k: 1 / len(feedback.RUBRIC) for k in feedback.RUBRIC} | {"hook_strength": 0.4, "payoff": 0.1}
    assert client.put("/api/learning/taste", json={"weights": mine}).status_code == 200
    assert runner._taste_seed() == mine
    start, seeded = runner._seeded(Config().llm.rubric_weights.as_dict())   # no ratings yet: it starts from the file
    assert seeded and start == mine
    client.delete("/api/learning/taste")
    assert runner._taste_seed() is None
