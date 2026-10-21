# Launch to-do: what only Marc can do

Written 2026-10-20 with the hosted-launch work. Everything here needs your name, your card, your identity or
your hands on a website, so no code can do it. Do it in this order: the later steps need what the earlier ones
give (a live address first, because three reviewers open your site).

Letters in brackets match the list in the launch brief. Text in `{{name}}` form is yours to fill in. Nothing in
this file or in the repo holds your email address: put it in the server's env and in the forms yourself.

| # | Step | Needs | Takes |
|---|------|-------|-------|
| 1 | Domain, DNS, contact address, server (h) | a card | an afternoon |
| 2 | AI accounts: Anthropic, Gemini, Groq (b, c, d) | a card | 1 hour |
| 3 | Stock library permissions (e) | email | send now, wait days |
| 4 | DMCA agent (f) | $6 and your address | 20 minutes |
| 5 | Paddle (g) | ID checks, the live site | 1 to 2 weeks of review |
| 6 | A lawyer reads the legal pages (i) | money | 1 to 2 weeks |
| 7 | TikTok, Meta and Google reviews (a) | the live site and a demo video | 1 to 6 weeks each |
| 8 | Closed beta, then paid sign-ups (j) | 5 to 10 clippers | 2 to 4 weeks |

Start 3, 5 and 7 early: they are waiting games. You can send the stock emails the same day the site is up.

## 0. Fill-ins in the public pages

The pages in `src/clipper/hosted/site/` use three placeholders the gateway fills: `{{contact_email}}`,
`{{public_url}}`, `{{operator_name}}`. Set them in the server env (`CLIPPER_CONTACT_EMAIL`, `CLIPPER_PUBLIC_URL`,
and the operator name setting of the gateway). Seven spots also say `[FILL: ...]` because the gateway cannot know
them: governing law and court (terms), your registered address (terms, privacy, DMCA), the DMCA agent's name
(DMCA), the hosting provider (privacy) and the email service (privacy). Find them with:

```
grep -rn "FILL" src/clipper/hosted/site/
```

Three promises in the privacy page must be true on the server, so set them up on purpose:

