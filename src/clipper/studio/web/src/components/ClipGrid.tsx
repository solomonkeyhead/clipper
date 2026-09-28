import { useEffect, useState } from "react";
import type { Clip } from "@/api/client";
import { useHotkeys } from "@/lib/hotkeys";
import { useUI } from "@/lib/store";
import { copyText } from "@/lib/utils";
import { ClipCard, useStatusWithUndo } from "./clips";

/** A grid of clips you can drive from the keyboard: J/K, Enter, C, L, P, X. */
export function ClipGrid({ clips, showCampaign = false }: { clips: Clip[]; showCampaign?: boolean }) {
  const [focus, setFocus] = useState(-1);
  const setListIds = useUI((s) => s.setListIds);
  const open = useUI((s) => s.setOpenClip);
  const setStatus = useStatusWithUndo();

  useEffect(() => setListIds(clips.map((c) => c.id)), [clips, setListIds]);
  useEffect(() => {
    if (focus < 0) return;
    document.querySelector<HTMLElement>(`[data-clip="${clips[focus]?.id}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focus, clips]);

  const current = clips[focus];
  useHotkeys({
    j: () => setFocus((i) => Math.min(clips.length - 1, i + 1)),
    k: () => setFocus((i) => Math.max(0, i - 1)),
    Enter: () => current && open(current.id),
    c: () => current?.caption && void copyText(current.caption, "Caption"),
    l: () => current?.posts[0] && void copyText(current.posts[0].url, "Link"),
    p: () => current && setStatus(current, "posted"),
    x: () => current && setStatus(current, "skipped"),
  });

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-4">
      {clips.map((clip, i) => (
        <ClipCard key={clip.id} clip={clip} showCampaign={showCampaign} focused={i === focus} />
      ))}
    </div>
  );
}
