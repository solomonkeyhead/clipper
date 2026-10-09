import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { useCampaign, useEditCaption, type Clip } from "@/api/client";
import { Button, Tip } from "./ui";

async function rerender(id: number, hook?: string) {
  const res = await fetch(`/api/clips/${id}/rerender`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(hook ? { hook } : {}) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data as { hook: string };
}

const SELECT = "h-8 max-w-full rounded-md border border-line bg-surface-1 px-2 text-xs";

/**
 * The hook burned into the video (D90). Changing it makes the clip again in the
 * background: "New hook" takes the brief's least-used line; or choose a line, or
 * type one. Nothing here sits behind a plan (D120, D169).
 */
export function HookControl({ clip }: { clip: Clip }) {
  const qc = useQueryClient();
  const { data: detail } = useCampaign(clip.campaign);
  const lines = detail?.brief?.hook_texts ?? [];
  const [custom, setCustom] = useState<string | null>(null);
  const go = useMutation({
    mutationFn: (hook?: string) => rerender(clip.id, hook),
    onSuccess: (res) => { setCustom(null); void qc.invalidateQueries({ queryKey: ["clips"] });
      toast("Making it again", { description: `With “${res.hook}” on screen. Takes about a minute; you can keep working.` }); },
    onError: (e) => toast.error((e as Error).message),
  });
  if (clip.status !== "ready" && clip.status !== "skipped") return null;
  if (clip.rerendering) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted">
        <Loader2 className="size-3.5 animate-spin text-accent" />
        {clip.rerendering === "queued" ? "Waiting to be made again with its new hook…" : "Making it again with its new hook…"}
      </p>
    );
  }
  const mismatch = clip.hook && clip.hook !== clip.title && lines.includes(clip.title);
  return (
    <div className="flex flex-col gap-1.5">
      {clip.rerender_error && <p className="text-xs text-danger">Couldn't make it again: {clip.rerender_error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted">On-screen hook</span>
        {mismatch && (
          <Button size="sm" variant="secondary" disabled={go.isPending} onClick={() => go.mutate(clip.title)}>
            <RefreshCw className="size-3.5" /> Show the title
          </Button>
        )}
        {lines.length > 1 && (
          <Tip label="Make it again with the brief's line this campaign has used least">
            <Button size="sm" variant="ghost" disabled={go.isPending} onClick={() => go.mutate(undefined)}>
              <RefreshCw className="size-3.5" /> New hook
            </Button>
          </Tip>
        )}
        <select aria-label="Choose a hook" className={SELECT} value="" disabled={go.isPending}
                onChange={(e) => e.target.value === "\u0000" ? setCustom("") : e.target.value && go.mutate(e.target.value)}>
          <option value="">Choose…</option>
          {lines.filter((l) => l !== clip.hook).map((l) => <option key={l} value={l}>{l}</option>)}
          <option value={"\u0000"}>Write my own…</option>
        </select>
      </div>
      {custom !== null && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (custom.trim()) go.mutate(custom.trim()); }}>
          <input value={custom} onChange={(e) => setCustom(e.target.value)} autoFocus maxLength={120}
                 onKeyDown={(e) => e.stopPropagation()} placeholder="The line to show over its first seconds"
                 className="h-8 flex-1 rounded-sm border border-line bg-surface-1 px-2.5 text-sm focus:border-accent focus:outline-none" />
          <Button type="submit" size="sm" variant="primary" disabled={!custom.trim() || go.isPending}>Make it</Button>
          <Button size="sm" variant="ghost" onClick={() => setCustom(null)}>Cancel</Button>
        </form>
      )}
    </div>
  );
}

/** The caption's first line, swapped for another of the brief's approved captions. */
export function CaptionChoice({ clip }: { clip: Clip }) {
  const edit = useEditCaption();
  const { data: detail } = useCampaign(clip.campaign);
  const lines = detail?.brief?.captions ?? [];
  if (lines.length < 2 || (clip.status !== "ready" && clip.status !== "skipped")) return null;
  const current = lines.find((l) => clip.caption.startsWith(l));
  const choose = (line: string) => {
    // Swap the caption line, keeping the description and hashtags below it.
    const caption = current ? line + clip.caption.slice(current.length)
      : clip.caption.includes("\n\n") ? [line, ...clip.caption.split("\n\n").slice(1)].join("\n\n") : line;
    edit.mutate({ id: clip.id, caption }, {
      onSuccess: () => toast.success("Caption changed"),
      onError: (e) => toast.error((e as Error).message),
    });
  };
  return (
    <select aria-label="Choose a caption" className={SELECT} value="" disabled={edit.isPending}
            onChange={(e) => e.target.value && choose(e.target.value)}>
      <option value="">Choose…</option>
      {lines.filter((l) => l !== current).map((l) => <option key={l} value={l}>{l}</option>)}
    </select>
  );
}
