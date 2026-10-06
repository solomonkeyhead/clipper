import { Link } from "@tanstack/react-router";
import {
  AlertTriangle, CheckCircle2, ExternalLink, Eye, KeyRound, Loader2, Monitor, Moon, Plus, Stethoscope, Sun, XCircle,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  connectInstagram, connectX, instagramConnectState, runSystemCheck, startInstagramConnect, startTikTokConnect, startYouTubeConnect, testAI, tiktokConnectState, youtubeConnectState, useAccounts, useDisconnect,
  useSetKeys, useSetSettings, useSettings, useSetup, type Account, type CheckResult,
} from "@/api/client";
import { Field, TextInput } from "@/components/form";
import { GroupsCard } from "@/components/groups";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, CopyButton, PageHeader, Skeleton, Switch, Tip } from "@/components/ui";
import { notifyOn, notifySupported, setNotify } from "@/lib/notify";
import { useUI, type Theme } from "@/lib/store";
import { cn, formatCount, openTab } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

/* ---------- small pieces ---------- */

const Ext = ({ href, children }: { href: string; children: ReactNode }) => (
  <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-accent hover:underline">
    {children}<ExternalLink className="size-3" />
  </a>
);

/** One titled group of numbered steps in a setup guide. */
function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-sm font-medium">{title}</div>
      <Steps>{children}</Steps>
    </div>
  );
}

/** Copy one of the web pages Clipper wrote for a Google app (docs/site). */
function SiteCopy({ page }: { page: string }) {
  const copy = async () => {
    const text = await (await fetch(`/api/setup/site/${page}`)).text();
    await navigator.clipboard.writeText(text);
    toast.success(`${page} copied`, { description: "Paste it into GitHub's file editor." });
  };
  return (
    <button type="button" onClick={() => void copy()}
            className="ml-1 inline-flex h-6 items-center gap-1 rounded-sm border border-line bg-surface-2 px-2 text-xs font-medium text-fg hover:bg-surface-3">
      Copy page
    </button>
  );
}

function Steps({ children }: { children: ReactNode }) {
  return <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-sm text-muted marker:text-subtle [&_b]:text-fg">{children}</ol>;
}

/** A secret field: typed into, never shown back. */
function SecretInput({ id, value, onChange, placeholder, isSet }: {
  id?: string; value: string; onChange: (v: string) => void; placeholder?: string; isSet?: boolean;
}) {
  return (
    <TextInput id={id} type="password" autoComplete="off" spellCheck={false} value={value}
               onChange={(e) => onChange(e.target.value)}
               placeholder={isSet ? "Saved. Paste a new one to replace it" : placeholder} />
  );
}

/* ---------- Accounts ---------- */

function AccountRow({ account }: { account: Account }) {
  const disconnect = useDisconnect();
  const setScope = useUI((s) => s.setAccountScope);
  const [confirm, setConfirm] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface-2 px-3 py-2.5">
      <span className={cn("flex items-center gap-1.5 text-sm font-medium",
        account.health === "ok" ? "text-success" : account.health === "warn" ? "text-warning" : "text-danger")}>
        {account.health === "ok" ? <CheckCircle2 className="size-4" /> : account.health === "warn"
          ? <AlertTriangle className="size-4" /> : <XCircle className="size-4" />}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold">{account.handle ? `@${account.handle}` : "Connected account"}</div>
        <div className="text-xs text-muted">
          {account.detail}
          {account.expires_in_days != null && account.connected && ` Login good for ${Math.floor(account.expires_in_days)} more days.`}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">{account.posts} post{account.posts === 1 ? "" : "s"} · {formatCount(account.views)} views</span>
          {account.groups.map((g) => <span key={g} className="rounded-full border border-line px-2 py-0.5 text-subtle">{g}</span>)}
        </div>
      </div>
      {account.health !== "ok" && account.platform === "tiktok" && <AddTikTok label="Reconnect" />}
      {account.health !== "ok" && account.platform === "instagram" && <AddInstagram label="Reconnect" />}
      {account.health !== "ok" && account.platform === "youtube" && <AddYouTube label="Reconnect" />}
      <Tip label="Show only this account across Clipper">
        <Button size="icon" variant="ghost" aria-label="View this account" onClick={() => {
          setScope(`account:${account.key}`);
          toast(`Viewing @${account.handle}`, { description: "Switch back with the button in the top bar." });
        }}><Eye className="size-4" /></Button>
      </Tip>
      {confirm ? (
        <span className="flex items-center gap-1.5 text-xs">
          <span className="text-muted">Stop syncing this account?</span>
          <Button size="sm" variant="danger" onClick={() => disconnect.mutate({ platform: account.platform, id: account.id },
            { onSuccess: () => toast("Disconnected", { description: "Its posts stay in your stats; they just stop updating." }) })}>
            Disconnect
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
        </span>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>Disconnect</Button>
      )}
    </div>
  );
}

