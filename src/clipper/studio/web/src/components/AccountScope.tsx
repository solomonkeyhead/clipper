import { ChevronDown, Layers, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useAccountGroups, useAccounts } from "@/api/client";
import { useUI } from "@/lib/store";
import { cn } from "@/lib/utils";
import { PlatformIcon } from "./PlatformIcon";

/**
 * "Viewing": all accounts, one group, or one account (D89). Narrows the dashboard,
 * clips and stats. Shown only once there's something to choose between -- more
 * than one account on a platform, or a group -- so a one-account setup never sees it.
 */
export function AccountScope() {
  const { data: accounts = [] } = useAccounts();
  const { data: groups = [] } = useAccountGroups();
  const scope = useUI((s) => s.accountScope);
  const setScope = useUI((s) => s.setAccountScope);
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  const perPlatform = accounts.reduce<Record<string, number>>((n, a) => ({ ...n, [a.platform]: (n[a.platform] ?? 0) + 1 }), {});
  const worthShowing = groups.length > 0 || Object.values(perPlatform).some((n) => n > 1);
  const group = groups.find((g) => `group:${g.id}` === scope);
  const account = accounts.find((a) => `account:${a.key}` === scope);
  // A group or account that has gone (deleted, disconnected): back to everything.
  useEffect(() => {
    if (scope !== "all" && accounts.length && !group && !account) setScope("all");
  }, [scope, accounts.length, group, account, setScope]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  if (!worthShowing) return null;
  const choose = (value: string) => { setScope(value); setOpen(false); };
  const item = (value: string, children: React.ReactNode) => (
    <button key={value} type="button" role="menuitemradio" aria-checked={scope === value} onClick={() => choose(value)}
            className={cn("flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-surface-2",
              scope === value && "bg-accent-soft text-fg")}>
      {children}
    </button>
  );
  const label = account ? `@${account.handle}` : group ? group.name : "All accounts";
  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open}
              aria-label={`Showing ${label}`}
              className={cn("flex h-8 max-w-48 items-center gap-1.5 rounded-full border px-3 text-xs font-medium",
                scope === "all" ? "border-line bg-surface-1 text-muted hover:text-fg" : "border-accent bg-accent-soft text-fg")}>
        {account ? <PlatformIcon platform={account.platform} className="size-3.5" /> : group ? <Layers className="size-3.5" /> : <Users className="size-3.5" />}
        {/* The name only fits beside the other pills from tablet width up. */}
        <span className="hidden truncate sm:inline">{label}</span>
        <ChevronDown className="size-3 shrink-0" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-50 mt-1 max-h-[70vh] w-64 overflow-auto rounded-md border border-line bg-surface-1 p-1 shadow-3">
          {item("all", <><Users className="size-4 text-muted" /> All accounts</>)}
          {groups.length > 0 && <div className="px-2 pt-2 pb-1 text-xs font-semibold tracking-wide text-subtle uppercase">Groups</div>}
          {groups.map((g) => item(`group:${g.id}`, <><Layers className="size-4 text-muted" /><span className="flex-1 truncate">{g.name}</span>
            <span className="text-xs text-subtle">{g.members.length}</span></>))}
          <div className="px-2 pt-2 pb-1 text-xs font-semibold tracking-wide text-subtle uppercase">Accounts</div>
          {accounts.map((a) => item(`account:${a.key}`, <><PlatformIcon platform={a.platform} className="size-4" />
            <span className="flex-1 truncate">@{a.handle}</span></>))}
        </div>
      )}
    </div>
  );
}
