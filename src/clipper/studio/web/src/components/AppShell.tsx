import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import {
  AlertTriangle, BarChart3, Film, GraduationCap, LayoutDashboard, Megaphone, PanelLeft, RefreshCw, Scissors, Search, Sparkles,
  Settings, UserCircle2, WifiOff,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useCampaigns, useClips, useJobs, useStatus, useSyncNow } from "@/api/client";
import { useHotkeys } from "@/lib/hotkeys";
import { useLiveUpdates } from "@/lib/live";
import { useUI } from "@/lib/store";
import { ago, cn } from "@/lib/utils";
import { AskPanel } from "./AskPanel";
import { ClipSheet } from "./clips";
import { CommandPalette, ShortcutSheet } from "./Palette";
import { Button, Kbd, Tip } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  keys: string;
  badge?: number;
  tone?: "accent" | "warning" | "neutral";
}

function useNav(): NavItem[] {
  const { data: campaigns = [] } = useCampaigns();
  const { data: clips = [] } = useClips();
  const { data: jobs = [] } = useJobs();
  const working = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  const active = new Set(campaigns.filter((c) => !c.archived).map((c) => c.name));
  // Clips waiting on you: ready to post, or posted and not yet submitted.
  const unrated = clips.filter((c) => c.rating == null && c.file_exists).length;
  const waiting = clips.filter((c) => (c.status === "ready" || c.status === "posted") && active.has(c.campaign)).length;
  return [
    { to: "/", label: "Dashboard", icon: <LayoutDashboard />, keys: "G D" },
    { to: "/campaigns", label: "Campaigns", icon: <Megaphone />, keys: "G C", badge: active.size },
    { to: "/new", label: "New clips", icon: <Scissors />, keys: "G N", badge: working, tone: "accent" },
    { to: "/clips", label: "Clips", icon: <Film />, keys: "G L", badge: waiting, tone: "accent" },
    { to: "/stats", label: "Stats", icon: <BarChart3 />, keys: "G S" },
    { to: "/learning", label: "Learning", icon: <GraduationCap />, keys: "G R", badge: unrated, tone: "neutral" as const },
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
        "group flex h-9 items-center gap-3 rounded-md px-2.5 text-sm font-medium text-muted",
        "transition-colors duration-[var(--dur-fast)] hover:bg-surface-2 hover:text-fg",
        active && "bg-surface-2 text-fg",
        collapsed && "justify-center px-0",
      )}
    >
      <span className={cn("[&>svg]:size-[18px] [&>svg]:stroke-[1.75]", active && "text-accent")}>{item.icon}</span>
      {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
      {!collapsed && item.badge ? (
        <span className={cn(
          "tabular min-w-5 rounded-full px-1.5 text-center text-[11px] leading-5 font-semibold",
          item.tone === "accent" ? "bg-accent-soft text-accent"
            : item.tone === "warning" ? "bg-[color-mix(in_oklch,var(--warning)_16%,transparent)] text-warning"
            : "bg-surface-3 text-muted",
        )}>{item.badge}</span>
      ) : null}
    </Link>
  );
  return collapsed ? <Tip label={item.label} keys={item.keys} side="right">{link}</Tip> : link;
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

export function AppShell() {
  useLiveUpdates();
  const navigate = useNavigate();
  const collapsed = useUI((s) => s.collapsed);
  const toggle = useUI((s) => s.toggleCollapsed);
  const online = useUI((s) => s.online);
  const setPalette = useUI((s) => s.setPalette);
  const setShortcuts = useUI((s) => s.setShortcuts);
  const setAsk = useUI((s) => s.setAsk);
  const nav = useNav();
  const pendingG = useRef(0);

  const goto = (to: string) => () => {
    if (Date.now() - pendingG.current < 1200) {
      pendingG.current = 0;
      void navigate({ to });
    }
  };
  useHotkeys({
    g: () => { pendingG.current = Date.now(); },
    d: goto("/"), h: goto("/"), c: goto("/campaigns"), n: goto("/new"), l: goto("/clips"),
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
          <div className="grid size-7 place-items-center rounded-md bg-accent text-accent-fg shadow-1">
            <svg viewBox="0 0 24 24" className="size-3.5" fill="currentColor"><path d="M8 5.5v13l10.5-6.5z" /></svg>
          </div>
          {!collapsed && <span className="text-md font-semibold tracking-tight">Clipper</span>}
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
          {[nav[0], nav[1], nav[2], nav[3], nav[4]].map((item) => (
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