1. Server logs are deleted after 30 days.
2. Backups are overwritten within 30 days.
3. Stored YouTube data is deleted within 7 days of a disconnect and within 30 days of a revoked or failing
   token, and YouTube statistics are refreshed at least every 30 days (the YouTube API Services policy asks for
   this: section III.D.2 and III.E.4, [policy](https://developers.google.com/youtube/terms/developer-policies)).
   Ask for a check that the stats sync does this before the Google review.

**The old local-app pages.** `docs/site/privacy.html` still says `YOUR-EMAIL-HERE`. It is plain HTML with no
templating, so it is left as it is. It is the page behind Google's review of your own local app (it describes
the program on your computer, not the hosted site). Before you send it anywhere, replace the placeholder with
your contact address by hand, or retire it once the hosted pages are live and the Google consent screen points to
`<public url>/privacy` instead. `docs/site/terms.html` and `docs/site/index.html` have no placeholder.

## 1. A domain, DNS and the contact address (h)

1. Buy a domain (any registrar; Cloudflare Registrar or Namecheap are fine). Pick a name that does not contain
   TikTok, Instagram, YouTube or "clip" as the whole of the name: TikTok's review refuses app names that mention a
   social platform or describe what the app does ([TikTok app review rules](https://developers.tiktok.com/docs/en/app-review-guidelines)).
2. Make a contact address on that domain, for example `hello@yourdomain`, and a second one for copyright notices if
   you want them apart (`dmca@yourdomain`). Put the first one in `CLIPPER_CONTACT_EMAIL`. Use these addresses for
   every form below, so your personal one never goes into the repo or a public page.
3. Point DNS at the server (an A or AAAA record for the bare domain and for `www`). Use a host with HTTPS
   (Caddy in front of `python -m clipper.hosted`, or a platform that does HTTPS for you). Set `CLIPPER_PUBLIC_URL`
   to `https://yourdomain`.
4. Add the sender records for email (SPF, DKIM, DMARC) at your email provider so sign-in and reset emails arrive.
5. Check from a phone that `/`, `/terms`, `/privacy`, `/refunds` and `/dmca` open with no login. Paddle, TikTok and
   Google all open them without signing in.
6. Verify the domain in [Google Search Console](https://search.google.com/search-console) with the Google account
   that owns the Cloud project. Google requires it for the consent screen's authorized domains
   ([Google verification requirements](https://support.google.com/cloud/answer/13464321)).

## 2. AI accounts

### (b) Anthropic Console

Source: [Anthropic workspaces](https://platform.claude.com/docs/en/build-with-claude/workspaces).

1. Open the [Claude Console](https://platform.claude.com) and add a card under billing. Prepay a small amount.
2. Settings, Workspaces, Create workspace. Name it `clipper-site`. Spend limits cannot be set on the Default
   Workspace, so this has to be a new one.
3. Select the workspace, click Spend limits, and set a monthly cap you can afford to lose (start at $100) with
   alerts at 50% and 80%.
4. In the workspace, create an API key named `clipper-site`. Put it in the server env as `ANTHROPIC_API_KEY`
   and nowhere else: not in the repo, not in chat, not in a screenshot.
5. Do not reuse the key you use on your own computer. The site's key can be rotated without touching yours.
6. Commercial API terms say your inputs are yours and Anthropic may not train on customer content
   ([commercial terms](https://www.anthropic.com/legal/commercial-terms)). The "no training" line in the FAQ and privacy page relies on this.

### (c) Gemini, paid tier

Why: the free tier lets Google use what you send to improve its products and let humans read it, and it may not be
used for users in the EEA, Switzerland or the UK. Only the paid service may be used when your app is available
there ([Gemini API terms](https://ai.google.dev/gemini-api/terms)). Gemini watches parts of the user's video, so this is the one that matters.

1. In [Google AI Studio](https://aistudio.google.com), open the API keys page for a new project named `clipper-site`.
2. Click Set up billing in the Billing Tier column, pick or create a Cloud Billing account, add a card. The upgrade
   shows up within about 10 minutes ([billing docs](https://ai.google.dev/gemini-api/docs/billing)).
3. On the project's Spend page, set a Monthly spend cap (marked experimental by Google, so also do step 4).
4. In the [Cloud Billing console](https://console.cloud.google.com/billing), Budgets and alerts, make a budget for
   this project with alerts at 50%, 90% and 100%, sent to your contact address.
5. Create the key under the billed project. Put it in the server env as `GEMINI_API_KEY`. A key from a project
   without billing is still on the free terms, so check the tier says Paid before you put it on the server.

### (d) Groq, paid tier

Why: the free tier takes audio files up to 25 MB, the Developer tier up to 100 MB
([Groq speech to text](https://console.groq.com/docs/speech-to-text)). An hour of audio is bigger than 25 MB.

1. In the [Groq console](https://console.groq.com), Settings, Billing: move to the Developer plan and add a card.
2. Set a spend limit if the console offers one, and an alert.
3. Create an API key and put it in the server env as `GROQ_API_KEY`.
4. Read Groq's current data-use page once and confirm they do not train on API customers' audio. The privacy page
   says the providers do not train on it.
5. Check the account's rate limits (Settings, Limits). The Developer tier allowed 7,200 audio seconds an hour and
   28,800 a day for Whisper in October 2026, shared by every user: about 2 hours of footage an hour, 8 a day.
   Clipper sends 10-minute chunks and waits when Groq says slow down (D230), but a busy day needs more. Ask Groq
   for a raise before the beta opens.

## 3. Stock libraries: ask in writing (e)

Create searches Pexels, Pixabay and Coverr for footage and Freesound for sounds, and the finished video goes to a
paying user. Their public licences are written for people, not for a paid app that hands users finished videos, and
some limit what an app may store. Pexels asks API apps to show a prominent link back and to stay away from copying
its core service. Pixabay does not allow permanent hotlinking, asks for caching for 24 hours and attribution, and
its licence does not cover redistributing content on its own
([Pexels API](https://www.pexels.com/api/documentation/), [Pixabay API](https://pixabay.com/api/docs/)).
Freesound licences differ by file, so a paid app needs to restrict itself to the files whose licence allows it.
Ask each in writing and keep the replies. Send from your contact address, not a personal one.

### Pexels

> Subject: Permission request: paid app that uses the Pexels API
>
> Hello Pexels team,
>
> I run Clipper ({{public_url}}), a paid web app that helps people make short explainer videos. One part of it
> (Create) searches the Pexels API for stock video from a phrase taken from the user's script, picks a clip, and
> includes it inside a finished video that the user downloads and posts. Users pay a monthly subscription for the
> app. We show a link to Pexels and credit the creator where we list results, we do not host a library of your
> files, and we do not resell footage on its own.
>
> Could you confirm in writing that this use is allowed under the Pexels licence and API terms, and tell me
> whether you want specific attribution wording or a higher rate limit than the default? Happy to send a demo.
>
> Thank you,
> {{name}}
> {{operator_name}}

### Pixabay

> Subject: Permission request: paid app using the Pixabay API
>
> Hello Pixabay team,
>
> I run Clipper ({{public_url}}), a paid web app that helps people make short explainer videos. Create, one part of
> the app, searches the Pixabay API with a phrase from the user's script, downloads the chosen video to our server
> (no permanent hotlinking), uses it inside a finished video, and gives the user that video to download. We cache
> API requests for 24 hours and credit Pixabay where results are shown. Videos are not offered on their own.
>
> Could you confirm in writing that this is allowed for a paid subscription app, and tell me whether we need
> full API access for the larger video sizes? I can send a demo.
>
> Thank you,
> {{name}}
> {{operator_name}}

### Coverr

> Subject: Permission request: Coverr API in a paid app
>
> Hello Coverr team,
>
> I run Clipper ({{public_url}}), a paid web app that helps people make short explainer videos. One part (Create)
> searches the Coverr API for footage matching the user's script and includes the chosen clip inside a finished
> video that the user downloads and posts. We credit Coverr where results are shown, and we never offer your
> footage as a download on its own.
>
> Could you confirm in writing that a paid app may use the API this way, and say what attribution you want? I can
> send a demo.
>
> Thank you,
> {{name}}
> {{operator_name}}

### Freesound

> Subject: Using Freesound sounds in a paid app
>
> Hello Freesound team,
>
> I run Clipper ({{public_url}}), a paid web app that helps people make short explainer videos. One part (Create)
> can add a short sound hit to a video, taken through the Freesound API. We plan to use only sounds whose
> licence allows commercial use (CC0, or CC BY with credit given in the app and on the finished video's page), and
> we will store each sound's licence and author with it.
>
> Is this use fine under the Freesound API terms for an app that charges its users? Is there anything we should
> add, such as a commercial API agreement or an attribution format you prefer?
>
> Thank you,
> {{name}}
> {{operator_name}}

If one says no, switch that source off in the server config until you have a replacement. Do not launch Create with
a library that said no.

## 4. DMCA designated agent (f)

Without a registered agent you lose the DMCA safe harbor, and you host users' uploads. Source:
[Copyright Office DMCA directory](https://www.copyright.gov/dmca-directory/).

1. Go to the directory site and create an account (Login/Register). Paper forms are no longer accepted.
2. Register the service provider: the legal name of the operator, the website address `https://yourdomain`, and any
   other names the service is known by.
3. Register the designated agent: a name (you, or a company), a postal address (a PO box or a commercial mail box is
   accepted, so your home address does not have to be public), a phone number and the copyright email address.
   Use the same agent and address on `/dmca` (the `[FILL]` spots).
4. Pay the fee. It was $6 per filing when the electronic system started and law-firm notes still quote it;
   confirm on the Copyright Office's fee page before paying.
5. Diary the renewal: the designation expires after three years and has to be renewed or amended. The system sends
   reminders to the account's email.

## 5. Paddle (g)

Paddle is the merchant of record: it sells to your users, charges and pays tax, and pays you. Do sandbox first,
live later.

### Sign up and verify

1. Create a seller account at [paddle.com](https://www.paddle.com). Pick the entity type (sole proprietor or
   company), add your legal name and address, then the payout details. Their ID and business checks are the slow
   part.
2. Use the sandbox (a separate login and dashboard) for everything until the beta gates in step 8 pass. Sandbox
   needs no website approval.

### Website approval (live)

Paddle opens your site. Submit the domain under Settings, Website approval, and expect 5 to 7 business days if it
is reviewed by hand ([Paddle domain approval](https://www.paddle.com/help/start/account-verification/what-is-domain-approval)).
Checklist, all of it reachable from the page navigation with no login:

- [ ] The site is live on HTTPS.
- [ ] A clear description of the product (the landing page does this).
- [ ] The pricing, with plans and packs (the landing page `#pricing`, also `/pricing`).
- [ ] The key features (the landing page).
- [ ] Terms, Refund policy and Privacy policy linked in the footer of every page.
- [ ] Your legal name (or your brand name if you are a sole proprietor) is in the Terms. The Terms use
      `{{operator_name}}`, so set it to exactly the name on your Paddle account. A capital letter that differs can
      delay approval.
- [ ] The Terms say Paddle is the Merchant of Record (they do).
- [ ] The refund window is 14 days or more and the page does not say "no refunds" or "all sales are final". Paddle's
      sample policy language rejects those phrases ([Paddle seller policies](https://developer.paddle.com/embedded-billing-partner-skills/.well-known/skills/draft-seller-policies/SKILL.md)).
- [ ] Refund requests are pointed at paddle.net or the Paddle receipt (the refund page does this).
- [ ] The pages return 200 with no login.
- [ ] Generative-AI products need acceptable-use rules and an accuracy warning in the Terms (they have both).

**Decided:** the first 14 days after a plan's first payment are an unconditional refund, once per account.
Paddle's guidance warns about "unreasonable qualifiers", and the cost is small: the AI budget per account is capped.

### Products and prices to create

Each plan gets one product with two recurring prices; each pack gets one product with one one-time price. Use USD
and the SaaS tax category, and fill the Paddle price id (starts `pri_`) into the matching env var on the server.

| Product | Price | Billing | Env var |
|---|---|---|---|
| Starter | $15.00 | every 1 month | `PADDLE_PRICE_STARTER_MONTHLY` |
| Starter | $144.00 | every 1 year (= $12 a month) | `PADDLE_PRICE_STARTER_YEARLY` |
| Pro | $29.00 | every 1 month | `PADDLE_PRICE_PRO_MONTHLY` |
| Pro | $276.00 | every 1 year (= $23 a month) | `PADDLE_PRICE_PRO_YEARLY` |
| Agency | $79.00 | every 1 month | `PADDLE_PRICE_AGENCY_MONTHLY` |
| Agency | $756.00 | every 1 year (= $63 a month) | `PADDLE_PRICE_AGENCY_YEARLY` |
| Pack: one campaign (120 minutes) | $5.00 | one time | `PADDLE_PRICE_PACK_CAMPAIGN` |
| Pack: 300 minutes | $10.00 | one time | `PADDLE_PRICE_PACK_MINUTES` |
| Pack: 5 Shorts | $20 | one time | `PADDLE_PRICE_PACK_SHORTS` |

No trial on any price (the free plan is the trial). The sandbox and live dashboards have different ids, so keep
two env files and do not mix them.

### Keys and the webhook

- `PADDLE_API_KEY`: Paddle, My account, Settings, Authentication, API keys tab, New API key
  ([docs](https://developer.paddle.com/api-reference/about/api-keys)). A sandbox key starts `pdl_sdbx_`, a live
  one `pdl_live_`.
- `PADDLE_CLIENT_TOKEN`: the same Authentication page, Client-side tokens tab. It is the public token the
  checkout uses in the browser.
- `PADDLE_WEBHOOK_SECRET`: create a notification destination (Notifications under Developer tools in the
  dashboard), type webhook, URL `<public url>/paddle/webhook`, and tick exactly these events:
  `subscription.created`, `subscription.updated`, `subscription.canceled`, `subscription.past_due`,
  `transaction.completed`, `adjustment.created`, `adjustment.updated` (the last two tell Clipper a refund was
  given). Save, then open the destination and copy its secret key. Paddle's own docs do not name these menus in one
  place, and the dashboard moves them now and then: if a name differs, look for the same word.
- `PADDLE_ENV`: `sandbox` with the sandbox keys, `production` with the live ones. Left empty it means sandbox.
- Default payment link: Paddle, Checkout, Checkout configuration, "Default payment link": enter `<public url>/billing`
  and save, in the sandbox and again in live. Paddle sells nothing until one is set. In live the domain has to be
  approved first (above), and a subdomain such as `clip.example.com` needs its own approval.
- Customer portal: nothing to set. The "Manage or cancel" button on /billing opens Paddle's portal for that
  customer (card, invoices, cancel).
- Set all of them in the server env only. Never paste them in chat, commit them or log them. After changing the
  env, restart the gateway.

What Clipper does with each event: a new or changed subscription sets the plan and its period; a cancel the user
schedules keeps the paid plan to the period's end, then Free; a failed renewal keeps the plan 7 more days, then
Free unless the payment goes through; a pack adds its credits; an approved refund is noted on /admin ("Refund
given", one per account) and a full refund of a subscription also cancels it at once. An event for a price or an
account Clipper doesn't know is logged and answered 200 (otherwise Paddle retries it for two days).

### Test before you go live

1. In the sandbox, buy Starter with the sandbox test card from Paddle's docs.
2. Confirm the Billing page shows Starter, the meters show 300 minutes and 2 Shorts, and the receipt email arrives.
3. Use the whole allowance and confirm the work stops at zero before any AI is called.
4. Buy a pack and confirm it adds to the meter and does not expire.
5. Cancel with "Manage or cancel", and confirm the Billing page says when the plan ends and access lasts until then.
   (To see the end without waiting a month, cancel "immediately" from the sandbox dashboard instead.)
6. Refund the test order in full from the sandbox dashboard. Sandbox approves refunds every ten minutes; then
   confirm the plan drops back to Free and /admin shows "Refund given" for the account.
7. Subscribe to Agency, invite a second email from Billing, open the link in a private window, sign up with that
   email and join. Confirm the second login sees the same studio and no Billing or Delete.
8. Only then switch the env to live keys and live price ids, and do one real purchase of the $5 pack with your own
   card. Refund it.

## 6. A lawyer reads the legal pages (i)

The four legal pages are a draft, written for you to have a lawyer review (the first line of each file says so).
Hire a lawyer who knows online services in the country of your operator and ideally GDPR. Send them
`terms.html`, `privacy.html`, `refunds.html` and `dmca.html` and these questions:

> I run a paid web service that takes users' videos, sends parts of them to AI providers (Anthropic, Google
> Gemini, Groq) and gives users clips back. Payments go through Paddle as merchant of record. Please review the
> attached pages and tell me: (1) which entity should be named and where it should be registered; (2) the
> governing law and court clause; (3) whether the 14 day refund rule and the EU and UK withdrawal wording
> work with Paddle's rules; (4) whether the privacy page meets GDPR, UK GDPR and CCPA, and whether I need a
> data processing agreement, a representative in the EU or UK, or a records-of-processing document; (5) whether
> the liability limit and the indemnity are enforceable for consumers; (6) the retention periods; (7) whether the
> DMCA page and repeat infringer policy are enough for the safe harbor; (8) anything missing for the
> YouTube API Services, TikTok and Meta platform terms.

Do not launch paid sign-ups before they come back. The pages are also what the platform reviewers read.

## 7. The platform reviews (a)

File these once the site is live on its own domain and the legal pages are public: all three reviewers open your
site, and TikTok rejects a site that is only a landing page or login page, or one where the privacy and terms links
are hidden in a menu (the footer links satisfy "visible"). Keep the privacy page's text for each platform in step
with what you ask for.

**Use-case text (adapt per form):**

> Clipper ({{public_url}}) is a web app for people who make short vertical clips for paid clipping campaigns.
> Users upload long videos, Clipper cuts ranked clips, and the user posts them on their own accounts. After
> posting, the user connects their own TikTok, Instagram or YouTube account to Clipper so they can see how those
> posts performed. Clipper reads the account's name and the statistics of its videos (views, likes, comments and,
> where the platform provides them, insights such as watch time and shares), shows them to that same user inside
> their Clipper account, and matches each post to the clip it came from. Access is read-only. Clipper cannot post,
> edit or delete anything. The data is used only for that purpose, is shown only to the account owner, is not
> sold, shared or used for advertising, and is not used to train AI models. A user can disconnect an account at
> any time, which deletes the stored data and the sign-in token, and can delete their whole account.

**One 60-second demo video, cut to fit each platform.** Record the real hosted site on its real domain, in
English, signed in with a test account. Say nothing you cannot show.

| Seconds | On screen | Say |
|---|---|---|
| 0 to 8 | Browser at `https://yourdomain`, the landing page, then Log in. | "This is Clipper at yourdomain. I'm logging in with my account." |
| 8 to 20 | The Clips page with a few clips ready to post. | "Clipper makes clips from long videos for paid campaigns. I post them on my own accounts." |
| 20 to 30 | Accounts page, click Add an account for the platform. | "Now I connect my [TikTok, Instagram or YouTube] account to see how my posts perform." |
| 30 to 42 | The platform's own consent screen, full screen, with the scopes visible. Click Allow. | "The platform asks me to approve read-only access to [the scopes]. I approve." |
| 42 to 54 | Back in Clipper: the connected account, the video list with views, likes, comments, and a clip matched to its post. | "Clipper shows the account's name and my videos' statistics, and matches each post to the clip it came from." |
| 54 to 60 | Click Disconnect, then show the account gone. Open `/privacy` in a tab. | "I can disconnect at any time, which deletes the data. The privacy policy says how it is handled." |

Per platform:

- **TikTok** ([review rules](https://developers.tiktok.com/docs/en/app-review-guidelines)). Create a *Login Kit for
  web* app with the Display API, scopes `user.info.basic` and `video.list`, redirect `<public url>/callback/tiktok`
  (or the hosted redirect the connect service uses). Fill in: name (not "TikTok", not a description of what it does),
  a 1024-pixel icon, a description that says what it does, the website URL (the site's home), Privacy and Terms
  links, the scope explanations (use the use-case text), and the demo video. The video must show the real site, and
  its domain must match the website URL you gave. A first submission needs the sandbox demonstration done first.
  Up to five videos of 50 MB each. Remove any scope or product you do not use. Apps that are for private or personal
  use are refused, so word it as the multi-user app it is. Until approved, only listed sandbox users can connect.
- **Meta** ([Instagram app review](https://developers.facebook.com/docs/instagram-platform/app-review)). Create an app
  with Instagram API with Instagram login. Because Clipper serves users' own accounts rather than yours, you are a
  Tech Provider and need App Review for Advanced Access, and a Meta business that has completed Business Verification
  (connect it under App settings, Basic). Request `instagram_business_basic` and
  `instagram_business_manage_insights`. Provide the privacy policy URL, a 1024 by 1024 icon, a category, a
  business email, and a screencast for each permission showing login, the consent screen and the permission in use.
  Write test-user steps for the reviewer with a test login. Meta's review page lists the basic permission but not
  the insights one, so open the App Dashboard's App Review requests and read what it asks for before filming.
  Also set the Data Deletion Request URL to `<public url>/privacy#delete` and add the deauthorize callback the
  dashboard asks for. Only Business or Creator Instagram accounts work with this login.
- **Google** ([verification requirements](https://support.google.com/cloud/answer/13464321)). In Google Cloud,
  make a Web application OAuth client with the YouTube Data API and YouTube Analytics API enabled, set the consent
  screen to In production, and add `https://www.googleapis.com/auth/youtube.readonly` and
  `https://www.googleapis.com/auth/yt-analytics.readonly`. The authorized domain must be verified in Search Console
  (step 1). The homepage must say what the app does and link the privacy policy, the same link as on the consent
  screen, on the same domain. The video must show the full OAuth grant with the whole consent screen, in English, with
  the scopes visible, and then the features that use them. In the scope justification, say why the narrower scopes
  do not work: the first lists the channel's videos and their public counts, the second gives average view duration
  and shares. Note that the YouTube API Services privacy requirements are in the privacy page's YouTube section;
  re-read it against the form before you send. If the default quota (10,000 units a day) is not enough later, ask
  for an extension through Google's YouTube API Services audit form.

If a reviewer asks for something you cannot show, such as a platform that is not live yet, say so plainly in the
reply and keep that product or scope unchecked.

Do not enter your platform passwords in any tool or chat. Do the logins in the recording yourself.

## 8. The closed beta and the gates (j)

### Recruiting message (for clipping Discords)

Post it where the server rules allow it, or send it to the owner first.

> Hi all. I built a tool for clippers and I want 5 to 10 people to try it free for 2 weeks.
>
> You put in a long video (an upload, or a public Google Drive or Dropbox link) plus the campaign brief. It cuts
> ranked vertical clips, checks each one against the brief's rules, writes the captions, title and hashtags, and
> tells you why it picked each one. You can change the start, end, hook, captions and framing by hand.
>
> What I ask back: use it on at least 3 real campaign clips, post them the normal way, tell me what was wrong or
> confusing (a message or a 15 minute call is fine), and tell me honestly if you would pay for it and how much.
> If a clip gets approved and paid, tell me that too.
>
> It is free for the 2 weeks. No card. After that you can stop, or keep the free plan. Reply here or message me if
> you want in. {{name}}

Rules for the beta: one account each; tell people it is a beta and that the AI makes mistakes; give them a way to
reach you; do not promise earnings.

### Gates before paid sign-ups open

All of these must be true. Write down each result and the date.

1. **Cost per hour.** A real hour of footage, run through the live site, costs under $1.00 in AI calls (measured about $0.85, D236). Read it from
   the usage table (`usage.sqlite`) or the provider consoles.
2. **Cost per Short.** One Create Short with its 3 redraws costs under $3.50 in AI calls (measured about $2.20 before redraws, D236).
3. **Isolation.** A second account cannot see or spend the first account's files, clips, accounts or allowance. Try
   it by hand: other account's URLs, ids and files.
4. **Billing works.** A test card buys Starter in sandbox, and the meters stop at zero with no AI call after.
5. **The site is legally ready.** Terms, privacy, refunds and DMCA are live with the lawyer's changes in, and a
   backup has been restored once to a clean server to prove it works.
6. **People like it.** 3 beta clips were approved and paid by a campaign, and 3 users say they would pay.

Then: switch Paddle to live (step 5), turn on paid sign-ups, and raise the beta users' free allowance only if
you chose to.

## Sources checked

- YouTube API Services policy: https://developers.google.com/youtube/terms/developer-policies (III.A.1, III.A.2,
  III.D.2, III.E.4)
- Google OAuth verification: https://support.google.com/cloud/answer/13464321
- TikTok app review: https://developers.tiktok.com/docs/en/app-review-guidelines and
  https://developers.tiktok.com/docs/en/display-api-get-started
- Meta Instagram app review: https://developers.facebook.com/docs/instagram-platform/app-review
- Copyright Office DMCA directory: https://www.copyright.gov/dmca-directory/ (the $6 figure comes from law-firm
  notes, for example https://www.quarles.com/publications/new-online-system-under-the-digital-millennium-copyright-act-requires-website-operators-to-re-register-agent-designations-by-2018/ ; confirm on the site)
- Paddle domain approval: https://www.paddle.com/help/start/account-verification/what-is-domain-approval and
  https://www.paddle.com/help/start/account-verification/why-has-my-domain-been-rejected
- Paddle seller policies: https://developer.paddle.com/embedded-billing-partner-skills/.well-known/skills/draft-seller-policies/SKILL.md
- Paddle API keys: https://developer.paddle.com/api-reference/about/api-keys
- Anthropic workspaces: https://platform.claude.com/docs/en/build-with-claude/workspaces
- Gemini terms and billing: https://ai.google.dev/gemini-api/terms and https://ai.google.dev/gemini-api/docs/billing
- Groq speech to text: https://console.groq.com/docs/speech-to-text
- Pexels API: https://www.pexels.com/api/documentation/ ; Pixabay API: https://pixabay.com/api/docs/

Things I could not confirm from the sources and you should check on the day: the DMCA fee, whether the two YouTube
scopes are labelled sensitive in your Cloud console, Meta's exact screencast demand for the insights permission,
Coverr's and Freesound's API terms, Groq's training policy, and the Paddle dashboard menu names for notifications.
