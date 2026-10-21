"""The holes the hosted-launch audit found, closed (D229): each in local mode as before where it should be."""

from __future__ import annotations

import http.server
import ipaddress
import threading
import zipfile

import pytest

from clipper.create import svgfilm
from clipper.studio import imports


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.delenv("CLIPPER_TOKEN", raising=False)


@pytest.fixture
def client(data_root, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    campaigns = tmp_path / "campaigns"
    campaigns.mkdir()
    monkeypatch.setattr(server, "campaigns_dir", lambda: campaigns)
    monkeypatch.delenv("CLIPPER_TOKEN", raising=False)
    return TestClient(server.create_app())


def _server(handler) -> tuple[http.server.HTTPServer, str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


# ---- 1. starting and the token

def test_hosted_without_a_token_refuses_to_start(monkeypatch, hosted):
    from clipper.studio import server

    monkeypatch.setattr(server, "_running", lambda port: pytest.fail("got past the check"))
    with pytest.raises(SystemExit, match="CLIPPER_TOKEN"):
        server.serve(open_browser=False)


def test_serve_checks_the_host_header_only_on_ones_own_computer(monkeypatch):
    import uvicorn

    from clipper.studio import server

    seen = []
    monkeypatch.setattr(server, "_running", lambda port: False)
    monkeypatch.setattr(server, "create_app", lambda **kw: seen.append(kw))
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.delenv("CLIPPER_HOST", raising=False)
    monkeypatch.delenv("CLIPPER_HOSTED", raising=False)
    server.serve(open_browser=False)
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.setenv("CLIPPER_TOKEN", "t" * 32)
    server.serve(open_browser=False)
    assert [kw["local_only"] for kw in seen] == [True, False]


def test_behind_the_gateway_only_the_bearer_header_counts(data_root, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.setenv("CLIPPER_TOKEN", "secret-token")
    c = TestClient(server.create_app())
    assert c.get("/api/code-version").status_code == 401
    assert c.get("/api/code-version?token=secret-token", follow_redirects=False).status_code == 401
    assert c.get("/api/code-version", cookies={"clipper_token": "secret-token"}).status_code == 401
    assert c.get("/api/code-version", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/code-version", headers={"Authorization": "Bearer secret-token"}).status_code == 200


def test_the_token_cookie_is_secure_over_https_and_lasts_a_week(data_root, monkeypatch):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    monkeypatch.delenv("CLIPPER_HOSTED", raising=False)
    monkeypatch.setenv("CLIPPER_TOKEN", "secret-token")
    c = TestClient(server.create_app())
    plain = c.get("/?token=secret-token", follow_redirects=False).headers["set-cookie"]
    proxied = c.get("/?token=secret-token", headers={"X-Forwarded-Proto": "https"},
                    follow_redirects=False).headers["set-cookie"]
    assert "Max-Age=604800" in plain and "secure" not in plain.lower()
    assert "secure" in proxied.lower()
    assert c.get("/api/code-version", cookies={"clipper_token": "secret-token"}).status_code == 200


# ---- 2. what only makes sense on one's own computer

def test_hosted_refuses_the_own_computer_endpoints(client, hosted):
    from clipper.studio import setup

    assert client.put("/api/setup/keys", json={"GEMINI_API_KEY": "x"}).status_code == 403
    with pytest.raises(PermissionError):
        setup.set_keys({"GEMINI_API_KEY": "x"})
    assert client.put("/api/ai-jobs", json={"choice": "gemini"}).status_code == 403
    assert client.post("/api/quit").status_code == 403
    assert client.post("/api/clips/1/reveal").status_code == 404
    assert client.post("/api/compute/apply").status_code == 403
    assert client.post("/api/setup/check").status_code == 403
    refused = client.put("/api/settings", json={"plan": "pro"})
    assert refused.status_code == 403 and "billing" in refused.json()["detail"]
    assert client.put("/api/settings", json={"sync_minutes": "30"}).status_code == 200


def test_ones_own_computer_still_sets_the_plan_and_the_ai(client):
    assert client.put("/api/settings", json={"plan": "free"}).json()["plan"] == "free"
    assert client.put("/api/ai-jobs", json={"choice": "gemini"}).json() == {"choice": "gemini"}


def test_hosted_lists_no_footage_from_the_servers_downloads_folder(data_root, hosted, monkeypatch, tmp_path):
    from clipper.studio import server

    monkeypatch.setattr(server.Path, "home", lambda: tmp_path)
    assert server.source_folders() == [data_root / "downloads"]
    monkeypatch.delenv("CLIPPER_HOSTED")
    assert server.source_folders()[1] == tmp_path / "Downloads"


def test_hosted_channel_settings_take_no_picture_paths(client, monkeypatch):
    slug = client.post("/api/create/channels", json={"name": "Lab", "pack": "physics"}).json()["slug"]
    assert client.put(f"/api/create/channels/{slug}", json={"character": "C:/me.png"}).status_code == 200
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    for body in ({"character": "C:/Windows/x.png"}, {"presenter": "/etc/x"}, {"watermark": "x.png"},
                 {"reactions": ["a.png"]}, {"poses": "happy = /x.png"}):
        assert client.put(f"/api/create/channels/{slug}", json=body).status_code == 403, body
    assert client.put(f"/api/create/channels/{slug}", json={"character": "", "niche": "physics"}).status_code == 200


# ---- 3. drawings filmed in a browser

@pytest.mark.parametrize("dirty", [
    "<svg><script>alert(1)</script></svg>",
    "<svg><scr<script>x</script>ipt>alert(1)</script></svg>",
    "<svg><SCRIPT src=x.js /></svg>",
    "<svg><foreignObject><iframe src='http://x'></iframe></foreignObject></svg>",
    "<svg><iframe src=//x></iframe><object data=x></object><embed src=x></svg>",
    "<svg onload=alert(1)></svg>",
    "<svg/onload=alert(1)></svg>",
    "<svg><rect x='1'onclick='a()'/></svg>",
    "<svg><rect title='a>b' onmouseover=\"a()\"/></svg>",
    "<svg><a href='javascript:alert(1)'>x</a></svg>",
    "<svg><a href=http://x>x</a></svg>",
    "<svg><use xlink:href='http://x/y.svg#a'/></svg>",
    "<svg><image href='file:///C:/secret.png'/></svg>",
    "<svg><set attributeName='href' to='java\tscript:alert(1)'/></svg>",
    "<svg><style>@import 'http://x/y.css'; a{background:url( 'http://x/p.png' )}</style></svg>",
])
def test_nothing_that_runs_or_loads_survives_cleaning(dirty):
    from clipper.create import svgfilm

    out = svgfilm.clean(dirty).lower()
    for bad in ("<scr", "onload", "onclick", "onmouseover", "javascript", "http", "file:",
                "<iframe", "<object", "<embed", "<foreignobject", "@import", "x.js"):
        assert bad not in out, (bad, out)


def test_cleaning_keeps_what_drawings_use():
    from clipper.create import svgfilm

    svg = ("<svg xmlns='http://www.w3.org/2000/svg'><defs><filter id='chalk'/></defs>"
           "<style>.a{filter:url(#chalk);mask:url('data:image/png;base64,AAA')}</style>"
           "<use href='#a'/><image href='data:image/png;base64,AAA'/><text>one = online</text></svg>")
    assert svgfilm.clean(svg) == svg


@pytest.fixture
def hits():
    """A local server that records every request it gets."""
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd, base = _server(Handler)
    yield seen, base
    httpd.shutdown()


@pytest.mark.skipif(not svgfilm.ready(), reason="no browser to film drawings in")   # asked before conftest's stand-in
def test_a_filmed_drawing_reaches_no_address_and_runs_no_script(monkeypatch, hits):
    seen, base = hits
    monkeypatch.setattr(svgfilm, "clean", lambda svg: svg)   # the browser's own guard, not the cleaning
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100'>"
           f"<style>@import url({base}/a.css);</style><image href='{base}/b.png' width='10' height='10'/>"
           f"<rect id='r' width='100' height='100' fill='#000'/>"
           f"<script>document.getElementById('r').setAttribute('fill', '#ff0000')</script></svg>")
    with svgfilm.film(svg, [0.0], 1.0, (0, 0, 0), 100, 100) as shoot:
        picture = shoot(0.5)
    assert seen == []
    assert picture.getpixel((50, 50)) == (0, 0, 0)


# ---- 4. link imports

def test_only_the_real_hosts_count_and_hosted_takes_only_drive_and_dropbox(monkeypatch):
    for bad in ("https://evildropbox.com/s/x/a.mp4", "https://dropbox.com.evil.net/s/x/a.mp4",
                "https://notdrive.google.com.evil.io/file/d/X/a.mp4"):
        assert imports.inspect(bad).kind == "direct"   # an ordinary link locally, never read as Drive or Dropbox
    assert imports.inspect("https://drive.google.com@evil.com/a.mp4").kind == "direct"
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    for bad in ("https://evildropbox.com/s/x/a.mp4", "https://cdn.example.com/eps/ep2.mov",
                "https://drive.google.com@evil.com/a.mp4"):
        with pytest.raises(imports.ImportError_, match="Drive and Dropbox"):
            imports.inspect(bad)
    assert imports.inspect("https://www.dropbox.com/scl/fi/xyz/a.mp4?dl=0").kind == "dropbox-file"
    assert imports.inspect("https://drive.google.com/file/d/ABC/view").kind == "drive-file"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.0.1", "169.254.169.254",
                                "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1",
                                "fc00::1", "::ffff:127.0.0.1", "ff02::1"])
def test_private_and_local_addresses_are_blocked(ip):
    assert imports._blocked(ipaddress.ip_address(ip))


def test_public_addresses_are_not():
    assert not imports._blocked(ipaddress.ip_address("142.250.80.46"))
    assert not imports._blocked(ipaddress.ip_address("2a00:1450:4001::200e"))


def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 443))])
    with pytest.raises(imports.ImportError_, match="private or local"):
        imports._open("https://cdn.example.com/a.mp4")


