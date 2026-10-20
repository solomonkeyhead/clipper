# Deploying the hosted site

One Linux server runs everything: Clipper's gateway (sign-in, billing, one Clipper process per
user) and Caddy, which gets the HTTPS certificate on its own. Everything is in `Dockerfile` and
`deploy/`. Written with D-NEW.

## 1. The server

CPU only at launch, because transcription goes through Groq. Start with 8 vCPU, 16 GB RAM and
a disk of 200 GB or more (footage is 1 to 5 GB a source and clips are kept 7 to 180 days).
A Hetzner cloud server of that size works. Pick Debian 12 or Ubuntu 24.04. A GPU only makes sense
if transcription moves back onto the server.

## 2. Install Docker

```
curl -fsSL https://get.docker.com | sh
```

Open ports 80 and 443 only (ports 80 and 443/udp too if you use a firewall: `ufw allow 80,443/tcp`
and `ufw allow 443/udp`). Keep SSH on keys.

## 3. DNS

Add an A record (and AAAA if the server has IPv6) for your domain pointing at the server. Wait until
`ping your-domain` shows the server's address; Caddy cannot get a certificate before that.

## 4. Get the code and fill in the settings

```
git clone <the repo> /opt/clipper && cd /opt/clipper
git checkout <a release tag>
cd deploy
cp env.example .env
nano .env
mkdir -p /srv/clipper-backups && chown 1000:1000 /srv/clipper-backups
```

`env.example` lists every variable with a note. The ones you need on day one: `CLIPPER_DOMAIN`,
`CLIPPER_PUBLIC_URL`, `CLIPPER_ADMINS`, `CLIPPER_CONTACT_EMAIL`, `SMTP_URL` (so password resets
arrive), the AI keys, and the Paddle block. Use `PADDLE_ENV=sandbox` until a test purchase and a
test cancel both work, then switch to `production` with the production keys and prices.

Optional: set `SENTRY_DSN` for error reports. It only works if `sentry-sdk` is installed in the
image (not included); without it the gateway ignores the setting.

## 5. Start it

```
docker compose up -d --build
docker compose ps
curl https://your-domain/healthz
```

The first build takes several minutes (ffmpeg, Chromium, fonts). Then sign up on the site with an
email listed in `CLIPPER_ADMINS`: that account gets `/admin`. Sign up as a normal user too and run
one clip end to end before telling anyone.

## 6. Backups and the disk check

Put these in root's crontab (`crontab -e`). The backup runs in the app container and writes to the
host folder; the disk check runs on the volume.

```
17 3 * * * cd /opt/clipper/deploy && docker compose exec -T app python scripts/ops/backup.py || echo "clipper backup failed" | mail -s "clipper backup" you@example.com
*/30 * * * * cd /opt/clipper/deploy && docker compose exec -T app python scripts/ops/check_disk.py --to you@example.com
```

The backup keeps 14 days of `gateway.sqlite`, every user's databases, `usage.sqlite` and `plan.json`
in `/srv/clipper-backups/<date>/`. It does not copy footage or clips: those are rebuilt or
re-uploaded. A backup on the same server is not safe from the server dying. Copy the folder
off the machine each night (`rclone`, `rsync` to another host, or a Hetzner snapshot) as well.

A systemd timer works the same way: run the same command from a `.service` and schedule it in a `.timer`.

The disk check exits 1 and emails when under 15% is free. Make room by shortening retention or
growing the disk.

## 7. Updating

Only deploy a tag whose GitHub Actions `test` run is green (`.github/workflows/test.yml` runs the
unit tests and ruff on every push and pull request). There is no auto-deploy: you do this by hand.

```
cd /opt/clipper && git fetch --tags && git checkout <new tag>
cd deploy && docker compose up -d --build
```

Signed-in users' processes restart on their next request. Run a backup first if the release changes
databases.

## 8. Restoring

```
cd /opt/clipper/deploy
docker compose stop app
docker compose run --rm --no-deps app python scripts/ops/backup.py --restore 2026-10-20
docker compose up -d
```

It copies that day's files over the live ones and clears old SQLite journal files. It refuses to run
while the app answers `/healthz`; `--force` overrides that, only use it if you are sure nothing is
writing. To test a backup, restore into a scratch folder: `--root /tmp/restore-test`.

## 9. Logs

```
docker compose logs -f app       # Clipper and each user's process
docker compose logs -f caddy     # requests and certificate problems
docker compose ps                # health
```

Docker keeps logs without a limit by default. Add to `/etc/docker/daemon.json`
`{"log-driver":"json-file","log-opts":{"max-size":"50m","max-file":"5"}}` and restart Docker.

## 10. The first week

- Free disk space, every morning (`df -h /var/lib/docker`). Footage fills it faster than expected.
- The AI cost per user against the plan price: `/admin` and the `ai_calls` table in each `usage.sqlite`.
  The per-plan budget is a safety net; check that no one sits at it.
- Paddle: the webhook log shows 200s. A failed webhook means a paying user stays on Free.
- Backups: look at the date folders and restore one into a scratch folder once.
- Memory: `docker stats`. One process per active user stays up for 20 idle minutes; 16 GB should hold
  a few dozen at once. If it swaps, move to a bigger server.
- Sign-in emails reach inboxes (send a reset to a Gmail and an iCloud address).
- Upload of a multi-GB file through the domain, start to finish.
