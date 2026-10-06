import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Settings2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { createApi, type ChannelEdit, type CreateView } from "@/api/client";
import { Button, Card } from "@/components/ui";
import { cn } from "@/lib/utils";

const input = "h-9 w-full rounded-sm border border-line bg-surface-2 px-3 text-sm outline-none focus:border-accent";
const area = "w-full rounded-md border border-line bg-surface-1 p-3 text-sm outline-none focus:border-accent";

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </label>
  );
}

/** Make a channel from a niche pack (D146): its persona, rules and diagrams to start from, yours to change after. */
export function NewChannel({ data, onDone, first = false }: { data: CreateView; onDone: () => void; first?: boolean }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [handle, setHandle] = useState("");
  const [pack, setPack] = useState(data.packs[0]?.key ?? "explainer");
  const [niche, setNiche] = useState("");
  const [busy, setBusy] = useState(false);
  const chosen = data.packs.find((p) => p.key === pack);
  const make = async () => {
    setBusy(true);
    try {
      await createApi.channelNew({ name, pack, handle, niche });
      await qc.invalidateQueries({ queryKey: ["create"] });
      toast.success("Channel made", { description: "Now get ideas for it, or write your own script." });
      onDone();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div>
        <h2 className="text-md font-semibold">{first ? "Set up your channel" : "A new channel"}</h2>
        <p className="mt-1 text-sm text-muted">
          A channel is one of your own accounts that you make videos for. Pick the kind of videos it makes; you can edit every
          rule after.
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Channel name"><input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Fact Machine" /></Field>
        <Field label="Its handle" hint="So Clipper can find its posts and count their views."><input className={input} value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@factmachine" /></Field>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {data.packs.map((p) => (
          <label key={p.key} className={cn("flex cursor-pointer flex-col gap-1 rounded-md border p-3", pack === p.key ? "border-accent bg-accent-soft/50" : "border-line")}>
            <span className="flex items-center gap-2 text-sm font-semibold">
              <input type="radio" name="pack" className="accent-[var(--color-accent)]" checked={pack === p.key} onChange={() => setPack(p.key)} /> {p.label}
            </span>
            <span className="text-xs text-muted">{p.about}</span>
          </label>
        ))}
      </div>
      <Field label="What it is about (optional)" hint={chosen && !chosen.drawings ? "This kind uses real stock footage only, no drawings." : undefined}>
        <input className={input} value={niche} onChange={(e) => setNiche(e.target.value)} placeholder="e.g. strange facts about the ocean" />
      </Field>
      <div className="flex gap-2">
        <Button variant="primary" disabled={busy || !name.trim()} onClick={() => void make()}>Make the channel</Button>
        {!first && <Button variant="ghost" onClick={onDone}>Cancel</Button>}
      </div>
    </Card>
  );
}

interface FullChannel {
  slug: string; name: string; handle: string; niche: string; persona: string; rules: string[]; voice: string;
  subject: string; expert: string; areas: string; idea_focus: string; idea_avoid: string; words_per_second: number; drawings: boolean; pack: string; examples: number;
}