def test_a_redirect_to_a_private_address_is_refused(monkeypatch, hits):
    seen, base = hits

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"{base}/secret")
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd, start = _server(Redirect)
    try:
        # Only the first server is let through: the redirect's address (also this computer) must be checked again.
        port = httpd.server_port
        real = imports._public_connection

        def first_only(address, *a, **k):
            if address[1] == port:
                import socket

                return socket.create_connection(address, *a)
            return real(address, *a, **k)

        monkeypatch.setattr(imports, "_public_connection", first_only)
        with pytest.raises(imports.ImportError_, match="private or local"):
            imports._open(f"{start}/a.mp4")
        assert seen == []
    finally:
        httpd.shutdown()


def test_an_unreachable_site_says_nothing_about_its_ports(monkeypatch):
    monkeypatch.setattr(imports, "_blocked", lambda ip: False)
    with pytest.raises(imports.ImportError_) as refused:
        imports._open("http://127.0.0.1:1/a.mp4")   # nothing listens on port 1
    assert str(refused.value) == "Couldn't reach that site."


# ---- 5. upload limits

def test_the_cap_is_none_locally_8_gb_hosted_or_what_is_set(monkeypatch):
    monkeypatch.delenv("CLIPPER_MAX_UPLOAD_GB", raising=False)
    monkeypatch.delenv("CLIPPER_HOSTED", raising=False)
    monkeypatch.delenv("CLIPPER_HOST", raising=False)
    assert imports.upload_cap() is None
    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    assert imports.upload_cap() == 8 * 1024**3
    monkeypatch.setenv("CLIPPER_MAX_UPLOAD_GB", "2")
    assert imports.upload_cap() == 2 * 1024**3


