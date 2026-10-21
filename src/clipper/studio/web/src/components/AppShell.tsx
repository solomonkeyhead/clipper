import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  AlertTriangle, ArrowRight, BarChart3, Bot, Film, Gauge, Send, GraduationCap, LayoutDashboard, Loader2, LogOut, Megaphone, PanelLeft, RefreshCw,
  Scissors, Search, MoreHorizontal, Settings, UserCircle2, Wand2, WifiOff,
} from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  useCampaigns, useClips, useCreate, useCreateAI, useHome, useHosted, useJobs, useLearningOpen, useSetup, useStatus, useSyncNow, useUsage,
} from "@/api/client";
import { notify } from "@/lib/notify";
import { useLiveUpdates } from "@/lib/live";
import { useUI } from "@/lib/store";
import { ago, cn, formatMoney } from "@/lib/utils";
import { AccountScope } from "./AccountScope";
import { ClipSheet } from "./clips";
import { CommandPalette } from "./Palette";
import { Button, Kbd, Mark, Tag, Tip, buttonClass } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  badge?: number;
  hint?: string;     // what the badge counts, on hover
  tone?: "accent" | "warning" | "neutral";
  tag?: string;      // a word after the name: "Beta"
}

function useNav(): NavItem[] {
  const { data: campaigns = [] } = useCampaigns();
  const { data: clips = [] } = useClips();
  const { data: jobs = [] } = useJobs();
  const working = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const active = new Set(campaigns.filter((c) => !c.archived).map((c) => c.name));
  // Clips waiting on you: ready to post, or posted and not yet submitted.
  // Your own channel's posted Shorts have nothing to submit, so they aren't waiting (D147).
  const mine = clips.filter((c) => active.has(c.campaign));
  const toPost = mine.filter((c) => c.status === "ready").length;
  const toSubmit = mine.filter((c) => c.status === "posted" && c.submits !== false && !c.submit_at_views).length;
  const waiting = toPost + toSubmit;
  // The hosted site sells clipping, with Create in beta (D224); Learning waits for enough ratings there (D238).
  const hosted = useHosted();
  const learning = useLearningOpen();
  return [
    { to: "/", label: "Dashboard", icon: <LayoutDashboard /> },
    // Create is the daily job, so it sits right under the Dashboard (D118).
    { to: "/create", label: "Create", icon: <Wand2 />, tag: hosted ? "Beta" : undefined },
    // A number only where something waits on you (D154): a count of campaigns asked nothing of anyone.
    { to: "/campaigns", label: "Campaigns", icon: <Megaphone /> },
    { to: "/new", label: "New clips", icon: <Scissors />, badge: working, tone: "accent",
      hint: `${working} clipping job${working === 1 ? "" : "s"} running or queued` },
    { to: "/clips", label: "Clips", icon: <Film /> },
    { to: "/post", label: "Post queue", icon: <Send />, badge: waiting, tone: "accent",
      hint: [toPost && `${toPost} to post`, toSubmit && `${toSubmit} to submit`].filter(Boolean).join(", ") },
    { to: "/stats", label: "Stats", icon: <BarChart3 /> },
    ...(learning ? [{ to: "/learning", label: "Learning", icon: <GraduationCap /> }] : []),
  ];
}

const PHONE_TABS = ["/", "/create", "/clips", "/post", "/campaigns"];

const SECONDARY: NavItem[] = [
  { to: "/accounts", label: "Accounts", icon: <UserCircle2 /> },
  { to: "/settings", label: "Settings", icon: <Settings /> },
];

function NavLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const active = item.to === "/" ? path === "/" : path.startsWith(item.to);
  const link = (
    <Link
      to={item.to}
      className={cn(
        "group flex h-10 items-center gap-3 rounded-sm px-3 text-sm font-semibold text-muted",
        "transition-colors duration-[var(--dur-fast)] hover:bg-surface-2 hover:text-fg",
        // The page you're on is white on a grey slab, never amber (D120, D215): amber is only the next thing to do.
        active && "bg-surface-2 text-fg",
        collapsed && "justify-center px-0",
      )}
    >
      <span className="[&>svg]:size-[18px] [&>svg]:stroke-[1.9]">{item.icon}</span>
      {!collapsed && <span className="flex-1 truncate">{item.label}{item.tag && <> <Tag className="ml-0.5">{item.tag}</Tag></>}</span>}
      {!collapsed && item.badge ? (
        <span className={cn(
          "tabular min-w-5 text-right text-[13px] font-bold",
          item.tone === "warning" ? "text-warning" : "text-fg",
        )} title={item.hint}>{item.badge}</span>
      ) : null}
    </Link>
  );
  return collapsed ? <Tip label={item.tag ? `${item.label} (${item.tag.toLowerCase()})` : item.label} side="right">{link}</Tip> : link;
}