function TikTokAppKeys({ onSaved }: { onSaved?: () => void }) {
  const { data: setup } = useSetup();
  const save = useSetKeys();
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  return (
    <div className="flex flex-col gap-4 rounded-md border border-dashed border-line p-4">
      <div>
        <div className="text-sm font-semibold">One-time setup: a free TikTok developer app</div>
        <p className="mt-0.5 text-sm text-muted">TikTok only shares your posts' stats with an app you register. It takes about 5 minutes, once.</p>
      </div>
      <Steps>
        <li>Sign in at <Ext href="https://developers.tiktok.com/apps">developers.tiktok.com</Ext> and press <b>Connect an app</b> (any name, e.g. "my clip stats").</li>
        <li>Add the products <b>Login Kit</b> (choose <b>Desktop</b>) and <b>Display API</b>, with the scopes <b>user.info.basic</b> and <b>video.list</b>.</li>
        <li>Set the redirect URI to <code className="rounded bg-surface-3 px-1">http://localhost:3455/callback/</code> <CopyButton text="http://localhost:3455/callback/" what="Redirect URI" variant="ghost" /></li>
        <li>Open <b>Sandbox</b>, and under <b>Target users</b> add each TikTok account you'll connect (up to 10).</li>
        <li>Copy the app's <b>Client key</b> and <b>Client secret</b> here.</li>
      </Steps>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Client key">{(id) => <SecretInput id={id} value={key} onChange={setKey} isSet={setup?.keys.TIKTOK_CLIENT_KEY} />}</Field>
        <Field label="Client secret">{(id) => <SecretInput id={id} value={secret} onChange={setSecret} isSet={setup?.keys.TIKTOK_CLIENT_SECRET} />}</Field>
      </div>
      <div className="flex justify-end">
        <Button variant="primary" disabled={!key.trim() || !secret.trim() || save.isPending}
          onClick={() => save.mutate({ TIKTOK_CLIENT_KEY: key, TIKTOK_CLIENT_SECRET: secret }, {
            onSuccess: () => { setKey(""); setSecret(""); toast.success("TikTok app keys saved"); onSaved?.(); },
            onError: (e) => toast.error((e as Error).message),
          })}>
          <KeyRound className="size-4" /> Save keys
        </Button>
      </div>
    </div>
  );
}

type ConnectState = { state: string; message: string; url: string };

/** Connect one more account through a platform's own consent page, opened in a new tab. One click
 *  once the platform's app keys are saved (D150): the button itself starts the sign-in. */