def test_an_upload_over_the_cap_is_refused_and_leaves_nothing(client, data_root, monkeypatch):
    monkeypatch.setenv("CLIPPER_MAX_UPLOAD_GB", str(1000 / 1024**3))   # 1000 bytes
    refused = client.put("/api/uploads/big.mp4", content=b"x" * 2000)
    assert refused.status_code == 413
    assert list((data_root / "downloads").iterdir()) == []


def test_an_upload_with_a_taken_name_gets_a_new_one(client, data_root):
    first = client.put("/api/uploads/ep.mp4", content=b"one").json()
    second = client.put("/api/uploads/ep.mp4", content=b"two").json()
    assert (first["name"], second["name"]) == ("ep.mp4", "ep (2).mp4")
    assert (data_root / "downloads" / "ep.mp4").read_bytes() == b"one"


def test_a_cut_off_upload_leaves_no_part_file(tmp_path):
    import asyncio

    class Request:
        def __init__(self):
            self.headers = {"content-length": "6"}

        async def stream(self):
            yield b"abc"
            raise ConnectionError("the browser went away")

    with pytest.raises(ConnectionError):
        asyncio.run(imports.receive(Request(), tmp_path / "v.mp3", None))
    assert list(tmp_path.iterdir()) == []


def test_a_voice_is_at_most_200_mb():
    from clipper.studio import create_api

    assert create_api.VOICE_MAX_BYTES == 200 * 1024**2


