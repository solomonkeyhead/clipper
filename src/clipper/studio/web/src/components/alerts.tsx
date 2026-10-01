import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, BellRing, CheckCircle2, ExternalLink, Loader2, RefreshCw, Send, Settings2,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";
import {
  checkAlerts, disconnectWhop, saveAlertPrefs, startWhopConnect, testAlertPush, useAlerts, useDiscordBot,
  useSetKeys, useWhopFeeds, watchChannels, watchWhopFeeds, whopConnectState, type Alerts, type DiscordBot,
} from "@/api/client";
import { cn, ago, openTab } from "@/lib/utils";
import { Field, NumberInput, Segmented, TextArea, TextInput } from "./form";
import { Button, Card, CopyButton } from "./ui";

const PORTAL = "https://discord.com/developers/applications";

function Step({ n, title, done, children }: { n: number; title: string; done: boolean; children: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className={cn("grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold",
        done ? "bg-success text-bg" : "bg-surface-3 text-muted")}>
        {done ? <CheckCircle2 className="size-4" /> : n}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2 pb-1">
        <div className="text-sm font-semibold">{title}</div>
        {children}
      </div>
    </li>
  );
}

const Hint = ({ children }: { children: ReactNode }) => <div className="text-sm text-muted">{children}</div>;
const ExtLink = ({ href, children }: { href: string; children: ReactNode }) => (
  <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
    {children}<ExternalLink className="size-3" />
  </a>
);

