import {
  AlertTriangle, CheckCircle2, ExternalLink, Eye, KeyRound, Loader2, Plus, Stethoscope, XCircle,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  connectInstagram, instagramConnectState, runSystemCheck, startInstagramConnect, startTikTokConnect, startYouTubeConnect, testAI, tiktokConnectState, youtubeConnectState, useAccounts, useDisconnect,
  useHosted, useSetKeys, useSetSettings, useSettings, useSetup, type Account, type CheckResult,
} from "@/api/client";
import { Field, TextInput } from "@/components/form";
import { GroupsCard } from "@/components/groups";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, CopyButton, PageHeader, Skeleton, Switch, Tip, buttonClass } from "@/components/ui";
import { notifyOn, notifySupported, setNotify } from "@/lib/notify";
import { useUI } from "@/lib/store";
import { cn, formatCount, openTab } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";

/* ---------- small pieces ---------- */

const Ext = ({ href, children }: { href: string; children: ReactNode }) => (
  <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium link">
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
    <button type="button" onClick={() => copy()}
            className={cn(buttonClass("secondary", "sm"), "ml-1")}>
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
          {account.groups.map((g) => <span key={g} className="font-semibold text-subtle">{g}</span>)}
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
          <Button size="sm" variant="danger" onClick={() => disconnect.mutateAsync({ platform: account.platform, id: account.id },
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
    <div className="flex flex-col gap-4 rounded-md bg-surface-2 p-4">
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
          onClick={() => save.mutateAsync({ TIKTOK_CLIENT_KEY: key, TIKTOK_CLIENT_SECRET: secret }, {
            onSuccess: () => { setKey(""); setSecret(""); toast.success("TikTok app keys saved"); onSaved?.(); },
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
  const hosted = useHosted();
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
      <div className="flex flex-col gap-3 rounded-md bg-surface-2 p-4">
        <div className="flex items-center gap-2 text-sm font-medium"><Loader2 className="size-4 animate-spin text-fg" /> Waiting for you to approve in the {name} tab…</div>
        <p className="text-sm text-muted">{waitingHint}</p>
        {trouble && Date.now() - waiting.since > 25_000 && <p className="rounded-md bg-warning/10 p-2.5 text-sm text-warning">{trouble}</p>}
        <div className="flex gap-2">
          <a href={waiting.url} target="_blank" rel="noopener noreferrer"
             className={buttonClass("secondary", "sm")}>
            <ExternalLink className="size-3.5" /> Open {name} again
          </a>
          <CopyButton text={waiting.url} what="Link" label="Copy link" />
        </div>
      </div>
    );
  }
  // Hosted, app keys are the site's (D229): without the site's own app for this platform there's nothing to set up here.
  if (hosted && !ready) return <span className="text-sm text-muted">Connecting {name} isn't available on this site yet.</span>;
  if (needKeys && !ready) return <>{appKeys}</>;
  return (
    <Button variant={ready ? "primary" : "secondary"} onClick={() => (ready ? void connect() : setNeedKeys(true))}>
      <PlatformIcon platform={platform} className="size-4" /> {ready ? label : `${label} (one-time setup)`}
    </Button>
  );
}

function AddTikTok({ label = "Add a TikTok account" }: { label?: string }) {
  const { data: setup } = useSetup();
  // Hosted (D239): the login comes back to this browser's Clipper session, and the app is the site's, not the user's.
  const hosted = useHosted();
  return (
    <AddAccount platform="tiktok" name="TikTok" label={label} ready={!!setup?.tiktok_app}
      appKeys={<TikTokAppKeys />} start={startTikTokConnect} state={tiktokConnectState}
      waitingHint={hosted
        ? "TikTok connects whichever account is logged in on tiktok.com in this browser. For a different account, log into it there first."
        : "TikTok connects whichever account is logged in on tiktok.com in that browser. For a different account, log into it there first, or copy this link into another browser."}
      trouble={hosted
        ? `Seeing "non_sandbox_target"? TikTok is still reviewing this site's app, so only test accounts can connect for now.`
        : `Seeing "non_sandbox_target" or "We couldn't log in with TikTok"? Your TikTok app is still in Sandbox: open it at developers.tiktok.com, go to Sandbox → Target users, add this TikTok account, then try again.`} />
  );
}

function YouTubeAppKeys() {
  const { data: setup } = useSetup();
  const save = useSetKeys();
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  return (
    <div className="flex flex-col gap-4 rounded-md bg-surface-2 p-4">
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
          onClick={() => save.mutateAsync({ YOUTUBE_CLIENT_ID: id, YOUTUBE_CLIENT_SECRET: secret }, {
            onSuccess: () => { setId(""); setSecret(""); toast.success("Google app saved"); },
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
    <div className="flex flex-col gap-4 rounded-md bg-surface-2 p-4">
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
          onClick={() => save.mutateAsync({ INSTAGRAM_APP_ID: id, INSTAGRAM_APP_SECRET: secret }, {
            onSuccess: () => { setId(""); setSecret(""); toast.success("Meta app saved", { description: "Now \"Add an Instagram account\" is one click." }); },
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
    <div className="flex flex-col gap-4 rounded-md bg-surface-2 p-4">
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
        <Button variant="primary" disabled={token.trim().length < 20 || busy} onClick={() => connect()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <PlatformIcon platform="instagram" className="size-4" />} Connect
        </Button>
      </div>
    </div>
  );
}

/** X: the app's Bearer Token and the account's username (D83). Paid per post read. */
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
  const hosted = useHosted();
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
          <p className="text-xs text-subtle">
            Clipper only reads stats through each platform's official API. {hosted ? "Logins are kept in your account's own folder on Clipper's server." : "Logins are stored on this PC only, in Clipper's data folder."}
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
      <div className="max-w-full shrink-0 pt-0.5">{control}</div>
    </div>
  );
}

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
  const { hardware: hw, recommended: rec } = data;
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
      {/* Model names without float16 or cuda (D221): the label says what it means. */}
      <div className="text-muted">{rec.label}. An hour of footage takes about {rec.minutes_per_hour} minutes to read.</div>
      {data.matches
        ? <div className="text-success">Already set up this way.</div>
        : <Button variant="primary" size="sm" className="self-start" onClick={() => apply()}>Use the best settings for this computer</Button>}
    </div>
  );
}

interface AIJobs {
  choice: string;
  available: Record<string, boolean>;
}

const PROVIDERS: [string, string][] = [["claude_plan", "Claude (your Claude subscription, through Claude Code)"], ["claude_api", "Claude API key (paid)"], ["gemini", "Gemini"]];

/** One AI for every job (D216; a list per job before, D148). Automatic keeps Clipper's own order per job.
 *  The paid Claude key is only used where `llm.paid_api_jobs` allows it, or if you pick it here. */
function WhichAI() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ai-jobs"], queryFn: async () => {
    const res = await fetch("/api/ai-jobs");
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<AIJobs>;
  } });
  if (!data) return <Skeleton className="h-9 w-48" />;
  const set = async (choice: string) => {
    const res = await fetch("/api/ai-jobs", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice }) });
    if (!res.ok) { toast.error("Couldn't save that"); return; }
    toast.success("Saved", { description: "Used from the next job." });
    void qc.invalidateQueries({ queryKey: ["ai-jobs"] });
    void qc.invalidateQueries({ queryKey: ["create", "ai"] });
  };
  return (
    <select value={data.choice} aria-label="Which AI" onChange={(e) => void set(e.target.value)}
            className="h-9 max-w-full rounded-sm border border-line bg-surface-2 px-3 text-sm">
      {/* Picks made per job before D216 keep working until one choice replaces them. */}
      {data.choice === "mixed" && <option value="mixed" disabled>Your own pick per job</option>}
      <option value="">Automatic</option>
      {PROVIDERS.filter(([k]) => data.available[k] || k === data.choice).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
    </select>
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
          <h2 className="text-md font-semibold">Gemini key</h2>
          <p className="mt-0.5 text-sm text-muted">
            Gemini watches videos, writes captions and scripts. It's free: get a key at <Ext href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</Ext> (sign in, <b className="text-fg">Create API key</b>, copy).
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
          onClick={() => save.mutateAsync({ GEMINI_API_KEY: key }, {
            onSuccess: () => { setKey(""); void check(); },
          })}>
          Save
        </Button>
        {setup?.ai_ready && (
          <Button variant="secondary" disabled={testing} onClick={() => check()}>
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
    ["PEXELS_API_KEY", "Pexels", "https://www.pexels.com/api/", "Sign in, then Your API key. The best people footage, and photos."],
    ["COVERR_API_KEY", "Coverr", "https://coverr.co/developers", "Make a free account, then create an app: the key is shown at once."],
    ["FREESOUND_API_KEY", "Freesound (sound hits)", "https://freesound.org/apiv2/apply", "Sign in, then apply for a key: the Client secret is the key. Only free-to-use (CC0) sounds are offered."],
  ];
  const typed = Object.entries(values).filter(([, v]) => v.trim());
  return (
    <Card className="flex flex-col gap-4 p-5" id="footage">
      <div>
        <h2 className="text-md font-semibold">Footage and sound libraries for Create</h2>
        <p className="mt-0.5 text-sm text-muted">
          Create searches every library you have a key for, and NASA's video library, which needs none. All are free; more
          libraries mean more footage to choose from. Freesound adds sounds to pick from for the sound hits;
          Pixabay's key covers only its footage, as its sounds aren't in its API.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
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
                onClick={() => save.mutateAsync(Object.fromEntries(typed), {
                  onSuccess: () => { setValues({}); toast.success("Library keys saved"); },
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
        <Button variant="secondary" disabled={busy} onClick={() => run()}>
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
  const hosted = useHosted();
  const save = useSetSettings();
  return (
    <div className="fade-in flex max-w-3xl flex-col gap-4">
      <PageHeader title="Settings" />
      {/* Under headings (D199): one long card mixed clipping, the app and this computer. */}
      {/* Hosted, the AI and the footage libraries are the site's, with its keys (D229): none of it is set here. */}
      {hosted === false && <>
        <h2 className="row-head">AI</h2>
        <AIKey />
        <Card className="px-5" id="create-ai">
          <Row title="Which AI"
               body="Automatic picks the best AI you have for each job. Watching a video is always Gemini."
               control={<WhichAI />} />
        </Card>
        <FootageKeys />
      </>}
      <h2 className={cn("row-head", hosted === false && "mt-6")}>Clipping</h2>
      <Card className="divide-y divide-line px-5">
        <Row
          title="Open on the payoff"
          body="Podcast and stream clips tease their best line first, then play from the start, so they loop. Never on TV or film."
          control={settings ? (
            <Switch label="Open on the payoff" checked={settings.payoff_first !== "0"}
                    onChange={(v) => save.mutate({ payoff_first: v ? "1" : "0" })} />
          ) : <Skeleton className="h-5 w-9" />}
        />
        <Row
          title="Pick the cover"
          body="Clipper puts each clip's best still on its first frame, so TikTok and Instagram use it as the cover."
          control={settings ? (
            <Switch label="Pick the cover" checked={settings.auto_cover !== "0"}
                    onChange={(v) => save.mutate({ auto_cover: v ? "1" : "0" })} />
          ) : <Skeleton className="h-5 w-9" />}
        />
      </Card>
      <h2 className="row-head mt-6">The app</h2>
      <Card className="divide-y divide-line px-5">
        <Row
          title="Sync every"
          body="How often Clipper fetches views and stats from your connected accounts while it's open."
          control={settings ? (
            <select value={settings.sync_minutes} aria-label="Sync interval"
                    onChange={(e) => save.mutate({ sync_minutes: e.target.value })}
                    className="h-9 max-w-full rounded-sm border border-line bg-surface-2 px-3 text-sm">
              {["5", "15", "30", "60"].map((m) => <option key={m} value={m}>{m} minutes</option>)}
            </select>
          ) : <Skeleton className="h-9 w-32" />}
        />
        <Row
          title="Tell me when it's done"
          body="A notification when a clipping job or a Short's build finishes while you're on another tab or window. Only this browser; it asks permission the first time."
          control={<NotifySwitch />}
        />
      </Card>
      {/* The plan preview and the GitHub Pages copies went (D221): a tier switch with nothing billed, and pages the
          account setups already offer where they're needed. */}
      {/* Hosted, this computer is the server's: nothing to set or check here. */}
      {hosted === false && <>
        <h2 className="row-head mt-6">This computer</h2>
        <Card className="divide-y divide-line px-5">
        <Row title="This computer" body="Clipper reads every video's speech. A model too big for your graphics card runs out of memory; too small and the captions suffer. This picks the one that fits." control={<Computer />} />
        </Card>
        <SystemCheck />
      </>}
    </div>
  );
}