def test_a_zip_too_big_unpacks_nothing(data_root, monkeypatch, tmp_path):
    archive = tmp_path / "f.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a.mp4", b"x" * 600)
        z.writestr("b.mp4", b"x" * 600)
    runner = imports.ImportRunner(lambda *a: None)
    (data_root / "downloads").mkdir(exist_ok=True)
    monkeypatch.setenv("CLIPPER_MAX_UPLOAD_GB", str(500 / 1024**3))    # a member over the cap
    with pytest.raises(imports.ImportError_, match="too big"):
        runner._unzip(archive)
    monkeypatch.setenv("CLIPPER_MAX_UPLOAD_GB", str(1000 / 1024**3))   # members fit; all of it under 4x
    assert runner._unzip(archive) == ["a.mp4", "b.mp4"]
    monkeypatch.setenv("CLIPPER_MAX_UPLOAD_GB", str(290 / 1024**3))
    with zipfile.ZipFile(archive, "w") as z:
        for k in range(5):
            z.writestr(f"{k}.mp4", b"x" * 250)                         # each fits, 1250 > 4 x 290
    with pytest.raises(imports.ImportError_, match="too big"):
        runner._unzip(archive)
    assert not list((data_root / "downloads").glob("*.part"))


# ---- 6. ffmpeg and ffprobe

def test_inputs_are_files_and_pipes_only_and_lavfi_is_left_alone():
    from clipper.render import ffmpeg

    cmd = ffmpeg.safe_inputs(["ffmpeg", "-ss", "1", "-i", "a.mp4", "-f", "lavfi", "-i", "color=black", "out.mp4"])
    assert cmd == ["ffmpeg", "-ss", "1", "-protocol_whitelist", "file,pipe", "-i", "a.mp4",
                   "-f", "lavfi", "-i", "color=black", "out.mp4"]
    assert ffmpeg.safe_inputs(cmd) == cmd


def test_a_run_gets_ten_times_its_media_and_at_least_ten_minutes(monkeypatch, tmp_path):
    from clipper.render import ffmpeg

    long = tmp_path / "long.mp4"
    long.write_bytes(b"x")
    monkeypatch.setattr(ffmpeg, "_seconds", lambda path, stamp: 3600.0)
    assert ffmpeg.media_timeout(["ffmpeg", "-i", str(long), "o.mp4"]) == 36000
    assert ffmpeg.media_timeout(["ffmpeg", "-i", str(tmp_path / "none.mp4"), "o.mp4"]) == 600