function TokenStep({ alerts, bot, error }: { alerts: Alerts; bot?: DiscordBot; error?: string }) {
  const save = useSetKeys();
  const qc = useQueryClient();
  const [token, setToken] = useState("");
  const submit = () => save.mutate({ DISCORD_BOT_TOKEN: token }, {
    onSuccess: () => {
      setToken("");
      void qc.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <Step n={1} title="Make a Discord bot for Clipper" done={!!bot}>
      <ol className="list-decimal pl-5 text-sm text-muted">
        <li>Open the <ExtLink href={PORTAL}>Discord Developer Portal</ExtLink>, click <b>New Application</b> and call it "Clipper alerts".</li>
        <li>In the <b>Bot</b> tab, turn on <b>Message Content Intent</b> and save.</li>
        <li>Still in <b>Bot</b>, click <b>Reset Token</b>, copy it, and paste it here. It stays on this computer.</li>
      </ol>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <TextInput type="password" autoComplete="off" spellCheck={false} value={token} aria-label="Bot token"
                   onChange={(e) => setToken(e.target.value)}
                   placeholder={alerts.token_set ? "Saved. Paste a new one to replace it" : "Bot token"} />
        <Button type="submit" variant="secondary" disabled={save.isPending || !token.trim()}>Save</Button>
      </form>
      {bot && <div className="flex items-center gap-1.5 text-sm text-success"><CheckCircle2 className="size-4" /> Connected as {bot.name}</div>}
      {bot && !bot.content_intent && (
        <div className="flex items-start gap-1.5 text-sm text-warning">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          Message Content Intent is off, so Clipper would read empty posts. Turn it on in the Bot tab and save.
        </div>
      )}
      {alerts.token_set && error && <div className="flex items-start gap-1.5 text-sm text-danger"><AlertTriangle className="mt-0.5 size-4 shrink-0" /> {error}</div>}
    </Step>
  );
}

function ChannelPicker({ alerts, bot }: { alerts: Alerts; bot: DiscordBot }) {
  const qc = useQueryClient();
  const [picked, setPicked] = useState<string[]>(alerts.watched.map((c) => c.id));
  const [busy, setBusy] = useState(false);
  useEffect(() => setPicked(alerts.watched.map((c) => c.id)), [alerts.watched]);
  const guilds = [...new Set(bot.channels.map((c) => c.guild))];
  const changed = picked.slice().sort().join() !== alerts.watched.map((c) => c.id).sort().join();
  const save = async () => {
    setBusy(true);
    try {
      qc.setQueryData(["alerts"], await watchChannels(picked));
      toast.success(picked.length ? `Watching ${picked.length} channel${picked.length === 1 ? "" : "s"}` : "Not watching any channels");
      if (picked.length) void checkAlerts();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!bot.channels.length) return <Hint>No channels yet. Add the bot to your server first (step 2), then refresh.</Hint>;
  return (
    <div className="flex flex-col gap-2">
      {guilds.map((g) => (
        <div key={g}>
          <div className="mb-1 text-xs font-medium text-subtle">{g}</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {bot.channels.filter((c) => c.guild === g).map((c) => (
              <label key={c.id} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={picked.includes(c.id)}
                       onChange={(e) => setPicked((p) => e.target.checked ? [...p, c.id] : p.filter((i) => i !== c.id))} />
                #{c.name}
              </label>
            ))}
          </div>
        </div>
      ))}
      <div><Button variant="primary" size="sm" disabled={busy || !changed} onClick={() => void save()}>
        {busy && <Loader2 className="size-3.5 animate-spin" />} Watch these
      </Button></div>
    </div>
  );
}

function Preferences({ alerts }: { alerts: Alerts }) {
  const qc = useQueryClient();
  const keys = useSetKeys();
  const [profile, setProfile] = useState(alerts.profile);
  const [rate, setRate] = useState<number | null>(alerts.min_rate);
  const [topic, setTopic] = useState("");
  const save = async () => {
    try {
      if (topic.trim()) await keys.mutateAsync({ NTFY_TOPIC: topic.trim() });
      qc.setQueryData(["alerts"], await saveAlertPrefs(profile, rate ?? 0));
      setTopic("");
      toast.success("Saved");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const test = () => void testAlertPush().then(() => toast.success("Sent. Check your phone."), (e) => toast.error((e as Error).message));
  return (
    <div className="flex flex-col gap-3">
      <Field label="What you clip" hint="The AI reads this to judge each campaign's fit. Say what you do and don't want.">
        {(id) => <TextArea id={id} rows={3} value={profile} onChange={(e) => setProfile(e.target.value)} />}
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Skip campaigns paying under" hint="Per 1,000 views, when a post says.">
          {(id) => <NumberInput id={id} value={rate} onChange={setRate} prefix="$" suffix="/ 1K" min={0} step={0.25} />}
        </Field>
        <Field label="Phone alerts (ntfy topic)" optional hint={<>Install <ExtLink href="https://ntfy.sh">ntfy</ExtLink>, subscribe to a hard-to-guess topic, and enter the same name here.</>}>
          {(id) => (
            <div className="flex gap-2">
              <TextInput id={id} type="password" autoComplete="off" value={topic} onChange={(e) => setTopic(e.target.value)}
                         placeholder={alerts.push_set ? "Saved. Enter a new one to replace it" : "my-clip-alerts-7f3k"} />
              {alerts.push_set && <Button variant="ghost" size="sm" onClick={test} aria-label="Send a test alert"><Send className="size-3.5" /> Test</Button>}
            </div>
          )}
        </Field>
      </div>
      <div><Button variant="secondary" size="sm" onClick={() => void save()}>Save preferences</Button></div>
    </div>
  );
}

function DiscordSetup({ alerts }: { alerts: Alerts }) {
  const { data: bot, error, refetch, isFetching } = useDiscordBot(alerts.token_set);
  const inServer = !!bot && bot.channels.length > 0;
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">
        For communities that announce campaigns in Discord (Vyro, brands, clipping servers). Follow their announcement
        channels into a private server of your own, and Clipper reads each new post there.
      </p>
      <ol className="flex flex-col gap-4">
        <TokenStep alerts={alerts} bot={bot} error={error ? (error as Error).message : undefined} />
        <Step n={2} title="Give it a private server" done={inServer}>
          <Hint>In Discord, click <b>+</b> (Add a Server) → <b>Create My Own</b> → <b>For me and my friends</b>. Then add the bot to it:</Hint>
          <div className="flex flex-wrap items-center gap-2">
            {bot ? (
              <a href={bot.invite} target="_blank" rel="noopener noreferrer"
                 className="inline-flex h-7 items-center gap-1.5 rounded-sm bg-accent px-2.5 text-xs font-medium text-accent-fg hover:bg-accent-hover">
                Add the bot to your server <ExternalLink className="size-3.5" />
              </a>
            ) : <Hint>(Finish step 1 first.)</Hint>}
            {bot && (
              <Button size="sm" variant="ghost" disabled={isFetching} onClick={() => void refetch()}>
                <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} /> Refresh
              </Button>
            )}
          </div>
          {inServer && <Hint>In: {[...new Set(bot.channels.map((c) => c.guild))].join(", ")}</Hint>}
        </Step>
        <Step n={3} title="Follow the campaign channels into it" done={alerts.watched.length > 0}>
          <Hint>
            In each Discord where campaigns get posted (Vyro, Whop clipping communities, brands you clip for), open the
            announcements or new-campaigns channel and click <b>Follow</b> at the top. Pick your server and a channel
            there (one channel for everything is fine). You need to be that server's owner, which you are.
          </Hint>
          <Hint>
            No Follow button? That channel isn't an announcement channel, so it can't be followed. Ask its mods, or paste
            campaigns you see there into <b>Check a campaign</b> below.
          </Hint>
        </Step>
        <Step n={4} title="Choose which channels Clipper reads" done={alerts.watched.length > 0}>
          {bot ? <ChannelPicker alerts={alerts} bot={bot} /> : <Hint>(Finish steps 1 and 2 first.)</Hint>}
        </Step>
      </ol>
    </div>
  );
}

const WHOP_DEV = "https://whop.com/dashboard/developer";
const WHOP_REDIRECT = "http://localhost:3456/callback";
const WHOP_PERMISSIONS: [string, string][] = [
  ["oauth:token_exchange", "Lets you sign in to Clipper with your Whop account. Clipper never posts or changes anything."],
  ["forum:read", "Reads campaign announcements in feeds you've joined, to alert you to campaigns that fit what you clip."],
  ["member:basic:read", "Lists the communities you've joined, so you can pick which feeds Clipper watches."],
  ["company:basic:read", "Shows the names of your communities and their feeds, so you can tell them apart."],
];

function WhopAppStep({ alerts }: { alerts: Alerts }) {
  const save = useSetKeys();
  const qc = useQueryClient();
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const submit = () => save.mutate({ ...(id.trim() && { WHOP_CLIENT_ID: id.trim() }), ...(secret.trim() && { WHOP_CLIENT_SECRET: secret.trim() }) }, {
    onSuccess: () => { setId(""); setSecret(""); void qc.invalidateQueries({ queryKey: ["alerts"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <Step n={1} title="Make a Whop app for Clipper" done={alerts.whop_app}>
      <ol className="list-decimal pl-5 text-sm text-muted">
        <li>Open the <ExtLink href={WHOP_DEV}>Whop developer dashboard</ExtLink> and create an app called "Clipper" (a website app).</li>
        <li>In <b>OAuth</b>, add the redirect URI <code className="rounded bg-surface-2 px-1 text-fg">{WHOP_REDIRECT}</code> <CopyButton text={WHOP_REDIRECT} what="Redirect URI" size="sm" variant="ghost" />.</li>
        <li>In <b>Permissions</b>, add these four. Whop asks why each is needed; copy the reasons:
          <ul className="mt-1 flex flex-col gap-1">
            {WHOP_PERMISSIONS.map(([name, why]) => (
              <li key={name} className="flex items-start gap-1.5"><code className="shrink-0 rounded bg-surface-2 px-1 text-fg">{name}</code>
                <span className="min-w-0 flex-1">{why}</span><CopyButton text={why} what="Reason" size="sm" variant="ghost" /></li>
            ))}
          </ul>
        </li>
        <li>Copy the app's ID (<code>NEXT_PUBLIC_WHOP_APP_ID</code>, starts with app_) and its API key (<code>WHOP_API_KEY</code>) into the boxes below. They stay on this computer.</li>
      </ol>
      <form className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <TextInput value={id} onChange={(e) => setId(e.target.value)} aria-label="Whop app ID" spellCheck={false}
                   placeholder={alerts.whop_app ? "App ID saved" : "App ID (app_...)"} />
        <TextInput type="password" autoComplete="off" spellCheck={false} value={secret} aria-label="Whop API key"
                   onChange={(e) => setSecret(e.target.value)} placeholder={alerts.whop_app ? "API key saved" : "API key"} />
        <Button type="submit" variant="secondary" disabled={save.isPending || (!id.trim() && !secret.trim())}>Save</Button>
      </form>
    </Step>
  );
}

function WhopSignIn({ alerts }: { alerts: Alerts }) {
  const qc = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const poll = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearInterval(poll.current), []);
  const connect = async () => {
    try {
      const res = await startWhopConnect();
      if (res.state === "failed") throw new Error(res.message);
      setWaiting(true);
      openTab(res.url);
      window.clearInterval(poll.current);
      poll.current = window.setInterval(async () => {
        const now = await whopConnectState();
        if (now.state === "done" || now.state === "failed") {
          window.clearInterval(poll.current);
          setWaiting(false);
          void qc.invalidateQueries({ queryKey: ["alerts"] });
          if (now.state === "done") toast.success("Signed in to Whop");
          else toast.error(now.message);
        }
      }, 2000);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const signOut = async () => {
    qc.setQueryData(["alerts"], await disconnectWhop());
    toast("Signed out of Whop");
  };
  return (
    <Step n={2} title="Sign in with Whop" done={alerts.whop_signed_in}>
      {alerts.whop_signed_in ? (
        <div className="flex items-center gap-2 text-sm text-success">
          <CheckCircle2 className="size-4" /> Signed in
          <Button size="sm" variant="ghost" onClick={() => void signOut()}>Sign out</Button>
        </div>
      ) : waiting ? (
        <div className="flex items-center gap-2 text-sm"><Loader2 className="size-4 animate-spin text-accent" /> Approve Clipper in the Whop tab…</div>
      ) : (
        <div><Button size="sm" variant="primary" disabled={!alerts.whop_app} onClick={() => void connect()}>Sign in with Whop</Button></div>
      )}
    </Step>
  );
}

function WhopFeedPicker({ alerts }: { alerts: Alerts }) {
  const qc = useQueryClient();
  const { data: feeds, error, refetch, isFetching } = useWhopFeeds(alerts.whop_signed_in);
  const [picked, setPicked] = useState<string[]>(alerts.whop_feeds.map((f) => f.id));
  const [busy, setBusy] = useState(false);
  useEffect(() => setPicked(alerts.whop_feeds.map((f) => f.id)), [alerts.whop_feeds]);
  if (!alerts.whop_signed_in) return <Hint>(Sign in first.)</Hint>;
  if (error) return <div className="text-sm text-danger">{(error as Error).message}</div>;
  if (!feeds) return <Hint>Loading your communities…</Hint>;
  const changed = picked.slice().sort().join() !== alerts.whop_feeds.map((f) => f.id).sort().join();
  const save = async () => {
    setBusy(true);
    try {
      qc.setQueryData(["alerts"], await watchWhopFeeds(picked));
      toast.success(picked.length ? `Watching ${picked.length} Whop feed${picked.length === 1 ? "" : "s"}` : "Not watching any Whop feeds");
      if (picked.length) void checkAlerts();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const companies = [...new Set(feeds.map((f) => f.company))];
  return (
    <div className="flex flex-col gap-2">
      {!feeds.length && <Hint>No feeds yet: join a community with campaign posts (step 3), then refresh.</Hint>}
      {companies.map((c) => (
        <div key={c}>
          <div className="mb-1 text-xs font-medium text-subtle">{c}</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {feeds.filter((f) => f.company === c).map((f) => (
              <label key={f.id} className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={picked.includes(f.id)}
                       onChange={(e) => setPicked((p) => e.target.checked ? [...p, f.id] : p.filter((i) => i !== f.id))} />
                {f.name}
              </label>
            ))}
          </div>
        </div>
      ))}
      <div className="flex gap-2">
        <Button variant="primary" size="sm" disabled={busy || !changed} onClick={() => void save()}>
          {busy && <Loader2 className="size-3.5 animate-spin" />} Watch these
        </Button>
        <Button size="sm" variant="ghost" disabled={isFetching} onClick={() => void refetch()}>
          <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} /> Refresh
        </Button>
      </div>
    </div>
  );
}

function WhopSetup({ alerts }: { alerts: Alerts }) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">
        Content Rewards' campaign bot posts new campaigns in Whop community feeds. Sign in with your own Whop app and
        Clipper reads the feeds you pick, through Whop's official API, as you.
      </p>
      <ol className="flex flex-col gap-4">
        <WhopAppStep alerts={alerts} />
        <WhopSignIn alerts={alerts} />
        <Step n={3} title="Join the communities that post campaigns" done={alerts.whop_feeds.length > 0}>
          <Hint>
            On Whop, join <b>Whop Clips</b> (its "Content Rewards New Campaigns" feed) and <b>Content Rewards</b>. A
            canceled membership can't be read, so rejoin if yours lapsed.
          </Hint>
        </Step>
        <Step n={4} title="Choose which feeds Clipper reads" done={alerts.whop_feeds.length > 0}>
          <WhopFeedPicker alerts={alerts} />
        </Step>
      </ol>
    </div>
  );
}

function Setup({ alerts, onClose }: { alerts: Alerts; onClose?: () => void }) {
  const [source, setSource] = useState<"whop" | "discord">("whop");
  return (
    <Card className="mb-6 flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-md font-semibold"><BellRing className="size-4 text-accent" /> Campaign alerts</h2>
          <p className="mt-0.5 text-sm text-muted">
            Clipper reads where new campaigns are announced, checks each one against what you clip, and lists them here.
          </p>
        </div>
        {onClose && <Button variant="ghost" size="sm" onClick={onClose}>Done</Button>}
      </div>
      <Segmented value={source} onChange={setSource} label="Source"
                 options={[["whop", `Whop${alerts.whop_feeds.length ? ` · ${alerts.whop_feeds.length} watched` : ""}`, "Content Rewards campaigns"],
                           ["discord", `Discord${alerts.watched.length ? ` · ${alerts.watched.length} watched` : ""}`, "Vyro, brands, clipping servers"]]} />
      {source === "whop" ? <WhopSetup alerts={alerts} /> : <DiscordSetup alerts={alerts} />}
      <div className="border-t border-line pt-4">
        <div className="mb-2 text-sm font-semibold">What fits you</div>
        <Preferences alerts={alerts} />
      </div>
    </Card>
  );
}

function Watching({ alerts, onEdit }: { alerts: Alerts; onEdit: () => void }) {
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const now = async () => {
    setBusy(true);
    try {
      const r = await checkAlerts();
      if (r.busy) toast("Already checking");
      else if (r.new.length) toast.success(`${r.new.length} new campaign${r.new.length === 1 ? "" : "s"}`, { description: r.new.join(", ") });
      else if (!r.errors.length) toast("Nothing new", { description: r.read ? `Read ${r.read} post${r.read === 1 ? "" : "s"}; none announced a new campaign.` : undefined });
      void qc.invalidateQueries({ queryKey: ["found"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const names = [...alerts.whop_feeds.map((f) => f.name), ...alerts.watched.map((c) => `#${c.name}`)];
  return (
    <Card className="mb-6 flex flex-wrap items-center gap-3 px-4 py-3">
      <BellRing className="size-4 shrink-0 text-accent" />
      <div className="min-w-0 flex-1 text-sm">
        <span className="font-medium">Campaign alerts on.</span>{" "}
        <span className="text-muted">
          Reading {names.length > 3 ? `${names.slice(0, 3).join(", ")} and ${names.length - 3} more` : names.join(", ")} every {alerts.every_minutes} min
          {" "}while Clipper is open · checked {ago(alerts.checked_at)}{alerts.push_set ? " · phone alerts on" : ""}
        </span>
        {alerts.error && <div className="mt-0.5 flex items-center gap-1.5 text-xs text-warning"><AlertTriangle className="size-3.5" /> {alerts.error}</div>}
      </div>
      <Button size="sm" variant="secondary" disabled={busy} onClick={() => void now()}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />} Check now
      </Button>
      <Button size="sm" variant="ghost" onClick={onEdit}><Settings2 className="size-3.5" /> Set up</Button>
    </Card>
  );
}

/** The top of Find campaigns: an invitation, the setup, or what's being watched. */
export function CampaignAlerts() {
  const { data: alerts } = useAlerts();
  const [editing, setEditing] = useState(false);
  if (!alerts) return null;
  const ready = (alerts.token_set && alerts.watched.length > 0) || (alerts.whop_signed_in && alerts.whop_feeds.length > 0);
  if (ready && !editing) return <Watching alerts={alerts} onEdit={() => setEditing(true)} />;
  if (!ready && !editing) {
    return (
      <Card className="mb-6 flex flex-wrap items-center gap-3 px-4 py-3">
        <BellRing className="size-4 shrink-0 text-accent" />
        <div className="min-w-0 flex-1 text-sm">
          <span className="font-medium">Hear about new campaigns first.</span>{" "}
          <span className="text-muted">Clipper can watch the Whop feeds and Discord channels where campaigns are announced and flag the ones that fit you.</span>
        </div>
        <Button size="sm" variant="primary" onClick={() => setEditing(true)}>Set up alerts</Button>
      </Card>
    );
  }
  return <Setup alerts={alerts} onClose={() => setEditing(false)} />;
}
