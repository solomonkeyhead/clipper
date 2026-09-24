# Campaign watcher

Pushes new clipping campaigns that fit you to your phone (ntfy), judged by
Gemini against the profile in `config/default.yaml` (`watch:`).

Vyro's and Whop's terms forbid scraping their sites, so the watcher never
visits them. It reads the campaign emails Vyro sends, which your own iCloud
rule forwards into a Gmail account used only for this. Whop sends no
new-campaign emails (its notification settings have no such option), so Whop
is not covered: check contentrewards.com/discover → New Campaigns by hand.

There are two versions. Run **one** of them, or every campaign is pushed twice.

| | Cloud (Google Apps Script) | PC (Task Scheduler) |
|---|---|---|
| Runs when the PC is off | yes | no |
| Where | `tools/apps_script/campaign_watch.gs` | `src/clipper/watch/` |
| Mailbox access | the script runs inside the Gmail account | IMAP + app password in `.env` |
| Secrets | Script Properties | `.env` |
| Logs | Apps Script → Executions | `data/logs/watch.log` |

## Shared setup

1. A Gmail used only for this, e.g. `solomonkeyclips.alerts@gmail.com`.
2. iCloud Mail (icloud.com/mail) → gear → Settings → Rules: *is from*
   `vyro.com` → *Forward to* that Gmail. (Plain forward, so Vyro mail still
   shows in iCloud.)
3. ntfy app on the phone, subscribed to the topic in `NTFY_TOPIC`.

## Cloud version (recommended)

1. Signed in to Google **as the watcher Gmail**, open script.google.com →
   **New project**. Rename it "Clipper campaign watcher".
2. Replace everything in `Code.gs` with the contents of
   `tools/apps_script/campaign_watch.gs`. Save (Ctrl+S).
3. Gear icon (**Project Settings**) → **Script Properties** → add:
   - `GEMINI_API_KEY` — the same key as in `.env`
   - `NTFY_TOPIC` — the same topic as in `.env`
4. Back in the editor, pick `testPush` in the function menu and click **Run**.
   Google asks you to authorise the script (read Gmail, connect to external
   services). As the owner of your own script you will see "Google hasn't
   verified this app": click **Advanced → Go to Clipper campaign watcher**.
   A push should arrive on the phone.
5. Run `dryRun`. **View → Logs** (or Executions) lists each recent email and
   what it would do. Nothing is pushed or remembered.
6. Run `install`. `watch` now runs every 10 minutes on Google's servers.
7. Turn off the PC version (below) so you don't get double pushes.

To stop it: run `uninstall`. To change what counts as a fit, edit the
constants at the top of the script (keep them in step with `config/default.yaml`).

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
