# Sign in with nothing but a login (D151)

Goal (Marc, 2026-10-05): a user logs into their account and it is connected, with no app of their own to
create. TikTok, Instagram and Google only share stats with a registered app, and an app's secret can't ship
inside a program anyone can open, so the app lives on a small service that Marc runs. The user's Clipper never
sees a secret.

```
Clipper (user's PC) -> connect service -> TikTok / Instagram / Google login page
       ^                    |  swaps the code for tokens with the app's secret
       +--- the browser posts the tokens to 127.0.0.1 ---+
```

The service keeps nothing. Code: `src/clipper/broker/` (the service), `src/clipper/connect.py` (Clipper's side).
Where it is set, every "Add an account" button is one click; a user's own app keys still win.

## What Marc has to do (nothing here can be done from code)

1. **Host it.** Anywhere with HTTPS; Render's free plan works (it sleeps when idle, so the first login after a quiet spell is slow). Others: Cloud Run free tier, Railway, Fly.io: `python -m clipper.broker`, port 8080.
   Set `PUBLIC_URL` (its address), `BROKER_SIGNING_KEY` (a long random string) and the keys below.
2. **Register one app per platform**, each with the redirect `PUBLIC_URL/callback/<platform>`:
   - TikTok: a *Login Kit for web* app with the Display API, scopes `user.info.basic`, `video.list`.
     `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`.
   - Google: a *Web application* OAuth client with the YouTube Data and Analytics APIs. `YOUTUBE_CLIENT_ID`,
     `YOUTUBE_CLIENT_SECRET`. Set the consent screen to *In production* (Testing ends a login after 7 days).
   - Meta: an app with *Instagram API with Instagram login*. `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`.
3. **Pass each platform's review.** This is the real gate. Until it is passed only people listed as testers
   (TikTok sandbox target users, Meta Instagram testers, Google's 100 test users) can log in; the rest see an
   error like `non_sandbox_target`. TikTok needs a demo video, a privacy policy and terms page (`docs/site`
   has the pages written for Google); Meta needs App Review for `instagram_business_basic` and
   `instagram_business_manage_insights`; Google needs OAuth verification for the YouTube scopes.
4. **Give Clipper the address.** `CLIPPER_BROKER_URL=https://...` in `.env`, or one line in
   `src/clipper/broker.url` (git-ignored) in the copy you hand out.

## Not covered
- **X** has no login in this: its stats are read with a developer token billed per post read, so a shared
  token would put every user's reads on Marc's bill. Users keep their own token and type a username.
- **Hosted Clipper** (`CLIPPER_HOST`): the tokens post back with a cross-site form, which the access cookie's
  SameSite=strict refuses. Only the local Clipper is covered.
- Trust: the service sees each login's tokens for a moment. Say so in the privacy page.
