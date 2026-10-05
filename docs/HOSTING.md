# Running Clipper: on someone's computer, or on a server

Marc's goal (2026-10-05): "eventually I want it to run on someone's computer OR have all computing done
on a server." This is where each stands. Written with D148.

## On someone's computer (works today)

`clipper studio` runs the Control Center on 127.0.0.1:8765, and everything happens on that machine.

- **Any hardware.** Settings > This computer reads the graphics card and CPU (`hardware.py`) and picks
  the speech-recognition model and precision that fit: large-v3 on a GPU with 8 GB, the same compressed
  on 4 GB, `medium` on less, and `small`/`base`/`tiny` on CPU alone. The choice is written to
  `<data>/config.auto.yaml`; a hand-edited `config.yaml` always wins. Rough speed: an hour of footage is
  about 3 minutes on a mid-range GPU and 10 to 20 on a CPU.
- **Any OS.** The Recycle Bin, clipboard and "show in folder" have Mac and Linux versions. Still Windows
  first: the desktop icon, `selfupdate` restart and the `.cmd` shim for Claude Code are tested there only.
- **Nothing of the owner inside.** Campaigns live in `<data>/campaigns`, personal settings in
  `<data>/config.yaml`, the first-run question decides which parts show (D145).

## On a server (the groundwork is done; two pieces are not)

```
CLIPPER_HOST=0.0.0.0 CLIPPER_TOKEN=<a long secret> clipper studio --no-browser
```

- `CLIPPER_HOST` other than this computer makes it reachable from other machines. It then **refuses to
  start without `CLIPPER_TOKEN`**. Open it once as `https://your-server/?token=<secret>`; the browser keeps
  a cookie. Anything else gets a 401. Put it behind HTTPS (a reverse proxy such as Caddy): the token is
  a password.
- `status.hosted` is true; the page hides what only makes sense on your own computer ("Show in folder").
  Downloading a clip is a normal HTTP download, uploads are already how footage gets in.
- `CLIPPER_DATA_DIR` moves all data (library, campaigns, caches) to the server's disk.

What is **not** done, in the order I would do it:

1. **One user per server.** There are no accounts: the token is the whole of the login, and everything
   is one person's library. Several people means a user table, a data folder per user, and the token
   becoming a sign-in. The `plan` setting (free / research / pro) is the start of per-user limits.
2. **Platform sign-ins from a server.** TikTok, YouTube, Instagram and X connect through a local
   `127.0.0.1` redirect. On a server each needs a public redirect URL registered with the platform
   (`https://your-server/callback`), and the TikTok and Instagram apps go through the platforms' own
   production review before strangers' accounts can connect (sandbox: your own accounts only).
3. **Doing the heavy work elsewhere.** "All computing on a server" means the transcription and rendering
   run on the server's GPU (or CPU) and the user's computer is only a browser. That is already what
   running Clipper on the server is: nothing runs on the viewer's side. Splitting the work further (a
   web server in one place, GPU workers in another) would put `jobs.py`'s queue on a shared store and
   the footage on shared storage. Not needed until one server's GPU is the bottleneck.

Costs to plan for on a server: GPU hours (or a slow CPU), disk for footage (a source is 1 to 5 GB and its
audio and clips are kept), and the AI keys, which are the owner's to pay (`llm.paid_api_jobs` limits the
paid Claude key to drawing).