/** Clipping runs on the server whatever page is open; this shows it on every page. */
function ClippingPill() {
  const { data: jobs = [] } = useJobs();
  const working = jobs.filter((j) => j.status === "running" || j.status === "queued");
  if (!working.length) return null;
  const now = working.find((j) => j.status === "running");
  const label = now
    ? `Clipping${working.length > 1 ? ` · ${working.length} left` : ""} · ${Math.round(now.pct)}%`
    : `${working.length} queued`;
  return (
    <Tip label={now ? `${now.name}: ${now.stage}` : "Waiting to start"}>
      <Link to="/new" className="flex h-9 items-center gap-1.5 rounded-sm px-2.5 text-xs font-semibold text-fg hover:bg-surface-2"
            aria-label={label}>
        <Loader2 className="size-3.5 animate-spin" /> {label}
      </Link>
    </Tip>
  );
}

/** A Short being built, on every page, like clipping is (D143). */
function BuildPill() {
  const { data } = useCreate();
  const building = data?.videos.find((v) => v.status === "building");
  if (!building) return null;
  // The step, not only a percent: "Choosing footage: 4 of 10" shows it moving when the percent can't (D154).
  const label = building.stage ? `Short: ${building.stage}` : "Building a Short";
  return (
    <Tip label={`${building.script.title || "Building"}${building.stage ? `: ${building.stage}` : ""}`}>
      <Link to="/create" className="flex h-9 max-w-64 items-center gap-1.5 rounded-sm px-2.5 text-xs font-semibold whitespace-nowrap text-fg hover:bg-surface-2" aria-label={label}>
        <Loader2 className="size-3.5 shrink-0 animate-spin" /> <span className="truncate">{label}</span>
      </Link>
    </Tip>
  );
}

/** Tell the user (when they've turned notifications on) that a Short finished building while they were elsewhere. */
function useBuildNotifications() {
  const { data } = useCreate();
  const qc = useQueryClient();
  const seen = useRef<Map<number, string>>(new Map());
  useEffect(() => {
    if (!data) return;
    for (const v of data.videos) {
      const before = seen.current.get(v.id);
      if (before === "building" && v.status !== "building") {
        notify(v.status === "failed" ? "A Short didn't build" : "Your Short is built", v.script.title || "Untitled", "/create");
        void qc.invalidateQueries({ queryKey: ["usage"] });   // a build used a Short or a redraw (D238)
      }
      seen.current.set(v.id, v.status);
    }
  }, [data, qc]);
}

/** Which AI is doing the work, on every page; a click opens Settings (D143). When something's missing it names the fix
 *  and goes to it (D209): "AI needs setup" in amber said something was wrong, not what to do. */
function AIPill() {
  const { data: ai } = useCreateAI();
  const { data: setup } = useSetup();
  const path = useRouterState({ select: (s) => s.location.pathname });
  const hosted = useHosted();
  if (!ai || hosted !== false) return null;   // hosted, the AI is the site's to set up, not the user's (D229)
  const claude = (ai.order[0] ?? "").startsWith("claude_code") || (ai.order[0] ?? "").startsWith("anthropic");
  const paid = (ai.order[0] ?? "").startsWith("anthropic");
  const noKey = setup ? !setup.ai_ready : false;
  const problem = ai.problem || (noKey ? "Gemini isn't set up: clipping and Create can't write or watch without it." : "");
  const label = noKey ? "Add an AI key" : problem ? "Fix the AI" : claude ? "Claude + Gemini" : "Gemini";
  if (noKey && path === "/") return null;   // the Dashboard's setup list has it as a step (D211)
  return (
    <Tip label={problem || (claude
      ? `Claude ${paid ? "(paid API, drawing only)" : "on your plan"} judges moments and draws; Gemini does ${ai.gemini_jobs.join(", ") || "the rest"} and watches the video.`
      : "Gemini is doing everything: Claude isn't set up.")}>
      {/* A problem shows at every width (REDESIGN_NOTES 8): under 1024px it was hidden, and so was the fix. */}
      <Link to="/settings" hash="ai" aria-label={label} className={cn("h-9 items-center gap-1.5 rounded-sm px-2.5 text-xs font-semibold whitespace-nowrap hover:bg-surface-2",
        problem ? "flex text-warning" : "hidden text-muted hover:text-fg lg:flex")}>
        <Bot className="size-4" /> <span className={cn(problem && "hidden sm:inline")}>{label}</span>{problem && <ArrowRight className="size-3" />}
      </Link>
    </Tip>
  );
}

