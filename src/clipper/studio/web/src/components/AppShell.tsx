import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import {
  AlertTriangle, BarChart3, Bot, Film, Send, GraduationCap, LayoutDashboard, Loader2, Megaphone, PanelLeft, RefreshCw, Scissors, Search, Sparkles,
  Settings, UserCircle2, Wand2, WifiOff,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useCampaigns, useClips, useCreate, useCreateAI, useJobs, useSetup, useStatus, useSyncNow, useUses } from "@/api/client";
import { notify } from "@/lib/notify";
import { useHotkeys } from "@/lib/hotkeys";
import { useLiveUpdates } from "@/lib/live";
import { useUI } from "@/lib/store";
import { ago, cn } from "@/lib/utils";
import { AccountScope } from "./AccountScope";
import { AskPanel } from "./AskPanel";
import { ClipSheet } from "./clips";
import { CommandPalette, ShortcutSheet } from "./Palette";
import { Button, Kbd, Mark, Tip } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  keys: string;
  badge?: number;
  hint?: string;     // what the badge counts, on hover
  tone?: "accent" | "warning" | "neutral";
}

function useNav(): NavItem[] {
  const { data: campaigns = [] } = useCampaigns();
  const { data: clips = [] } = useClips();
  const { data: jobs = [] } = useJobs();
  const uses = useUses();
  const working = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const active = new Set(campaigns.filter((c) => !c.archived).map((c) => c.name));
  // Clips waiting on you: ready to post, or posted and not yet submitted.
  // Your own channel's posted Shorts have nothing to submit, so they aren't waiting (D147).
  const mine = clips.filter((c) => active.has(c.campaign));
  const toPost = mine.filter((c) => c.status === "ready").length;
  const toSubmit = mine.filter((c) => c.status === "posted" && c.submits !== false && !c.submit_at_views).length;
  const waiting = toPost + toSubmit;
  return [
    { to: "/", label: "Dashboard", icon: <LayoutDashboard />, keys: "G D" },
    // Create is the daily job, so it sits right under the Dashboard (D118); hidden when you don't use it (D145).
    ...(uses.create ? [{ to: "/create", label: "Create", icon: <Wand2 />, keys: "G M" }] : []),
    // A number only where something waits on you (D154): a count of campaigns asked nothing of anyone.
    { to: "/campaigns", label: "Campaigns", icon: <Megaphone />, keys: "G C" },
    { to: "/new", label: "New clips", icon: <Scissors />, keys: "G N", badge: working, tone: "accent",
      hint: `${working} clipping job${working === 1 ? "" : "s"} running or queued` },
    { to: "/clips", label: "Clips", icon: <Film />, keys: "G L" },
    { to: "/post", label: "Post queue", icon: <Send />, keys: "", badge: waiting, tone: "accent",
      hint: [toPost && `${toPost} to post`, toSubmit && `${toSubmit} to submit`].filter(Boolean).join(", ") },
    { to: "/stats", label: "Stats", icon: <BarChart3 />, keys: "G S" },
    { to: "/learning", label: "Learning", icon: <GraduationCap />, keys: "G R" },
  ];
}

const SECONDARY: NavItem[] = [
  { to: "/accounts", label: "Accounts", icon: <UserCircle2 />, keys: "G A" },
  { to: "/settings", label: "Settings", icon: <Settings />, keys: "G ," },
];

function NavLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const active = item.to === "/" ? path === "/" : path.startsWith(item.to);
  const link = (
    <Link
      to={item.to}
      className={cn(
        "group flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted",
        "transition-colors duration-[var(--dur-fast)] hover:bg-surface-2 hover:text-fg",
        // The page you're on is the lit word.
        active && "bg-accent font-bold text-accent-fg hover:bg-accent-hover hover:text-accent-fg",
        collapsed && "justify-center px-0",
      )}
    >
      <span className="[&>svg]:size-[18px] [&>svg]:stroke-[1.9]">{item.icon}</span>
      {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
      {!collapsed && item.badge ? (
        <span className={cn(
          "tabular min-w-5 rounded-full px-1.5 text-center text-[11px] leading-5 font-semibold",
          active ? "bg-accent-fg/15 text-accent-fg"
            : item.tone === "accent" ? "bg-accent-soft text-accent"
            : item.tone === "warning" ? "bg-[color-mix(in_oklch,var(--warning)_16%,transparent)] text-warning"
            : "bg-surface-3 text-muted",
        )} title={item.hint}>{item.badge}</span>
      ) : null}
    </Link>
  );
  return collapsed ? <Tip label={item.label} keys={item.keys} side="right">{link}</Tip> : link;
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
      <Link to="/new" className="flex h-8 items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft px-3 text-xs font-medium text-accent hover:border-accent"
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
      <Link to="/create" className="flex h-8 max-w-64 items-center gap-1.5 rounded-full border border-accent/40 bg-accent-soft px-3 text-xs font-medium whitespace-nowrap text-accent hover:border-accent" aria-label={label}>
        <Loader2 className="size-3.5 shrink-0 animate-spin" /> <span className="truncate">{label}</span>
      </Link>
    </Tip>
  );
}

/** Tell the user (when they've turned notifications on) that a Short finished building while they were elsewhere. */
function useBuildNotifications() {
  const { data } = useCreate();
  const seen = useRef<Map<number, string>>(new Map());
  useEffect(() => {
    if (!data) return;
    for (const v of data.videos) {
      const before = seen.current.get(v.id);
      if (before === "building" && v.status !== "building") {
        notify(v.status === "failed" ? "A Short didn't build" : "Your Short is built", v.script.title || "Untitled", "/create");
      }
      seen.current.set(v.id, v.status);
    }
  }, [data]);
}

/** Which AI is doing the work, on every page; a click opens Settings (D143). */
function AIPill() {
  const { data: ai } = useCreateAI();
  const { data: setup } = useSetup();
  if (!ai) return null;
  const claude = (ai.order[0] ?? "").startsWith("claude_code") || (ai.order[0] ?? "").startsWith("anthropic");
  const paid = (ai.order[0] ?? "").startsWith("anthropic");
  const problem = ai.problem || (setup && !setup.ai_ready ? "Gemini isn't set up" : "");
  const label = problem ? "AI needs setup" : claude ? "Claude + Gemini" : "Gemini";
  return (
    <Tip label={problem || (claude
      ? `Claude ${paid ? "(paid API, drawing only)" : "on your plan"} judges moments and draws; Gemini does ${ai.gemini_jobs.join(", ") || "the rest"} and watches the video.`
      : "Gemini is doing everything: Claude isn't set up.")}>
      <Link to="/settings" className={cn("hidden h-8 items-center gap-1.5 rounded-full border border-line bg-surface-1 px-3 text-xs whitespace-nowrap hover:border-line-strong lg:flex",
        problem ? "text-warning" : "text-muted hover:text-fg")}>
        <Bot className="size-3.5" /> {label}
      </Link>
    </Tip>
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
        className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface-1 px-3 text-xs text-muted hover:border-line-strong hover:text-fg disabled:opacity-80"
      >
        {problems.length ? <AlertTriangle className="size-3.5 text-warning" />
          : <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />}
        <span className="tabular whitespace-nowrap">
          {busy ? "Syncing…" : <><span className="hidden sm:inline">Synced </span>{ago(status?.last_synced)}</>}
        </span>
      </button>
    </Tip>
  );
}