function AddAccount({ platform, name, label, ready, appKeys, start, state, waitingHint, trouble }: {
  platform: string; name: string; label: string; ready: boolean; appKeys: ReactNode;
  start: () => Promise<ConnectState>; state: () => Promise<ConnectState>; waitingHint: string; trouble?: string;
}) {
  const qc = useQueryClient();
  const [needKeys, setNeedKeys] = useState(false);
  const [waiting, setWaiting] = useState<{ url: string; since: number } | null>(null);
  const [, tick] = useState(0);
  const poll = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearInterval(poll.current), []);

  const connect = async () => {
    try {
      const res = await start();
      if (res.state === "failed") throw new Error(res.message);
      setWaiting({ url: res.url, since: Date.now() });
      openTab(res.url);
      window.clearInterval(poll.current);
      poll.current = window.setInterval(async () => {
        tick((n) => n + 1);
        const now = await state();
        if (now.state === "done" || now.state === "failed") {
          window.clearInterval(poll.current);
          setWaiting(null);
          void qc.invalidateQueries({ queryKey: ["accounts"] });
          if (now.state === "done") toast.success(`Connected ${now.message}`, { description: "Its posts' stats arrive on the next sync." });
          else toast.error(now.message);
        }
      }, 2000);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (waiting) {
    return (
      <div className="flex flex-col gap-3 rounded-md border border-dashed border-line p-4">
        <div className="flex items-center gap-2 text-sm font-medium"><Loader2 className="size-4 animate-spin text-accent" /> Waiting for you to approve in the {name} tab…</div>
        <p className="text-sm text-muted">{waitingHint}</p>
        {trouble && Date.now() - waiting.since > 25_000 && <p className="rounded-md bg-warning/10 p-2.5 text-sm text-warning">{trouble}</p>}
        <div className="flex gap-2">
          <a href={waiting.url} target="_blank" rel="noopener noreferrer"
             className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-2.5 text-xs font-medium hover:bg-surface-3">
            <ExternalLink className="size-3.5" /> Open {name} again
          </a>
          <CopyButton text={waiting.url} what="Link" label="Copy link" />
        </div>
      </div>
    );
  }
  if (needKeys && !ready) return <>{appKeys}</>;
  return (
    <Button variant={ready ? "primary" : "secondary"} onClick={() => (ready ? void connect() : setNeedKeys(true))}>
      <PlatformIcon platform={platform} className="size-4" /> {ready ? label : `${label} (one-time setup)`}
    </Button>
  );
}

function AddTikTok({ label = "Add a TikTok account" }: { label?: string }) {
  const { data: setup } = useSetup();
  return (
    <AddAccount platform="tiktok" name="TikTok" label={label} ready={!!setup?.tiktok_app}
      appKeys={<TikTokAppKeys />} start={startTikTokConnect} state={tiktokConnectState}
      waitingHint="TikTok connects whichever account is logged in on tiktok.com in that browser. For a different account, log into it there first, or copy this link into another browser."
      trouble={`Seeing "non_sandbox_target" or "We couldn't log in with TikTok"? Your TikTok app is still in Sandbox: open it at developers.tiktok.com, go to Sandbox → Target users, add this TikTok account, then try again.`} />
  );
}

function YouTubeAppKeys() {
  const { data: setup } = useSetup();
  const save = useSetKeys();
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  return (
    <div className="flex flex-col gap-4 rounded-md border border-dashed border-line p-4">
      <div>
        <div className="text-sm font-semibold">One-time setup: a free Google Cloud app</div>
        <p className="mt-0.5 text-sm text-muted">
          YouTube only shares your Shorts' stats with an app you register. About 20 minutes, once, in your browser; no card
          needed. Do the parts in order, and check the project picker at the top of Google Cloud says <b>Clipper</b> each time.
        </p>
      </div>

      <Part title="1. Create the project">
        <li>Open <Ext href="https://console.cloud.google.com/projectcreate">New project</Ext>. <b>Project name:</b> Clipper. Press <b>Create</b>.</li>
        <li>Wait for the bell notification "Create Project: Clipper", then pick <b>Clipper</b> in the project picker at the top.</li>
      </Part>

      <Part title="2. Turn on the two YouTube APIs">
        <li>Open <Ext href="https://console.cloud.google.com/apis/library/youtube.googleapis.com">YouTube Data API v3</Ext> and press <b>Enable</b>.</li>
        <li>Open <Ext href="https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com">YouTube Analytics API</Ext> and press <b>Enable</b>.</li>
      </Part>

      <Part title="3. Set up the sign-in screen">
        <li>Open <Ext href="https://console.cloud.google.com/auth/overview">Google Auth Platform</Ext> and press <b>Get started</b>.</li>
        <li><b>App information:</b> App name <b>Clipper</b>, User support email = your Gmail. <b>Next</b>.</li>
        <li><b>Audience:</b> choose <b>External</b>. <b>Next</b>.</li>
        <li><b>Contact information:</b> your email. <b>Next</b>.</li>
        <li><b>Finish:</b> tick that you agree to the Google API Services User Data Policy, press <b>Continue</b>, then <b>Create</b>.</li>
      </Part>

      <Part title="4. Make the app's two web pages (GitHub Pages, free)">
        <li>Google needs a public homepage and privacy policy before it lets the app stay signed in. Clipper has written both.</li>
        <li>Make a free account at <Ext href="https://github.com/signup">github.com</Ext>. Your username goes in the web address below.</li>
        <li>Open <Ext href="https://github.com/new">a new repository</Ext>. <b>Name:</b> exactly <code className="rounded bg-surface-3 px-1">YOUR-USERNAME.github.io</code>, <b>Public</b>, tick <b>Add a README file</b>, press <b>Create repository</b>.</li>
        <li>Press <b>Add file → Create new file</b>. Name it <b>privacy.html</b>, paste the page <SiteCopy page="privacy.html" />, replace both <b>YOUR-EMAIL-HERE</b> with the email you want shown, then <b>Commit changes</b> twice.</li>
        <li>Again for <b>index.html</b> <SiteCopy page="index.html" /> and <b>terms.html</b> <SiteCopy page="terms.html" /> (TikTok asks for terms; already have the site? Open each file, press the pencil, and paste over it).</li>
        <li>After a minute, check <code className="rounded bg-surface-3 px-1">https://YOUR-USERNAME.github.io/privacy.html</code> opens.</li>
      </Part>

      <Part title="5. Branding, and publish">
        <li>Open <Ext href="https://console.cloud.google.com/auth/branding">Branding</Ext>. <b>Authorized domains:</b> press <b>Add domain</b> and enter <code className="rounded bg-surface-3 px-1">YOUR-USERNAME.github.io</code>.</li>
        <li><b>Application home page:</b> <code className="rounded bg-surface-3 px-1">https://YOUR-USERNAME.github.io/</code>. <b>Application privacy policy link:</b> <code className="rounded bg-surface-3 px-1">https://YOUR-USERNAME.github.io/privacy.html</code>. Leave the logo empty (a logo sends the app to Google's review). <b>Save</b>.</li>
        <li>Open <Ext href="https://console.cloud.google.com/auth/audience">Audience</Ext>, press <b>Publish app</b>, then <b>Confirm</b>. It should say <b>In production</b>. (Left in Testing, Google signs you out every 7 days.)</li>
      </Part>

      <Part title="6. The keys">
        <li>Open <Ext href="https://console.cloud.google.com/auth/clients">Clients</Ext>, press <b>Create client</b>. <b>Application type:</b> Desktop app. <b>Name:</b> Clipper. <b>Create</b>.</li>
        <li>Copy the <b>Client ID</b> and <b>Client secret</b> from the box that opens into the two fields below, and press <b>Save keys</b>.</li>
      </Part>

      <p className="text-xs text-muted">
        When you connect, Google says it hasn't verified the app. It's your own app: press <b>Advanced → Go to Clipper</b>, then pick
        the <b>channel you post Shorts on</b> (a Brand Account channel is listed on its own).
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Client ID">{(fid) => <SecretInput id={fid} value={id} onChange={setId} isSet={setup?.keys.YOUTUBE_CLIENT_ID} />}</Field>
        <Field label="Client secret">{(fid) => <SecretInput id={fid} value={secret} onChange={setSecret} isSet={setup?.keys.YOUTUBE_CLIENT_SECRET} />}</Field>
      </div>
      <div className="flex justify-end">
        <Button variant="primary" disabled={!id.trim() || !secret.trim() || save.isPending}
          onClick={() => save.mutate({ YOUTUBE_CLIENT_ID: id, YOUTUBE_CLIENT_SECRET: secret }, {
            onSuccess: () => { setId(""); setSecret(""); toast.success("Google app saved"); },
            onError: (e) => toast.error((e as Error).message),
          })}>
          <KeyRound className="size-4" /> Save keys
        </Button>
      </div>
    </div>
  );
}

function AddYouTube({ label = "Add a YouTube channel" }: { label?: string }) {
  const { data: setup } = useSetup();
  return (
    <AddAccount platform="youtube" name="YouTube" label={label} ready={!!setup?.youtube_app}
      appKeys={<YouTubeAppKeys />} start={startYouTubeConnect} state={youtubeConnectState}
      waitingHint="Choose the channel itself, not only your Google account. To add another channel later, connect again and pick that one." />
  );
}

/** One-time: the Meta app's ID and secret, after which "Add an Instagram account" is one click (D150). */
function InstagramAppKeys({ onToken }: { onToken: () => void }) {
  const { data: setup } = useSetup();
  const save = useSetKeys();
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const redirect = "http://localhost:3458/callback/";
  return (
    <div className="flex flex-col gap-4 rounded-md border border-dashed border-line p-4">
      <div>
        <div className="text-sm font-semibold">One-time setup: a free Meta app</div>
        <p className="mt-0.5 text-sm text-muted">Instagram only shares Reel stats with an app you register. Do this once and every account after is one click.</p>
      </div>
      <Steps>
        <li>At <Ext href="https://developers.facebook.com/apps">developers.facebook.com</Ext>, create an app (type <b>Business</b>) and add the <b>Instagram</b> product.</li>
        <li>Under <b>Instagram → API setup with Instagram business login</b>, add this to <b>Valid OAuth redirect URIs</b>: <code className="rounded bg-surface-3 px-1">{redirect}</code> <CopyButton text={redirect} what="Redirect URI" variant="ghost" /></li>
        <li>Copy the <b>Instagram app ID</b> and <b>Instagram app secret</b> here. Other accounts than your own need <b>App roles → Instagram Tester</b>, accepted in Instagram.</li>
      </Steps>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Instagram app ID">{(fid) => <SecretInput id={fid} value={id} onChange={setId} isSet={setup?.keys.INSTAGRAM_APP_ID} />}</Field>
        <Field label="Instagram app secret">{(fid) => <SecretInput id={fid} value={secret} onChange={setSecret} isSet={setup?.keys.INSTAGRAM_APP_SECRET} />}</Field>
      </div>
      <div className="flex items-center justify-between gap-2">
        <Button variant="ghost" onClick={onToken}>Paste a token instead</Button>
        <Button variant="primary" disabled={!id.trim() || !secret.trim() || save.isPending}
          onClick={() => save.mutate({ INSTAGRAM_APP_ID: id, INSTAGRAM_APP_SECRET: secret }, {
            onSuccess: () => { setId(""); setSecret(""); toast.success("Meta app saved", { description: "Now \"Add an Instagram account\" is one click." }); },
            onError: (e) => toast.error((e as Error).message),
          })}>
          <KeyRound className="size-4" /> Save
        </Button>
      </div>
    </div>
  );
}

function AddInstagram({ label = "Add an Instagram account" }: { label?: string }) {
  const { data: setup } = useSetup();
  const [manual, setManual] = useState(false);
  if (manual) return <InstagramToken onBack={() => setManual(false)} />;
  return (
    <AddAccount platform="instagram" name="Instagram" label={label} ready={!!setup?.instagram_app}
      appKeys={<InstagramAppKeys onToken={() => setManual(true)} />} start={startInstagramConnect} state={instagramConnectState}
      waitingHint="Instagram connects the account you log in as. It must be a professional (Creator or Business) account." />
  );
}

function InstagramToken({ onBack }: { onBack: () => void }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(true);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const connect = async () => {
    setBusy(true);
    try {
      const res = await connectInstagram(token);
      setToken("");
      setOpen(false);
      await qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(`Connected @${res.username}`, { description: "Its Reels' stats arrive on the next sync." });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!open) {
    return <Button variant="secondary" onClick={() => setOpen(true)}><Plus className="size-4" /> Add an Instagram account</Button>;
  }
  return (
    <div className="flex flex-col gap-4 rounded-md border border-dashed border-line p-4">
      <Steps>
        <li>The account must be <b>professional</b> (Creator or Business): Instagram app → Settings → Account type and tools.</li>
        <li>At <Ext href="https://developers.facebook.com/apps">developers.facebook.com</Ext>, create an app (type <b>Business</b>) and add the <b>Instagram</b> product.</li>
        <li>For every account other than the app owner's: <b>App roles → Roles → Add people → Instagram Tester</b>, then accept the invite in Instagram (Settings → Website permissions → Apps and websites → Tester invites).</li>
        <li>Open <b>Instagram → API setup with Instagram business login</b>, press <b>Add account</b>, log in as that account, then <b>Generate token</b> with <b>instagram_business_basic</b> and <b>instagram_business_manage_insights</b>.</li>
        <li>Paste the token here. Clipper renews it by itself from then on.</li>
      </Steps>
      <Field label="Access token">{(id) => <SecretInput id={id} value={token} onChange={setToken} placeholder="IGAA…" />}</Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button variant="primary" disabled={token.trim().length < 20 || busy} onClick={() => void connect()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <PlatformIcon platform="instagram" className="size-4" />} Connect
        </Button>
      </div>
    </div>
  );
}

/** X: the app's Bearer Token and the account's username (D83). Paid per post read. */
function AddX() {
  const qc = useQueryClient();
  const { data: setup } = useSetup();
  const setKeys = useSetKeys();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const { data: known } = useAccounts();
  const [typed, setUsername] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const haveToken = Boolean(setup?.keys.X_BEARER_TOKEN);
  // Most people post under one name everywhere: start from a handle already connected (D150).
  const username = typed ?? known?.find((a) => a.platform !== "x" && a.handle)?.handle ?? "";
  const connect = async () => {
    setBusy(true);
    try {
      if (token.trim()) await setKeys.mutateAsync({ X_BEARER_TOKEN: token.trim() });
      const res = await connectX(username);
      setToken("");
      setUsername(null);
      setOpen(false);
      await qc.invalidateQueries({ queryKey: ["accounts"] });
      toast.success(`Connected @${res.username}`, { description: "Its posts and their views arrive within the hour." });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!open) {
    return <Button variant="secondary" onClick={() => setOpen(true)}><Plus className="size-4" /> Add an X account</Button>;
  }
  if (haveToken) {   // the app is set up: the username is the whole sign-in
    return (
      <div className="flex flex-wrap items-end gap-2 rounded-md border border-dashed border-line p-4">
        <div className="min-w-48 flex-1">
          <Field label="Your X username">
            {(id) => <TextInput id={id} value={username} placeholder="@solomonkeyclips" onChange={(e) => setUsername(e.target.value)}
                                onKeyDown={(e) => { if (e.key === "Enter" && username.trim() && !busy) void connect(); }} />}
          </Field>
        </div>
        <Button variant="primary" disabled={busy || !username.trim()} onClick={() => void connect()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <PlatformIcon platform="x" className="size-4" />} Connect
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4 rounded-md border border-dashed border-line p-4">
      <p className="text-sm text-muted">
        X charges for its API: <b>$0.005 per post read</b>, each post at most once a day. Clipper only reads posts up to two weeks
        old, so one clip a day costs about <b>$2 a month</b>. About 5 minutes, once.
      </p>
      <Steps>
        <li>Open <Ext href="https://console.x.com">console.x.com</Ext> and sign in with the X account you post clips on. Accept the Developer Agreement.</li>
        <li>Press <b>New App</b>. <b>Name:</b> Clipper. <b>Description:</b> Reads my own posts' view counts. Create it.</li>
        <li>On the screen that opens, copy the <b>Bearer Token</b> (it's shown once; you can regenerate it later under the app's <b>Keys and tokens</b>).</li>
        <li>Press <b>View pricing &amp; purchase credits</b> and buy credits yourself ($5 lasts months). X asks for a verified phone number.</li>
        <li>Paste the token and your username below, then <b>Connect</b>.</li>
      </Steps>
      <Field label="Bearer Token" hint={haveToken ? "Saved already. Paste a new one only to replace it." : undefined}>
        {(id) => <SecretInput id={id} value={token} onChange={setToken} placeholder="AAAAAAAAAAAAAAAAAAAAA…" isSet={setup?.keys.X_BEARER_TOKEN} />}
      </Field>
      <Field label="Your X username">
        {(id) => <TextInput id={id} value={username} placeholder="@solomonkeyclips" onChange={(e) => setUsername(e.target.value)} />}
      </Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
        <Button variant="primary" disabled={busy || !username.trim() || (!haveToken && token.trim().length < 30)} onClick={() => void connect()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <PlatformIcon platform="x" className="size-4" />} Connect
        </Button>
      </div>
    </div>
  );
}

function PlatformCard({ platform, title, accounts, add }: {
  platform: string; title: string; accounts: Account[]; add: ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2.5 text-md font-semibold">
          <PlatformIcon platform={platform} className="size-5" /> {title}
        </span>
        <span className="text-xs text-muted">{accounts.length ? `${accounts.length} connected` : "Not connected"}</span>
      </div>
      {accounts.map((a) => <AccountRow key={a.id} account={a} />)}
      <div>{add}</div>
    </Card>
  );
}

export function AccountsPage() {
  const { data, isLoading } = useAccounts();
  const list = data ?? [];
  return (
    <div className="fade-in max-w-3xl">
      <PageHeader title="Accounts"
        subtitle="Connect the accounts you post from. Clipper reads each post's views to track earnings, find your posts' links, and learn what works. With several accounts, group the ones that post together." />
      {isLoading ? <Skeleton className="h-40" /> : (
        <div className="flex flex-col gap-4">
          <GroupsCard accounts={list} />
          <PlatformCard platform="tiktok" title="TikTok" accounts={list.filter((a) => a.platform === "tiktok")} add={<AddTikTok />} />
          <PlatformCard platform="instagram" title="Instagram" accounts={list.filter((a) => a.platform === "instagram")} add={<AddInstagram />} />
          <PlatformCard platform="youtube" title="YouTube Shorts" accounts={list.filter((a) => a.platform === "youtube")} add={<AddYouTube />} />
          <PlatformCard platform="x" title="X" accounts={list.filter((a) => a.platform === "x")} add={<AddX />} />
          <p className="text-xs text-subtle">
            Clipper only reads stats through each platform's official API. Logins are stored on this PC only, in Clipper's data folder.
          </p>
          <p className="text-xs text-subtle">
            Registering an app with TikTok, Google or Meta needs a privacy policy and terms page online. Copy the ready-made ones for your
            GitHub Pages site: privacy <SiteCopy page="privacy.html" /> terms <SiteCopy page="terms.html" /> home <SiteCopy page="index.html" />
          </p>
        </div>
      )}
    </div>
  );
}

/* ---------- Settings ---------- */

function Row({ title, body, control, id }: { title: string; body: ReactNode; control: ReactNode; id?: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 py-4" id={id}>
      <div className="max-w-xl">
        <div className="text-sm font-medium">{title}</div>
        <div className="mt-1 text-sm text-muted">{body}</div>
      </div>
      <div className="shrink-0 pt-0.5">{control}</div>
    </div>
  );
}

const Where = ({ to, children }: { to: string; children: ReactNode }) => (
  <> <Link to={to} className="font-medium text-accent hover:underline">{children}</Link></>
);

/** Browser notifications when a clipping job or a Short's build finishes in the background (D143). */
function NotifySwitch() {
  const [on, setOn] = useState(notifyOn());
  if (!notifySupported()) return <span className="text-xs text-muted">Not supported by this browser</span>;
  return (
    <Switch label="Tell me when it's done" checked={on}
            onChange={(v) => void setNotify(v).then((now) => {
              setOn(now);
              if (v && !now) toast.error("Notifications are blocked", { description: "Allow them for this page in your browser's site settings, then try again." });
            })} />
  );
}

const USES_ROWS: [string, string, string][] = [
  ["use_campaigns", "Clip footage for campaigns", "New clips, campaign pages and the post and submit steps."],
  ["use_create", "Make original videos", "Create, for your own channel."],
  ["use_finder", "Find campaigns for me", "Campaign alerts and search on the Campaigns page."],
];

/** What you use Clipper for: the parts you don't use are hidden (D145). */
function Uses() {
  const { data: settings } = useSettings();
  const save = useSetSettings();
  if (!settings) return <Skeleton className="h-20" />;
  const values = settings as Record<string, string>;
  return (
    <div className="flex flex-col gap-2.5">
      {USES_ROWS.map(([key, title, body]) => (
        <label key={key} className="flex items-start gap-3">
          <Switch label={title} checked={values[key] !== "0"} onChange={(v) => save.mutate({ [key]: v ? "1" : "0" })} />
          <span><span className="text-sm font-medium">{title}</span><span className="block text-xs text-muted">{body}</span></span>
        </label>
      ))}
    </div>
  );
}

interface Compute {
  hardware: { gpu: string; vram_gb: number; cpu_cores: number; ram_gb: number; system: string };
  recommended: { device: string; model: string; compute_type: string; label: string; minutes_per_hour: number };
  current: { device: string; model: string; compute_type: string };
  matches: boolean;
}

/** What this computer is, and the speech-recognition model that suits it (D148). */
function Computer() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["compute"], queryFn: async () => {
    const res = await fetch("/api/compute");
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<Compute>;
  } });
  if (!data) return <Skeleton className="h-16" />;
  const { hardware: hw, recommended: rec, current: now } = data;
  const apply = async () => {
    const res = await fetch("/api/compute/apply", { method: "POST" });
    if (res.ok) {
      toast.success("Settings updated", { description: "The next video you clip uses them." });
      void qc.invalidateQueries({ queryKey: ["compute"] });
    } else toast.error("Couldn't save the settings");
  };
  return (
    <div className="flex flex-col gap-2 text-sm">
      <div>{hw.gpu ? `${hw.gpu}, ${hw.vram_gb} GB` : "No NVIDIA graphics card found"} · {hw.cpu_cores} CPU cores{hw.ram_gb ? ` · ${Math.round(hw.ram_gb)} GB memory` : ""}</div>
      <div className="text-muted">Speech recognition now: <b className="text-fg">{now.model}</b> ({now.compute_type}, {now.device}).</div>
      <div className="text-muted">Best for this computer: <b className="text-fg">{rec.model}</b> ({rec.compute_type}, {rec.device}). {rec.label}.
        An hour of footage takes about {rec.minutes_per_hour} minutes to read, roughly.</div>
      {data.matches
        ? <div className="text-success">Already set up this way.</div>
        : <Button variant="primary" size="sm" className="self-start" onClick={() => void apply()}>Use the best settings for this computer</Button>}
    </div>
  );
}

interface AIJobs {
  jobs: { id: string; label: string; about: string; choice: string }[];
  clipping: { choice: string; now: string };
  available: Record<string, boolean>;
}

const PROVIDERS: [string, string][] = [["claude_plan", "Claude on my plan"], ["claude_api", "Claude, paid API key"], ["gemini", "Gemini"], ["ollama", "Ollama on this computer"]];

/** Which AI does which job (D148). Automatic keeps Clipper's own order; a pick is tried first, and the
 *  paid Claude key is only ever used where you choose it here. */
function WhoDoesWhat() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ai-jobs"], queryFn: async () => {
    const res = await fetch("/api/ai-jobs");
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<AIJobs>;
  } });
  if (!data) return <Skeleton className="h-32" />;
  const set = async (job: string, choice: string) => {
    const res = await fetch("/api/ai-jobs", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job, choice }) });
    if (!res.ok) { toast.error("Couldn't save that"); return; }
    toast.success("Saved", { description: "Used from the next job." });
    void qc.invalidateQueries({ queryKey: ["ai-jobs"] });
    void qc.invalidateQueries({ queryKey: ["create", "ai"] });
  };
  const rows = [
    { id: "clipping", label: "Captions, descriptions and checks", about: "Everything else in clipping: captions, post descriptions, rule checks, scene splits.", choice: data.clipping.choice },
    ...data.jobs,
  ];
  return (
    <div className="flex flex-col divide-y divide-line">
      {rows.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
          <div className="min-w-0 max-w-md"><div className="text-sm font-medium">{r.label}</div><div className="text-xs text-muted">{r.about}</div></div>
          <select value={r.choice} aria-label={r.label} onChange={(e) => void set(r.id, e.target.value)}
                  className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm">
            <option value="">Automatic</option>
            {PROVIDERS.map(([k, label]) => <option key={k} value={k} disabled={!data.available[k]}>{label}{data.available[k] ? "" : " (not set up)"}</option>)}
          </select>
        </div>
      ))}
      <div className="py-2.5 text-xs text-muted">Watching the video itself is always Gemini: Claude can't take video. With no Gemini key that step is skipped and moments are judged from the words.</div>
    </div>
  );
}