/** What has actually been paid, in the money colour, always in view (D215): money is its own colour and its own
 *  place, not a box among the clips. Only recorded payouts, so it shows nothing until there is one. */
function PaidLine() {
  const { data } = useHome();
  const paid = data?.metrics.paid_usd;
  if (paid == null) return null;
  return (
    <Tip label="The payouts you've recorded on each campaign's page. Only this is money received.">
      <Link to="/stats" className="flex h-9 shrink-0 items-center gap-1.5 rounded-sm px-2 text-xs font-semibold text-muted hover:bg-surface-2 hover:text-fg pointer-coarse:h-11 lg:mr-2">
        <b className="num text-[15px] text-money">{formatMoney(paid)}</b> paid
      </Link>
    </Tip>
  );
}

/** Hosted (D238): this period's allowance, the gateway's billing page, and log out. Asked again when a job or a build
 *  ends (live.ts, useBuildNotifications), not on a timer. The gateway's /logout takes a POST only. */
function UsageLine({ place }: { place: "side" | "rail" | "menu" }) {
  const hosted = useHosted();
  const { data: u } = useUsage(hosted === true);
  if (!u?.hosted) return null;
  const date = u.period_end ? new Date(u.period_end).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
  const plan = `${(u.plan ?? "").replace(/^./, (c) => c.toUpperCase())} plan`;
  const when = u.status === "past_due" ? "payment due" : !date ? "" : u.status === "canceled" ? `ends ${date}` : `resets ${date}`;
  const { minutes: m, shorts: s } = u;
  const plural = (n: number, word: string) => `${word}${n === 1 ? "" : "s"}`;
  const meters = [
    m && { text: `${m.used} of ${m.limit} min`, out: m.used >= m.limit && !m.credits_left },
    s && { text: `${s.used} of ${s.limit} ${s.once ? "free " : ""}${plural(s.limit, "Short")}`, out: s.used >= s.limit && !s.credits_left },
  ].filter((x): x is { text: string; out: boolean } => !!x);
  const credits = [m?.credits_left ? `${m.credits_left} min` : "", s?.credits_left ? `${s.credits_left} ${plural(s.credits_left, "Short")}` : ""]
    .filter(Boolean).join(", ");
  const billing = u.billing_url || "/billing";
  const logout = (
    <form method="post" action={u.logout_url || "/logout"} className="contents">
      {place === "rail"
        ? <Tip label="Log out" side="right"><button type="submit" aria-label="Log out" className={buttonClass("ghost", "icon")}><LogOut className="size-4" /></button></Tip>
        : <button type="submit" role={place === "menu" ? "menuitem" : undefined} className={cn("font-semibold text-muted hover:text-fg", place === "menu" && "flex h-11 w-full items-center gap-3 rounded-sm px-3 text-sm hover:bg-surface-3 [&_svg]:size-4")}>
            {place === "menu" && <LogOut />}Log out
          </button>}
    </form>
  );
  const summary = [`${plan}${when ? `, ${when}` : ""}`, ...meters.map((x) => x.text), credits && `Credits left: ${credits}`].filter(Boolean).join(" · ");
  if (place === "rail") {
    return (
      <div className="flex flex-col items-center gap-1">
        <Tip label={summary} side="right">
          <a href={billing} aria-label="Plan and billing" className={cn(buttonClass("ghost", "icon"), meters.some((x) => x.out) && "text-warning")}><Gauge className="size-4" /></a>
        </Tip>
        {logout}
      </div>
    );
  }
  const lines = (
    <>
      <div className="font-semibold text-fg">{plan}{when && <span className={cn("font-normal", u.status === "past_due" ? "text-warning" : "text-muted")}> · {when}</span>}</div>
      <div className="tabular flex flex-wrap gap-x-1.5">
        {meters.map((x, i) => <span key={x.text} className={cn(x.out && "text-warning")}>{i > 0 && <span aria-hidden className="mr-1.5 text-subtle">·</span>}{x.text}</span>)}
      </div>
      {credits && <div>Credits left: {credits}</div>}
    </>
  );
  if (place === "menu") {
    return (
      <div className="mt-1 flex flex-col border-t border-line pt-1">
        <div className="flex flex-col gap-0.5 px-3 py-2 text-xs text-muted">{lines}</div>
        <a href={billing} role="menuitem" className="flex h-11 items-center gap-3 rounded-sm px-3 text-sm font-semibold text-muted hover:bg-surface-3 hover:text-fg [&_svg]:size-4">
          <Gauge /> Plan and billing
        </a>
        {logout}
      </div>
    );
  }
  return (
    <div className="mx-3 flex flex-col gap-0.5 border-t border-line pt-3 text-xs text-muted">
      {lines}
      <div className="mt-1.5 flex items-center gap-4">
        <a href={billing} className="inline-flex min-h-8 items-center font-semibold text-fg hover:underline pointer-coarse:min-h-11">Plan and billing</a>
        {logout}
      </div>
    </div>
  );
}

