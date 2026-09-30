# Campaign watcher

Pushes new clipping campaigns that fit you to your phone (ntfy), judged by
Gemini against the profile in `config/default.yaml` (`watch:`).

Vyro's and Whop's terms forbid scraping their sites, so the watcher never
visits them. It reads the campaign emails Vyro sends, which your own iCloud
rule forwards into a Gmail account used only for this. Whop sends no
new-campaign emails (its notification settings have no such option), so Whop
is not covered: check contentrewards.com/discover → New Campaigns by hand.

> **Being replaced.** Campaign alerts now come from Whop feeds (Content
> Rewards posts every new campaign there, D69) and Discord channels you follow
> (D65), both set up at Campaigns > Find campaigns > Set up alerts. This email
> watcher still covers Vyro's emails until Vyro is followed in Discord; then it
> goes. The never-installed Google Apps Script version was removed on 2026-09-30.

## Setup

1. A Gmail used only for this, e.g. `solomonkeyclips.alerts@gmail.com`.
2. iCloud Mail (icloud.com/mail) → gear → Settings → Rules: *is from*
   `vyro.com` → *Forward to* that Gmail. (Plain forward, so Vyro mail still
   shows in iCloud.)
3. ntfy app on the phone, subscribed to the topic in `NTFY_TOPIC`.

## PC version

`clipper watch --test-push` sends a test push; `clipper watch --dry-run`
judges new mail and prints the verdicts without pushing or remembering.
Task Scheduler runs `pythonw -m clipper.watch` every 10 minutes (task
"Clipper campaign watch"), windowless, logging to `data/logs/watch.log`.

- Turn off: `schtasks /Change /TN "Clipper campaign watch" /DISABLE`
- Turn on: `schtasks /Change /TN "Clipper campaign watch" /ENABLE`

The venv's base Python lives in `.python/` (git-ignored), not in
`%APPDATA%\uv`: Python installed from inside the Claude desktop app lands in
that app's private copy of AppData, which Task Scheduler cannot see (the task
failed with exit code 103, "No Python at ...").