def test_ffmpeg_and_ffprobe_get_the_whitelist_and_a_timeout(monkeypatch, tmp_path):
    import subprocess

    from clipper.ingest import probe
    from clipper.render import ffmpeg

    calls = []

    def fake(cmd, **kw):
        calls.append((cmd, kw["timeout"]))
        if kw["timeout"] == 600 and "-show_format" not in cmd:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return subprocess.CompletedProcess(cmd, 0, b"{}", b"")

    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(ffmpeg, "ffmpeg_path", lambda: tmp_path / "ffmpeg")
    monkeypatch.setattr(ffmpeg, "ffprobe_path", lambda: tmp_path / "ffprobe")
    monkeypatch.setattr(probe, "ffprobe_path", lambda: tmp_path / "ffprobe")
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    probe.probe_raw(media)
    assert calls[-1][0][1:3] == ["-protocol_whitelist", "file,pipe"] and calls[-1][1] == 600
    with pytest.raises(ffmpeg.FFmpegError, match="stopped"):
        ffmpeg.run(["-f", "lavfi", "-i", "color", "out.mp4"])
    with pytest.raises(subprocess.TimeoutExpired):   # a caller's own limit is the caller's (the NVENC probe)
        ffmpeg.run(["-f", "lavfi", "-i", "color", "out.mp4"], timeout=600)


# ---- 7, 8, 9: updating, deleting, no scraping

def test_hosted_never_pulls_or_restarts(monkeypatch):
    from clipper import selfupdate

    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.delenv("CLIPPER_NO_UPDATE", raising=False)
    monkeypatch.setattr(selfupdate, "_git", lambda *a, **k: pytest.fail("git was run"))
    assert selfupdate.pull() == ""


def test_hosted_deletes_for_real(monkeypatch, tmp_path):
    from clipper.utils import recycle

    monkeypatch.setenv("CLIPPER_HOSTED", "1")
    monkeypatch.setattr(recycle, "_trash_elsewhere", lambda p: pytest.fail("sent to a trash"))
    one = tmp_path / "a.mp4"
    one.write_bytes(b"x")
    folder = tmp_path / "work"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "b.png").write_bytes(b"x")
    assert recycle.recycle(one) and recycle.recycle(folder)
    assert not one.exists() and not folder.exists()


def test_the_tiktok_studio_reader_is_gone():
    from typer.testing import CliRunner

    from clipper.cli import app

    result = CliRunner().invoke(app, ["tiktok", "collect", "--file", "x.txt"])
    assert result.exit_code != 0
    with pytest.raises(ImportError):
        import clipper.tiktok.studio  # noqa: F401


# ---- 10. host header, campaign names, nosniff

def test_the_host_must_name_this_computer():
    from clipper.studio.server import local_host

    for ok in ("127.0.0.1", "127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765", "[::1]"):
        assert local_host(ok), ok
    for bad in ("evil.com", "evil.com:8765", "127.0.0.1.evil.com", "localhost.evil.com:80", "", "[::2]:8765"):
        assert not local_host(bad), bad


def test_on_ones_own_computer_another_host_is_refused_and_every_answer_says_nosniff(data_root):
    from fastapi.testclient import TestClient

    from clipper.studio import server

    c = TestClient(server.create_app(local_only=True))
    refused = c.get("/api/code-version")   # the test client calls itself "testserver"
    assert refused.status_code == 403 and refused.headers["x-content-type-options"] == "nosniff"
    ok = c.get("/api/code-version", headers={"Host": "127.0.0.1:8765"})
    assert ok.status_code == 200 and ok.headers["x-content-type-options"] == "nosniff"


def test_a_campaign_name_cant_reach_outside_its_folder(tmp_path):
    from clipper.campaign import editor

    folder = tmp_path / "campaigns"
    folder.mkdir()
    (tmp_path / "secret.yaml").write_text("name: other\n", encoding="utf-8")
    (folder / "chad-s2.yaml").write_text("name: chad-s2\n", encoding="utf-8")
    assert editor.path_for(folder, "../secret") is None
    assert editor.path_for(folder, "chad-s2") == folder / "chad-s2.yaml"