function SyncPill() {
  const { data: status } = useStatus();
  const syncing = useUI((s) => s.syncing) || status?.syncing;
  const sync = useSyncNow();
  const setSyncing = useUI((s) => s.setSyncing);
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  const problems = status?.problems ?? [];
  const busy = syncing || sync.isPending;
  return (
    <Tip label={problems.length ? problems.join(" · ")
      : `Syncs every ${status?.sync_minutes ?? 15} min while open. Click to sync now.`}>
      <button
        onClick={() => { setSyncing(true); sync.mutate(undefined, { onSettled: () => setSyncing(false) }); }}
        disabled={busy}
        className="flex h-9 items-center gap-2 rounded-sm px-2.5 text-xs font-semibold text-muted hover:bg-surface-2 hover:text-fg disabled:opacity-80"
      >
        {problems.length ? <AlertTriangle className="size-3.5 text-warning" />
          : <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />}
        <span className="tabular whitespace-nowrap">
          {busy ? "Syncing…" : !status?.last_synced ? "Sync now" : <><span className="hidden sm:inline">Synced </span>{ago(status.last_synced)}</>}
        </span>
      </button>
    </Tip>
  );
}

/** Whether the window is narrower than `px`, kept up to date. */
function useNarrow(px: number) {
  const query = `(max-width: ${px - 1}px)`;
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const on = () => setNarrow(list.matches);
    list.addEventListener("change", on);
    return () => list.removeEventListener("change", on);
  }, [query]);
  return narrow;
}

