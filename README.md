# clipper

Local, Windows-native CLI that turns one long-form video into a small set of
finished vertical clips (1080x1920 MP4, burned-in captions), ranked by predicted
performance, with a manifest explaining every pick.

> **Full setup, usage and troubleshooting docs land in Phase 7.**
> See [BUILD_BRIEF.md](BUILD_BRIEF.md) for scope and [PLAN.md](PLAN.md) for the
> concrete stack and status.

## Authorized sources only

Every campaign config must carry a `source_authorization` field naming the
campaign, URL, or explicit creator permission that covers the footage. The tool
refuses to run without it. Reposting material you are not authorized to use
risks copyright claims and account strikes.

clipper does **not** post anything. It has no upload, account, or
detection-evasion features, by design.

## Quick start

```powershell
uv venv --python 3.12 .venv
.\.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
clipper doctor
```
