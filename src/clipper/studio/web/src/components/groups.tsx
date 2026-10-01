import { Eye, Layers, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  useAccountGroups, useCampaigns, useDeleteGroup, useSaveGroup, type Account, type AccountGroup,
} from "@/api/client";
import { useUI } from "@/lib/store";
import { cn } from "@/lib/utils";
import { TextInput } from "./form";
import { PlatformIcon } from "./PlatformIcon";
import { Button, Card } from "./ui";

/**
 * Account groups (D89): accounts that post together -- one TikTok, Instagram and
 * YouTube for movie clips -- and the campaigns they post for. The viewing
 * switcher in the top bar shows one group at a time.
 */
export function GroupsCard({ accounts }: { accounts: Account[] }) {
  const { data: groups = [] } = useAccountGroups();
  const [editing, setEditing] = useState<AccountGroup | null>(null);
  if (accounts.length < 2 && !groups.length) return null;
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-2.5 text-md font-semibold"><Layers className="size-5" /> Groups</span>
        {!editing && (
          <Button variant="secondary" size="sm" onClick={() => setEditing({ name: "", members: [], campaigns: [] })}>
            <Plus className="size-3.5" /> New group
          </Button>
        )}
      </div>
      {!groups.length && !editing && (
        <p className="text-sm text-muted">
          Bundle the accounts that post together, like one TikTok, Instagram and YouTube for movie clips, and pick the
          campaigns they post for. Then switch the whole Control Center to one group from the top bar.
        </p>
      )}
      {groups.map((g) => editing?.id === g.id
        ? <GroupEditor key={g.id} group={editing!} accounts={accounts} onDone={() => setEditing(null)} />
        : <GroupRow key={g.id} group={g} accounts={accounts} onEdit={() => setEditing(g)} />)}
      {editing && editing.id == null && <GroupEditor group={editing} accounts={accounts} onDone={() => setEditing(null)} />}
    </Card>
  );
}

function Member({ account }: { account: Account | undefined }) {
  if (!account) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs">
      <PlatformIcon platform={account.platform} className="size-3" /> @{account.handle}
    </span>
  );
}

function GroupRow({ group, accounts, onEdit }: { group: AccountGroup; accounts: Account[]; onEdit: () => void }) {
  const remove = useDeleteGroup();
  const setScope = useUI((s) => s.setAccountScope);
  const { data: campaigns = [] } = useCampaigns();
  const titles = Object.fromEntries(campaigns.map((c) => [c.name, c.title]));
  const members = group.members.map((k) => accounts.find((a) => a.key === k));
  const posts = members.reduce((n, a) => n + (a?.posts ?? 0), 0);
  const views = members.reduce((n, a) => n + (a?.views ?? 0), 0);
  return (
    <div className="flex flex-col gap-2 rounded-md border border-line bg-surface-2 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="flex-1 truncate text-sm font-semibold">{group.name}</span>
        <span className="text-xs text-muted">{posts} posts · {Intl.NumberFormat("en", { notation: "compact" }).format(views)} views</span>
        <Button size="sm" variant="ghost" onClick={() => { setScope(`group:${group.id}`); toast(`Viewing ${group.name}`, { description: "Switch back with the button in the top bar." }); }}>
          <Eye className="size-3.5" /> View
        </Button>
        <Button size="icon" variant="ghost" aria-label="Edit group" onClick={onEdit}><Pencil className="size-3.5" /></Button>
        <Button size="icon" variant="ghost" aria-label="Delete group"
                onClick={() => remove.mutate(group.id!, { onSuccess: () => toast("Group deleted", { description: "Its accounts stay connected." }) })}>
          <Trash2 className="size-3.5" />
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">{group.members.map((k, i) => <Member key={k} account={members[i]} />)}</div>
      <div className="text-xs text-muted">
        {group.campaigns.length ? <>Posts for {group.campaigns.map((c) => titles[c] ?? c).join(", ")}</> : "Posts for any campaign"}
      </div>
    </div>
  );
}

function GroupEditor({ group, accounts, onDone }: { group: AccountGroup; accounts: Account[]; onDone: () => void }) {
  const save = useSaveGroup();
  const { data: campaigns = [] } = useCampaigns();
  const [name, setName] = useState(group.name);
  const [members, setMembers] = useState<string[]>(group.members);
  const [chosen, setChosen] = useState<string[]>(group.campaigns ?? []);
  const toggle = (list: string[], set: (v: string[]) => void, value: string) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  const chip = (on: boolean) => cn("inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium",
    on ? "border-accent bg-accent-soft text-fg" : "border-line text-muted hover:text-fg");
  return (
    <div className="flex flex-col gap-3 rounded-md border border-dashed border-line p-3">
      <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder="Group name, e.g. Movies" aria-label="Group name" autoFocus />
      <div>
        <div className="mb-1.5 text-xs font-medium text-muted">Accounts</div>
        <div className="flex flex-wrap gap-1.5">
          {accounts.map((a) => (
            <button key={a.key} type="button" aria-pressed={members.includes(a.key)} className={chip(members.includes(a.key))}
                    onClick={() => toggle(members, setMembers, a.key)}>
              <PlatformIcon platform={a.platform} className="size-3.5" /> @{a.handle}
            </button>
          ))}
        </div>
      </div>
      <div>
        <div className="mb-1.5 text-xs font-medium text-muted">Posts for <span className="text-subtle">(none picked: any campaign)</span></div>
        <div className="flex flex-wrap gap-1.5">
          {campaigns.filter((c) => !c.archived).map((c) => (
            <button key={c.name} type="button" aria-pressed={chosen.includes(c.name)} className={chip(chosen.includes(c.name))}
                    onClick={() => toggle(chosen, setChosen, c.name)}>
              {c.title}
            </button>
          ))}
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <Button variant="primary" disabled={!name.trim() || !members.length || save.isPending}
          onClick={() => save.mutate({ id: group.id, name, members, campaigns: chosen }, {
            onSuccess: () => { toast.success(group.id == null ? "Group made" : "Group saved"); onDone(); },
            onError: (e) => toast.error((e as Error).message),
          })}>
          Save
        </Button>
      </div>
    </div>
  );
}
