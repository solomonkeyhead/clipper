import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { useCampaignTitle, type Clip } from "@/api/client";
import { useUI } from "@/lib/store";
import { cn, formatDuration } from "@/lib/utils";
import { FadeImg } from "./progress";

/* The wall (D215): clips as 9:16 posters in rows by status, like a streaming catalog. The poster in focus
   (hover, keyboard, or the next one to post) takes the amber ring and its row steps back; styles.css has it. */

const NEXT: Record<string, string> = { ready: "Open to post", posted: "Open to submit", submitted: "See its views" };

/** A clip as a poster: picture first, its title and campaign under it. A click opens the clip. */
export function PosterTile({ clip, focus = false, className }: { clip: Clip; focus?: boolean; className?: string }) {
  const open = useUI((s) => s.setOpenClip);
  const title = useCampaignTitle();
  return (
    <button type="button" data-tile data-focus={focus || undefined} onClick={() => open(clip.id)}
            className={cn("group flex w-[148px] shrink-0 flex-col gap-2 text-left outline-none sm:w-[164px]", className)}>
      <Poster clip={clip} />
      <span className="flex min-w-0 flex-col gap-0.5 px-0.5">
        <span className="line-clamp-2 text-sm leading-snug font-semibold group-hover:text-fg">{clip.title || clip.hook || `Clip ${clip.id}`}</span>
        <span className="truncate text-xs text-muted">{title(clip.campaign)}</span>
        {/* The tile in focus says what a click does (D215); the line is always there so nothing below moves. */}
        <span className="tile-more inline-flex items-center gap-1 text-xs font-semibold text-fg" aria-hidden>
          {NEXT[clip.status] ?? "Open"} <ArrowRight className="size-3" />
        </span>
      </span>
    </button>
  );
}

/** The picture of a poster: the clip's frame, its score top left, its length bottom right. */
export function Poster({ clip, className }: { clip: Clip; className?: string }) {
  return (
    <span className={cn("poster block", className)}>
      {clip.file_exists
        ? <FadeImg src={clip.thumb} alt="" className="absolute inset-0 size-full object-cover" />
        : <span className="absolute inset-0 grid place-items-center px-3 text-center text-xs text-subtle">File missing</span>}
      {clip.score != null && (
        <span className="num absolute top-2 left-2 rounded-[3px] bg-black/70 px-1.5 text-[15px] leading-6 text-white">{clip.score.toFixed(1)}</span>
      )}
      {clip.duration_s ? (
        <span className="tabular absolute right-2 bottom-2 rounded-[3px] bg-black/70 px-1.5 text-[12px] leading-5 font-semibold text-white">
          {formatDuration(clip.duration_s)}
        </span>
      ) : null}
    </span>
  );
}

/** A row of the wall: its name, how many, a way to all of them, and the posters scrolling sideways.
 *  An empty row says so in one line with the step that fills it (D209), not a box of dashes. */
export function WallRow({ title, count, to, search, empty, children }: {
  title: string; count: number; to: string; search?: Record<string, string>; empty?: ReactNode; children?: ReactNode;
}) {
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <div className="flex items-baseline gap-3">
        <h2 className="row-head">{title}</h2>
        <span className="tabular text-sm font-semibold text-muted">{count}</span>
        {count > 0 && (
          <Link to={to} search={search}
                className="ml-auto inline-flex min-h-8 items-center gap-1 text-xs font-semibold text-muted hover:text-fg pointer-coarse:min-h-11">
            See all <ArrowRight className="size-3.5" />
          </Link>
        )}
      </div>
      {count === 0 ? <div className="py-1 text-sm text-muted">{empty}</div> : <div className="wall-row">{children}</div>}
    </section>
  );
}
