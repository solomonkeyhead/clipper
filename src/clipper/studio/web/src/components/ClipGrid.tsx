import { useEffect, useRef, useState } from "react";
import type { Clip } from "@/api/client";
import { useUI } from "@/lib/store";
import { ClipCard, SelectionBar } from "./clips";

/** A grid of clips. Pick several with the corner box, Ctrl-click or Shift-click to act on all of them. */
export function ClipGrid({ clips, showCampaign = false }: { clips: Clip[]; showCampaign?: boolean }) {
  const [picked, setPicked] = useState<ReadonlySet<number>>(new Set());
  const anchor = useRef<number | null>(null);
  const setListIds = useUI((s) => s.setListIds);

  useEffect(() => setListIds(clips.map((c) => c.id)), [clips, setListIds]);
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

  return (
    <>
      {/* Two a row on a phone (D198): one filled the screen. */}
      <div data-wall className="grid grid-cols-2 gap-x-3 gap-y-6 sm:grid-cols-[repeat(auto-fill,minmax(184px,1fr))] sm:gap-x-5 sm:gap-y-8">
        {clips.map((clip, i) => (
          <ClipCard key={clip.id} clip={clip} showCampaign={showCampaign}
                    selected={picked.has(clip.id)} selecting={picked.size > 0} onSelect={(e) => pick(i, e)} />
        ))}
      </div>
      <SelectionBar clips={chosen} onClear={clear} />
    </>
  );
}
