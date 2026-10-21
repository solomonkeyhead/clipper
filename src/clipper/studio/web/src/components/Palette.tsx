import { useNavigate } from "@tanstack/react-router";
import { Command } from "cmdk";
import {
  BarChart3, Film, Search, GraduationCap, Inbox, LayoutDashboard, Plus, Megaphone, PanelLeft, RefreshCw, Scissors, Send,
  Settings, UserCircle2, Wand2,
} from "lucide-react";
import type { ReactNode } from "react";
import { useCampaigns, useClips, useLearningOpen, useSyncNow } from "@/api/client";
import { useUI } from "@/lib/store";

/**
 * Every typed word must appear in the item (cmdk's default fuzzy match let
 * "chem" find "Switch to light theme"). Whole-phrase and prefix matches rank first.
 */
function matchWords(value: string, search: string, keywords: string[] = []): number {
  const title = value.toLowerCase();
  const rest = keywords.join(" ").toLowerCase();
  const query = search.toLowerCase().trim();
  if (!query) return 1;
  const words = query.split(/\s+/);
  if (!words.every((w) => title.includes(w) || rest.includes(w))) return 0;
  // A title match outranks one found only in a campaign name or caption.
  if (title.startsWith(query) || title.includes(` ${query}`)) return 1;
  if (title.includes(query)) return 0.8;
  if (words.every((w) => title.includes(w))) return 0.6;
  return 0.3;
}

function Item({ icon, label, onSelect, hint, value, keywords }: {
  icon: ReactNode; label: ReactNode; onSelect: () => void; hint?: string;
  value?: string; keywords?: string[];
}) {
  return (
    <Command.Item
      value={value}
      keywords={keywords}
      onSelect={onSelect}
      className="flex h-10 cursor-pointer items-center gap-3 rounded-md px-3 text-sm text-muted data-[selected=true]:bg-surface-2 data-[selected=true]:text-fg [&_svg]:size-4"
    >
      {icon}
      <span className="flex-1 truncate">{label}</span>
      {hint && <span className="truncate text-xs text-subtle">{hint}</span>}
    </Command.Item>
  );
}

const Group = ({ heading, children }: { heading: string; children: ReactNode }) => (
  <Command.Group heading={heading}
    className="px-2 pb-2 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-subtle">
    {children}
  </Command.Group>
);

export function CommandPalette() {
  const open = useUI((s) => s.paletteOpen);
  const setOpen = useUI((s) => s.setPalette);
  const setClip = useUI((s) => s.setOpenClip);
  const toggleCollapsed = useUI((s) => s.toggleCollapsed);
  const navigate = useNavigate();
  const sync = useSyncNow();
  const { data: campaigns = [] } = useCampaigns();
  const { data: clips = [] } = useClips();
  const learning = useLearningOpen();

  const run = (fn: () => void) => () => { setOpen(false); fn(); };
  const go = (to: string, search?: Record<string, string>) => run(() => void navigate({ to, search }));
  const title = new Map(campaigns.map((c) => [c.name, c.title]));

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command menu"
      filter={matchWords}
      overlayClassName="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]"
      contentClassName="fixed top-[14vh] left-1/2 z-50 w-[min(640px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-md border border-line-strong bg-surface-1/95 shadow-3 backdrop-blur-xl fade-in"
    >
      <Command.Input
        placeholder="Type a command or search…"
        className="h-12 w-full border-b border-line bg-transparent px-4 text-md outline-none placeholder:text-subtle"
      />
      <Command.List className="max-h-[min(420px,60vh)] overflow-y-auto py-2">
        <Command.Empty className="px-4 py-8 text-center text-sm text-muted">No results.</Command.Empty>
        <Group heading="Go to">
          <Item icon={<LayoutDashboard />} label="Dashboard" onSelect={go("/")} />
          <Item icon={<Wand2 />} label="Create a Short for your channel" onSelect={go("/create")} />
          <Item icon={<Megaphone />} label="Campaigns" onSelect={go("/campaigns")} />
          <Item icon={<Scissors />} label="New clips from footage" onSelect={go("/new")} />
          <Item icon={<Film />} label="Clips" onSelect={go("/clips")} />
          <Item icon={<Send />} label="Post queue: one clip at a time" onSelect={go("/post")} />
          <Item icon={<Send />} label="Clips ready to post" onSelect={go("/clips", { status: "ready" })} />
          <Item icon={<Inbox />} label="Clips to submit" onSelect={go("/clips", { status: "posted" })} />
          <Item icon={<Plus />} label="New campaign" onSelect={go("/campaigns/new")} />
          <Item icon={<BarChart3 />} label="Stats" onSelect={go("/stats")} />
          {learning && <Item icon={<GraduationCap />} label="Learning: what Clipper has learned" onSelect={go("/learning")} />}
          <Item icon={<UserCircle2 />} label="Accounts" onSelect={go("/accounts")} />
          <Item icon={<Settings />} label="Settings" onSelect={go("/settings")} />
        </Group>
        <Group heading="Actions">
          <Item icon={<Search />} label="Find campaigns" onSelect={go("/campaigns", { find: "1" })} />
          <Item icon={<RefreshCw />} label="Sync stats now" onSelect={run(() => sync.mutate())} />
          <Item icon={<PanelLeft />} label="Toggle sidebar" onSelect={run(toggleCollapsed)} />
        </Group>
        {campaigns.length > 0 && (
          <Group heading="Campaigns">
            {campaigns.map((c) => (
              <Item key={c.name} value={c.title} keywords={["campaign", c.name]} icon={<Megaphone />} label={c.title}
                    hint={c.archived ? "archived" : `${c.clips} clips`}
                    onSelect={go(`/campaigns/${encodeURIComponent(c.name)}`)} />
            ))}
          </Group>
        )}
        {clips.length > 0 && (
          <Group heading="Clips">
            {clips.map((c) => (
              <Item key={c.id} value={`${c.title} #${c.id}`} keywords={[title.get(c.campaign) ?? c.campaign, c.caption, "clip"]} icon={<Film />}
                    label={c.title} hint={title.get(c.campaign) ?? c.campaign} onSelect={run(() => setClip(c.id))} />
            ))}
          </Group>
        )}
      </Command.List>
    </Command.Dialog>
  );
}
