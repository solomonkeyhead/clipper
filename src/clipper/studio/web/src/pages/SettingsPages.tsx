import { AlertTriangle, CheckCircle2, Monitor, Moon, Sun, XCircle } from "lucide-react";
import { useAccounts, useSetSettings, useSettings } from "@/api/client";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Card, PageHeader, Skeleton, Switch } from "@/components/ui";
import { useUI, type Theme } from "@/lib/store";
import { PLATFORM_NAME, cn } from "@/lib/utils";

export function AccountsPage() {
  const { data, isLoading } = useAccounts();
  return (
    <div className="fade-in">
      <PageHeader title="Accounts" subtitle="The accounts Clipper reads stats from. Posting will use the same connections." />
      {isLoading ? <Skeleton className="h-40" /> : (
        <div className="grid gap-4 md:grid-cols-2">
          {(data ?? []).map((a) => (
            <Card key={a.platform} className="flex flex-col gap-3 p-5">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2.5 text-md font-semibold">
                  <PlatformIcon platform={a.platform} className="size-5" />
                  {PLATFORM_NAME[a.platform] ?? a.platform}
                </span>
                <span className={cn("flex items-center gap-1.5 text-sm font-medium",
                  a.health === "ok" ? "text-success" : a.health === "warn" ? "text-warning" : "text-danger")}>
                  {a.health === "ok" ? <CheckCircle2 className="size-4" /> : a.health === "warn"
                    ? <AlertTriangle className="size-4" /> : <XCircle className="size-4" />}
                  {a.connected ? "Connected" : "Not connected"}
                </span>
              </div>
              {a.handle && <div className="text-sm">@{a.handle}</div>}
              <p className="text-sm text-muted">{a.detail}</p>
              {a.expires_in_days != null && a.connected && (
                <p className="text-xs text-subtle">Login good for {Math.floor(a.expires_in_days)} more days.</p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ title, body, control }: { title: string; body: React.ReactNode; control: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-6 py-4">
      <div className="max-w-xl">
        <div className="text-sm font-medium">{title}</div>
        <div className="mt-1 text-sm text-muted">{body}</div>
      </div>
      <div className="shrink-0 pt-0.5">{control}</div>
    </div>
  );
}

export function SettingsPage() {
  const { data: settings } = useSettings();
  const save = useSetSettings();
  const theme = useUI((s) => s.theme);
  const setTheme = useUI((s) => s.setTheme);
  const shortcuts = useUI((s) => s.shortcuts);
  const setShortcutKeys = useUI((s) => s.setShortcutKeys);
  const themes: [Theme, string, React.ReactNode][] = [
    ["dark", "Dark", <Moon key="d" className="size-4" />],
    ["light", "Light", <Sun key="l" className="size-4" />],
    ["system", "System", <Monitor key="s" className="size-4" />],
  ];
  return (
    <div className="fade-in max-w-3xl">
      <PageHeader title="Settings" />
      <Card className="divide-y divide-line px-5">
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
          title="Sync every"
          body="How often Clipper fetches views and stats from TikTok and Instagram while it's open. A background task also syncs every 6 hours."
          control={settings ? (
            <select value={settings.sync_minutes} aria-label="Sync interval"
                    onChange={(e) => save.mutate({ sync_minutes: e.target.value })}
                    className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm">
              {["5", "15", "30", "60"].map((m) => <option key={m} value={m}>{m} minutes</option>)}
            </select>
          ) : <Skeleton className="h-9 w-32" />}
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
    </div>
  );
}
