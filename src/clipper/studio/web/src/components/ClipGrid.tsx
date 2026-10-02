import { useEffect, useRef, useState } from "react";
import { downloadUrl, type Clip } from "@/api/client";
import { useHotkeys } from "@/lib/hotkeys";
import { useUI } from "@/lib/store";
import { copyText } from "@/lib/utils";
import { ClipCard, SelectionBar, useBulk, useDeleteWithUndo, useStatusWithUndo } from "./clips";

/** A grid of clips you can drive from the keyboard: J/K, Enter, C, L, P, X.
 *  Pick several with the corner box, Ctrl-click or Shift-click; X and Delete then act on all of them. */
export function ClipGrid({ clips, showCampaign = false }: { clips: Clip[]; showCampaign?: boolean }) {
  const [focus, setFocus] = useState(-1);
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set());
  const anchor = useRef<number | null>(null);
  const setListIds = useUI((s) => s.setListIds);
  const open = useUI((s) => s.setOpenClip);
  const setStatus = useStatusWithUndo();
  const remove = useDeleteWithUndo();
  const bulk = useBulk();

  useEffect(() => setListIds(clips.map((c) => c.id)), [clips, setListIds]);
  useEffect(() => {
    if (focus < 0) return;
    document.querySelector<HTMLElement>(`[data-clip="${clips[focus]?.id}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focus, clips]);
  // A picked clip that leaves the grid (filtered out, deleted) is no longer picked.
  useEffect(() => setPicked((s) => {
    const shown = new Set(clips.map((c) => c.id));
    return [...s].every((id) => shown.has(id)) ? s : new Set([...s].filter((id) => shown.has(id)));
  }), [clips]);

  const pick = (i: number, e: React.MouseEvent) => {
    const id = clips[i].id;
    const from = e.shiftKey && anchor.current !== null ? clips.findIndex((c) => c.id === anchor.current) : -1;
    setPicked((s) => {
      const next = new Set(s);
      if (from >= 0) clips.slice(Math.min(from, i), Math.max(from, i) + 1).forEach((c) => next.add(c.id));
      else if (!next.delete(id)) next.add(id);
      return next;
    });
    anchor.current = id;
  };
  const clear = () => { setPicked(new Set()); anchor.current = null; };
  const chosen = clips.filter((c) => picked.has(c.id));

  const current = clips[focus];
  useHotkeys({
    j: () => setFocus((i) => Math.min(clips.length - 1, i + 1)),
    k: () => setFocus((i) => Math.max(0, i - 1)),
    Enter: () => current && open(current.id),
    c: () => current?.caption && void copyText(current.caption, "Caption"),
    l: () => current?.posts[0] && void copyText(current.posts[0].url, "Link"),
    p: () => current && setStatus(current, "posted"),
    x: () => {
      const ready = chosen.filter((c) => c.status === "ready");
      if (chosen.length) { if (ready.length) bulk.setStatus(ready, "skipped"); clear(); }
      else if (current) setStatus(current, "skipped");
    },
    d: () => current?.file_exists && window.location.assign(downloadUrl(current.id)),
    Delete: () => {
      if (chosen.length) { bulk.remove(chosen); clear(); }
      else if (current) remove(current);
    },
    Escape: () => clear(),
  });

  return (
    <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
        {clips.map((clip, i) => (
          <ClipCard key={clip.id} clip={clip} showCampaign={showCampaign} focused={i === focus}
                    selected={picked.has(clip.id)} selecting={picked.size > 0} onSelect={(e) => pick(i, e)} />
        ))}
      </div>
      <SelectionBar clips={chosen} onClear={clear} />
    </>
  );
}