function AIKey() {
  const { data: setup } = useSetup();
  const save = useSetKeys();
  const [key, setKey] = useState("");
  const [testing, setTesting] = useState(false);
  const check = async () => {
    setTesting(true);
    const res = await testAI().catch((e: Error) => ({ ok: false, detail: e.message }));
    setTesting(false);
    if (res.ok) toast.success("AI key works", { description: res.detail });
    else toast.error("AI key didn't work", { description: res.detail });
  };
  return (
    <Card className="flex flex-col gap-4 p-5" id="ai">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-md font-semibold">AI model for clipping</h2>
          <p className="mt-0.5 text-sm text-muted">
            Clipping uses Google's Gemini to watch the video, write captions and read campaign briefs, and Claude on your plan
            to judge the moments (change either under Who does what). Create, for your own channel, uses both; its page shows which AI is answering.
            Gemini is free:
            get a key at <Ext href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</Ext> (sign in, <b className="text-fg">Create API key</b>, copy).
          </p>
        </div>
        {setup && (setup.ai_ready
          ? <span className="flex shrink-0 items-center gap-1.5 text-sm font-medium text-success"><CheckCircle2 className="size-4" /> Ready</span>
          : <span className="flex shrink-0 items-center gap-1.5 text-sm font-medium text-warning"><AlertTriangle className="size-4" /> Needs a key</span>)}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Gemini API key" className="min-w-64 flex-1">
          {(id) => <SecretInput id={id} value={key} onChange={setKey} placeholder="AIza…" isSet={setup?.keys.GEMINI_API_KEY} />}
        </Field>
        <Button variant="primary" disabled={key.trim().length < 20 || save.isPending}
          onClick={() => save.mutate({ GEMINI_API_KEY: key }, {
            onSuccess: () => { setKey(""); void check(); },
            onError: (e) => toast.error((e as Error).message),
          })}>
          Save
        </Button>
        {setup?.ai_ready && (
          <Button variant="secondary" disabled={testing} onClick={() => void check()}>
            {testing && <Loader2 className="size-4 animate-spin" />} Test
          </Button>
        )}
      </div>
    </Card>
  );
}