function AutoPostPill() {
  const { data: status } = useStatus();
  if (!status?.auto_post) return null;
  return (
    <Link to="/settings"
          className="flex h-8 items-center gap-1.5 rounded-full bg-[color-mix(in_oklch,var(--warning)_16%,transparent)] px-3 text-xs font-semibold text-warning">
      <span className="size-1.5 animate-pulse rounded-full bg-current" /> Auto-post on
    </Link>
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
  const navigate = useNavigate();
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
  const setShortcuts = useUI((s) => s.setShortcuts);
  const setAsk = useUI((s) => s.setAsk);
  const nav = useNav();
  useBuildNotifications();
  const pendingG = useRef(0);

  const goto = (to: string) => () => {
    if (Date.now() - pendingG.current < 1200) {
      pendingG.current = 0;
      void navigate({ to });
    }
  };
  useHotkeys({
    g: () => { pendingG.current = Date.now(); },
    d: goto("/"), h: goto("/"), c: goto("/campaigns"), n: goto("/new"), l: goto("/clips"), m: goto("/create"),
    s: goto("/stats"), r: goto("/learning"), a: goto("/accounts"), ",": goto("/settings"),
    "[": toggle,
    i: () => setAsk(true),
    "?": () => setShortcuts(true),
    "/": () => setPalette(true),
  });
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
        "hidden shrink-0 flex-col border-r border-line bg-surface-1 transition-[width] duration-[var(--dur-panel)] ease-standard md:flex",
        collapsed ? "w-14" : "w-60",
      )}>
        <div className={cn("flex h-12 items-center gap-2.5 px-4", collapsed && "justify-center px-0")}>
          <Mark className="h-7 text-fg" />
          {!collapsed && <span className="cap text-xl">Clipper</span>}
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-2 py-2" aria-label="Main">
          {nav.map((item) => <NavLink key={item.to} item={item} collapsed={collapsed} />)}
          <div className="my-2 h-px bg-line" />
          {SECONDARY.map((item) => <NavLink key={item.to} item={item} collapsed={collapsed} />)}
        </nav>
        <div className={cn("flex items-center gap-1 border-t border-line p-2", collapsed && "flex-col")}>
          <Tip label={collapsed ? "Expand sidebar" : "Collapse sidebar"} keys="[" side="right">
            <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle sidebar">
              <PanelLeft className="size-4" />
            </Button>
          </Tip>
          <Tip label="Keyboard shortcuts" keys="?" side="right">
            <Button variant="ghost" size="icon" onClick={() => setShortcuts(true)} aria-label="Keyboard shortcuts">
              <span className="text-sm font-semibold">?</span>
            </Button>
          </Tip>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-line bg-bg/80 px-4 backdrop-blur-md md:px-6">
          <button
            onClick={() => setPalette(true)}
            className="flex h-8 w-full max-w-sm items-center gap-2 rounded-md border border-line bg-surface-1 px-3 text-sm text-subtle hover:border-line-strong hover:text-muted"
          >
            <Search className="size-4" />
            <span className="flex-1 truncate text-left">
              Search<span className="hidden sm:inline"> clips, campaigns, actions</span>…
            </span>
            <Kbd className="hidden sm:inline">Ctrl K</Kbd>
          </button>
          <div className="flex items-center gap-2">
            <Tip label="Ask about your clips, campaigns and trends" keys="I">
              <button onClick={() => setAsk(true)}
                      className="flex h-8 items-center gap-1.5 rounded-full border border-line bg-surface-1 px-3 text-xs font-medium text-muted hover:border-line-strong hover:text-fg">
                <Sparkles className="size-3.5 text-accent" /> Ask
              </button>
            </Tip>
            <AccountScope />
            <AIPill />
            <ClippingPill />
            <BuildPill />
            <AutoPostPill />
            <SyncPill />
          </div>
        </header>

        {!online && (
          <div className="flex items-center gap-2 border-b border-line bg-[color-mix(in_oklch,var(--warning)_12%,transparent)] px-6 py-2 text-sm text-warning">
            <WifiOff className="size-4" /> Lost connection to Clipper. Showing the last data; reconnecting…
          </div>
        )}

        <main className="min-h-0 flex-1 overflow-y-auto pb-20 md:pb-0">
          <div className="mx-auto w-full max-w-[1440px] px-4 py-6 md:px-8 md:py-8">
            <Outlet />
          </div>
        </main>

        {/* Bottom tabs (mobile) */}
        <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-line bg-surface-1/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden" aria-label="Main">
          {nav.slice(0, 5).map((item) => (
            <Link key={item.to} to={item.to}
                  activeOptions={{ exact: item.to === "/" }}
                  className="relative flex h-14 flex-col items-center justify-center gap-0.5 text-[11px] text-muted data-[status=active]:text-accent [&_svg]:size-5">
              {item.icon}
              {item.label}
              {item.badge ? <span className="absolute top-1.5 right-[calc(50%-18px)] size-2 rounded-full bg-accent" /> : null}
            </Link>
          ))}
        </nav>
      </div>

      <ClipSheet />
      <AskPanel />
      <CommandPalette />
      <ShortcutSheet />
    </div>
  );
}