/** Everything a channel's prompts say, editable. */
function ChannelSettings({ slug, onClose }: { slug: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["create", "channel", slug], queryFn: async () => {
    const res = await fetch(`/api/create/channels/${slug}`);
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<FullChannel>;
  } });
  const [edit, setEdit] = useState<ChannelEdit & { rulesText?: string }>({});
  if (!data) return null;
  const val = <K extends keyof FullChannel>(k: K) => (edit as Partial<FullChannel>)[k] ?? data[k];
  const set = (changes: Partial<ChannelEdit & { rulesText: string }>) => setEdit({ ...edit, ...changes });
  const save = async () => {
    const { rulesText, ...rest } = edit;
    try {
      await createApi.channelEdit(slug, { ...rest, ...(rulesText !== undefined ? { rules: rulesText.split("\n") } : {}) });
      await qc.invalidateQueries({ queryKey: ["create"] });
      toast.success("Channel saved", { description: "New scripts and ideas follow it." });
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><input className={input} value={val("name") as string} onChange={(e) => set({ name: e.target.value })} /></Field>
        <Field label="Handle"><input className={input} value={val("handle") as string} onChange={(e) => set({ handle: e.target.value })} /></Field>
        <Field label="What it is about"><input className={input} value={val("niche") as string} onChange={(e) => set({ niche: e.target.value })} /></Field>
        <Field label="The subject" hint="Said in the prompts: &quot;the &lt;subject&gt; behind it&quot;."><input className={input} value={val("subject") as string} onChange={(e) => set({ subject: e.target.value })} /></Field>
        <Field label="Who checks the facts"><input className={input} value={val("expert") as string} onChange={(e) => set({ expert: e.target.value })} /></Field>
        <Field label="Words a second" hint="How fast the voice reads. Sets how long a script runs."><input className={input} type="number" step="0.1" min="1.2" max="4"
          value={val("words_per_second") as number} onChange={(e) => set({ words_per_second: Number(e.target.value) })} /></Field>
      </div>
      <Field label="The topics it covers"><input className={input} value={val("areas") as string} onChange={(e) => set({ areas: e.target.value })} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Ideas should lean toward" hint="Steers every batch of ideas. Plain words are fine.">
          <textarea className={area} rows={3} value={val("idea_focus") as string} placeholder="e.g. forces and motion, light, sound, heat, electricity: physics acting on you or on things you use"
                    onChange={(e) => set({ idea_focus: e.target.value })} />
        </Field>
        <Field label="Ideas must never be about" hint="Skipped ideas also teach it what to avoid.">
          <textarea className={area} rows={3} value={val("idea_avoid") as string} placeholder="e.g. biology, physiology, medicine, how the body works inside"
                    onChange={(e) => set({ idea_avoid: e.target.value })} />
        </Field>
      </div>
      <Field label="Who is speaking (the persona)"><textarea className={area} rows={4} value={val("persona") as string} onChange={(e) => set({ persona: e.target.value })} /></Field>
      <Field label="The rules a script follows (one per line)">
        <textarea className={area} rows={8} value={edit.rulesText ?? data.rules.join("\n")} onChange={(e) => set({ rulesText: e.target.value })} />
      </Field>
      <Field label="How you make the voice" hint="Shown on the video's voice step."><input className={input} value={val("voice") as string} onChange={(e) => set({ voice: e.target.value })} /></Field>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="accent-[var(--color-accent)]" checked={val("drawings") as boolean} onChange={(e) => set({ drawings: e.target.checked })} />
        Use chalkboard drawings (off: real stock footage only)
      </label>
      <div className="flex gap-2">
        <Button variant="primary" onClick={() => void save()}>Save</Button>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
      </div>
    </Card>
  );
}

/** The channel switcher, its settings and "new channel", above everything in Create. */
export function ChannelBar({ data }: { data: CreateView }) {
  const qc = useQueryClient();
  const [panel, setPanel] = useState<"" | "new" | "edit">("");
  const switchTo = async (slug: string) => {
    await createApi.channelActive(slug);
    await qc.invalidateQueries({ queryKey: ["create"] });
  };
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {data.channels.length > 1 && (
          <select value={data.channel.slug} onChange={(e) => void switchTo(e.target.value)} aria-label="Channel"
                  className="h-9 rounded-sm border border-line bg-surface-2 px-3 text-sm">
            {data.channels.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
          </select>
        )}
        <Button variant="ghost" size="sm" onClick={() => setPanel(panel === "edit" ? "" : "edit")}><Settings2 className="size-3.5" /> Channel settings</Button>
        <Button variant="ghost" size="sm" onClick={() => setPanel(panel === "new" ? "" : "new")}><Plus className="size-3.5" /> New channel</Button>
      </div>
      {panel === "new" && <NewChannel data={data} onDone={() => setPanel("")} />}
      {panel === "edit" && <ChannelSettings slug={data.channel.slug} onClose={() => setPanel("")} />}
    </>
  );
}