/** The footage libraries Create searches, and their free keys (D130): typed here, kept in .env. */
function FootageKeys() {
  const { data: setup } = useSetup();
  const save = useSetKeys();
  const [values, setValues] = useState<Record<string, string>>({});
  const libraries: [string, string, string, ReactNode][] = [
    ["PIXABAY_API_KEY", "Pixabay", "https://pixabay.com/api/docs/", "Sign in, and your key is shown in the Parameters section."],
    ["PEXELS_API_KEY", "Pexels", "https://www.pexels.com/api/", "Free if Pexels is giving out keys; leave empty if not."],
    ["COVERR_API_KEY", "Coverr", "https://coverr.co/developers", "Make a free account, then create an app: the key is shown at once."],
  ];
  const typed = Object.entries(values).filter(([, v]) => v.trim());
  return (
    <Card className="flex flex-col gap-4 p-5" id="footage">
      <div>
        <h2 className="text-md font-semibold">Footage libraries for Create</h2>
        <p className="mt-0.5 text-sm text-muted">
          Create searches every library you have a key for, and NASA's video library, which needs none. All are free; more
          libraries mean more footage to choose from.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {libraries.map(([name, title, href, how]) => (
          <Field key={name} label={title}
                 hint={<>{setup?.keys[name] ? <span className="text-success">Key saved.</span> : <>No key yet. <Ext href={href}>Get one</Ext>: {how}</>}</>}>
            {(id) => <SecretInput id={id} value={values[name] ?? ""} onChange={(v) => setValues((x) => ({ ...x, [name]: v }))}
                                  isSet={setup?.keys[name]} placeholder="Paste the key" />}
          </Field>
        ))}
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted">NASA: always on, no key needed.</span>
        <Button variant="primary" disabled={!typed.length || save.isPending}
                onClick={() => save.mutate(Object.fromEntries(typed), {
                  onSuccess: () => { setValues({}); toast.success("Footage keys saved"); },
                  onError: (e) => toast.error((e as Error).message),
                })}>
          <KeyRound className="size-4" /> Save keys
        </Button>
      </div>
    </Card>
  );
}