export function AppShell() {
  useLiveUpdates();
  // In a narrow window the sidebar starts as icons, so pages get the room;
  // it still opens on demand, without changing the wide-window preference.
  const narrow = useNarrow(1024);
  const [narrowOpen, setNarrowOpen] = useState(false);
  const wideCollapsed = useUI((s) => s.collapsed);
  const toggleWide = useUI((s) => s.toggleCollapsed);
  const collapsed = narrow ? !narrowOpen : wideCollapsed;
  const toggle = narrow ? () => setNarrowOpen((v) => !v) : toggleWide;
  const online = useUI((s) => s.online);
  const setPalette = useUI((s) => s.setPalette);
  const nav = useNav();
  const [more, setMore] = useState(false);
  // A phone's tabs are the daily jobs (D198): posting is what a phone is for, and Clips and the Post queue sat
  // under More while New clips (uploading long videos) had a tab.
  const tabs = PHONE_TABS.map((to) => nav.find((i) => i.to === to)).filter((i): i is NavItem => !!i).slice(0, 4);
  const rest = [...nav.filter((i) => !tabs.includes(i)), ...SECONDARY];
  const path = useRouterState({ select: (s) => s.location.pathname });
  const moreActive = rest.some((i) => path.startsWith(i.to));
  useBuildNotifications();
  // Keyboard shortcuts went (D221): Ctrl K for search is the one left.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPalette]);

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* Sidebar (desktop) */}
      <aside className={cn(
        "hidden shrink-0 flex-col border-r border-line bg-bg transition-[width] duration-[var(--dur-panel)] ease-standard md:flex",
        collapsed ? "w-16" : "w-60",
      )}>
        <div className={cn("flex h-16 items-center gap-2.5 px-5", collapsed && "justify-center px-0")}>
          <Mark className="h-7 text-fg" />
          {!collapsed && <span className="cap text-[26px] tracking-[0.04em]">Clipper</span>}
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-3 py-2" aria-label="Main">
          {nav.map((item) => <NavLink key={item.to} item={item} collapsed={collapsed} />)}
          <div className="my-2 h-px bg-line" />
          {SECONDARY.map((item) => <NavLink key={item.to} item={item} collapsed={collapsed} />)}
        </nav>
        <UsageLine place={collapsed ? "rail" : "side"} />
        <div className={cn("flex items-center gap-1 p-3", collapsed && "flex-col")}>
          <Tip label={collapsed ? "Expand sidebar" : "Collapse sidebar"} side="right">
            <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle sidebar">
              <PanelLeft className="size-4" />
            </Button>
          </Tip>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="flex h-16 shrink-0 items-center justify-between gap-3 px-4 md:px-8">
          <button
            onClick={() => setPalette(true)}
            className="flex h-10 w-full max-w-sm items-center gap-2 rounded-sm bg-surface-2 px-3 text-sm text-subtle hover:text-muted"
          >
            <Search className="size-4" />
            <span className="flex-1 truncate text-left">
              Search<span className="hidden sm:inline"> clips, campaigns, actions</span>…
            </span>
            <Kbd className="hidden sm:inline">Ctrl K</Kbd>
          </button>
          <div className="flex items-center gap-0.5">
            <PaidLine />
            <AccountScope />
            <AIPill />
            <ClippingPill />
            <BuildPill />
            <SyncPill />
          </div>
        </header>

        {!online && (
          <div className="flex items-center gap-2 bg-[color-mix(in_oklch,var(--warning)_12%,transparent)] px-8 py-2 text-sm font-semibold text-warning">
            <WifiOff className="size-4" /> Lost connection to Clipper. Showing the last data; reconnecting…
          </div>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto pb-[calc(3.5rem+1px+env(safe-area-inset-bottom))] md:pb-0">
          <div className="mx-auto w-full max-w-[1520px] px-4 pt-4 pb-8 md:px-8 md:pt-6 md:pb-12">
            <Outlet />
          </div>
        </main>

        {/* Bottom tabs (mobile) */}
        <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-bg pb-[env(safe-area-inset-bottom)] md:hidden" aria-label="Main">
          {tabs.map((item) => (
            <Link key={item.to} to={item.to}
                  activeOptions={{ exact: item.to === "/" }}
                  className="relative flex h-14 flex-col items-center justify-center gap-0.5 text-[12px] font-semibold text-subtle data-[status=active]:text-fg data-[status=active]:before:absolute data-[status=active]:before:inset-x-5 data-[status=active]:before:top-0 data-[status=active]:before:h-0.5 data-[status=active]:before:bg-fg [&_svg]:size-5">
              {item.icon}
              {item.label}
              {item.badge ? <span className="tabular absolute top-1.5 left-[calc(50%+8px)] text-[11px] font-bold text-fg">{item.badge}</span> : null}
              {item.tag && <>{" "}<Tag className="absolute top-1 left-[calc(50%+10px)] text-[9px]">{item.tag}</Tag></>}
            </Link>
          ))}
          {/* The rest of the pages: a phone had no way to Stats, Settings and the others (D161). */}
          <button onClick={() => setMore(!more)} aria-expanded={more} aria-haspopup="menu"
                  className={cn("relative flex h-14 flex-col items-center justify-center gap-0.5 text-[12px] font-semibold text-subtle [&_svg]:size-5",
                    (more || moreActive) && "text-fg")}>
            <MoreHorizontal />
            More
            {rest.some((i) => i.badge) ? <span className="absolute top-1.5 right-[calc(50%-18px)] size-2 rounded-full bg-fg" /> : null}
          </button>
        </nav>
        {more && <>
          <button aria-label="Close the menu" className="fixed inset-0 z-30 cursor-default md:hidden" onClick={() => setMore(false)} />
          <div role="menu" className="menu-in origin-bottom-right fixed right-2 bottom-[calc(3.5rem+env(safe-area-inset-bottom)+0.5rem)] z-40 flex w-56 flex-col rounded-md bg-surface-2 p-1 shadow-3 md:hidden">
            {rest.map((item) => (
              <Link key={item.to} to={item.to} role="menuitem" onClick={() => setMore(false)}
                    className="flex h-11 items-center gap-3 rounded-sm px-3 text-sm font-semibold text-muted hover:bg-surface-3 hover:text-fg data-[status=active]:text-fg [&_svg]:size-4">
                {item.icon}
                <span className="flex-1">{item.label}{item.tag && <> <Tag className="ml-0.5">{item.tag}</Tag></>}</span>
                {item.badge ? <span className="tabular text-xs font-bold text-fg">{item.badge}</span> : null}
              </Link>
            ))}
            <UsageLine place="menu" />
          </div>
        </>}
      </div>

      <ClipSheet />
      <CommandPalette />
    </div>
  );
}