function SystemCheck() {
  const [results, setResults] = useState<CheckResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      setResults(await runSystemCheck());
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const icon = (s: string) => s === "ok" ? <CheckCircle2 className="size-4 text-success" />
    : s === "warn" ? <AlertTriangle className="size-4 text-warning" /> : <XCircle className="size-4 text-danger" />;
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-md font-semibold">System check</h2>
          <p className="mt-0.5 text-sm text-muted">Checks this PC has what clipping needs: video tools, graphics card, fonts, disk space.</p>
        </div>
        <Button variant="secondary" disabled={busy} onClick={() => void run()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Stethoscope className="size-4" />} {busy ? "Checking…" : "Run check"}
        </Button>
      </div>
      {results && (
        <ul className="flex flex-col divide-y divide-line">
          {results.map((r) => (
            <li key={r.name} className="flex gap-2.5 py-2">
              <span className="pt-0.5">{icon(r.status)}</span>
              <div className="min-w-0">
                <div className="text-sm font-medium">{r.name}</div>
                <div className="text-xs break-words text-muted">{r.detail}</div>
                {r.status !== "ok" && r.fix && <pre className="mt-1 text-xs whitespace-pre-wrap text-warning">{r.fix}</pre>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function SettingsPage() {
  const { data: settings } = useSettings();
  const save = useSetSettings();
  const qc = useQueryClient();
  const theme = useUI((s) => s.theme);
  const setTheme = useUI((s) => s.setTheme);
  const shortcuts = useUI((s) => s.shortcuts);
  const setShortcutKeys = useUI((s) => s.setShortcutKeys);
  const themes: [Theme, string, ReactNode][] = [
    ["dark", "Dark", <Moon key="d" className="size-4" />],
    ["light", "Light", <Sun key="l" className="size-4" />],
    ["system", "System", <Monitor key="s" className="size-4" />],
  ];
  return (
    <div className="fade-in flex max-w-3xl flex-col gap-4">
      <PageHeader title="Settings" />
      <AIKey />
      <Card className="px-5 py-3"><h2 className="text-md font-semibold">Who does what</h2>
        <p className="mt-0.5 mb-1 text-sm text-muted">Pick the AI for each job, or leave it on Automatic.</p><WhoDoesWhat /></Card>
      <FootageKeys />
      <Card className="divide-y divide-line px-5">
        <Row title="This computer" body="Clipper reads every video's speech. A model too big for your graphics card runs out of memory; too small and the captions suffer. This picks the one that fits." control={<Computer />} />
        <Row title="What you use Clipper for" body="Parts you turn off are hidden everywhere, so the app only shows what you do." control={<Uses />} />
        <Row
          title="Sync every"
          body="How often Clipper fetches views and stats from your connected accounts while it's open. X is read at most hourly, since it charges per read."
          control={settings ? (
            <select value={settings.sync_minutes} aria-label="Sync interval"
                    onChange={(e) => save.mutate({ sync_minutes: e.target.value })}
                    className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm">
              {["5", "15", "30", "60"].map((m) => <option key={m} value={m}>{m} minutes</option>)}
            </select>
          ) : <Skeleton className="h-9 w-32" />}
        />
        <Row
          title="Tell me when it's done"
          body="A notification when a clipping job or a Short's build finishes while you're on another tab or window. Only this browser; it asks permission the first time."
          control={<NotifySwitch />}
        />
        <Row
          title="Open on the payoff"
          body={<>Podcast and stream clips start with the first half of their best line, stopping just before it lands, then
            a quick flash back to the setup, and end on that line so they loop. Never on scripted TV or film, where it spoils
            the joke; skipped when a brief forbids re-edits or no line works on its own.
            <Where to="/learning">See whether it's working →</Where></>}
          control={settings ? (
            <Switch label="Open on the payoff" checked={settings.payoff_first !== "0"}
                    onChange={(v) => save.mutate({ payoff_first: v ? "1" : "0" })} />
          ) : <Skeleton className="h-5 w-9" />}
        />
        <Row
          title="Pick the cover"
          body={<>Clipper finds each clip's best still (a clear face, sharp, well lit) and puts it on the first frame with the
            hook, so TikTok and Instagram show it as the cover and you don't have to choose one. It's held for 1/15 of a
            second, too short to notice when it plays. Skipped when a brief forbids re-edits.
            <Where to="/learning">See whether it's working →</Where></>}
          control={settings ? (
            <Switch label="Pick the cover" checked={settings.auto_cover !== "0"}
                    onChange={(v) => save.mutate({ auto_cover: v ? "1" : "0" })} />
          ) : <Skeleton className="h-5 w-9" />}
        />
        <Row
          title="Theme"
          body="Dark suits watching video; light can be easier for long reading."
          control={
            <div className="flex rounded-md border border-line bg-surface-2 p-0.5" role="radiogroup" aria-label="Theme">
              {themes.map(([value, label, icon]) => (
                <button key={value} role="radio" aria-checked={theme === value} onClick={() => setTheme(value)}
                        className={cn("flex h-8 items-center gap-1.5 rounded-sm px-3 text-sm",
                          theme === value ? "bg-surface-1 text-fg shadow-1" : "text-muted hover:text-fg")}>
                  {icon}{label}
                </button>
              ))}
            </div>
          }
        />
        <Row
          title="Single-key shortcuts"
          body="Keys like J, K, C and P. They never fire while you're typing. Ctrl K works either way."
          control={<Switch label="Single-key shortcuts" checked={shortcuts} onChange={setShortcutKeys} />}
        />
      </Card>
      {/* Not used day to day: a switch for a feature still to come, and a preview of paid tiers (D118). */}
      <details open={settings?.auto_post === "1" || undefined} className="group rounded-lg border border-line bg-surface-1">
        <summary className="cursor-pointer px-5 py-3 text-sm font-medium text-muted hover:text-fg">Advanced</summary>
        <div className="divide-y divide-line px-5">
          <Row
            title="Auto-post approved clips"
            body={<>On: an approved clip posts itself through each platform's official API, after a 10-minute
              hold you can cancel. Off: every post waits for your click. Posting from Clipper arrives in the next
              update; this choice is saved for it.</>}
            control={settings ? (
              <Switch label="Auto-post" checked={settings.auto_post === "1"}
                      onChange={(v) => save.mutate({ auto_post: v ? "1" : "0" })} />
            ) : <Skeleton className="h-5 w-9" />}
          />
          <Row
            title="Plan (preview)"
            body={<>How paid tiers would work in a hosted Clipper. <b className="text-fg">Free</b>: clipping and stats, up to 10 clips
              a video and 3 videos at a time. <b className="text-fg">Research</b>: adds the Ask chat, choosing a clip's on-screen line,
              writing your own captions, and 10 videos at a time. <b className="text-fg">Pro</b>: everything, including every clip a video
              has, several accounts per platform with groups, and auto-posting once it's available. Nothing is billed; switch to see each tier.</>}
            control={settings ? (
              <div className="flex rounded-md border border-line bg-surface-2 p-0.5" role="radiogroup" aria-label="Plan">
                {([["free", "Free"], ["research", "Research"], ["pro", "Pro"]] as const).map(([value, label]) => (
                  <button key={value} role="radio" aria-checked={settings.plan === value}
                          onClick={() => save.mutate({ plan: value }, { onSuccess: () => void qc.invalidateQueries({ queryKey: ["research"] }) })}
                          className={cn("h-8 rounded-sm px-3 text-sm",
                            settings.plan === value ? "bg-surface-1 text-fg shadow-1" : "text-muted hover:text-fg")}>
                    {label}
                  </button>
                ))}
              </div>
            ) : <Skeleton className="h-9 w-48" />}
          />
        </div>
      </details>
      <SystemCheck />
    </div>
  );
}
