import * as Dialog from "@radix-ui/react-dialog";
import { ExternalLink, FolderOpen, Info, SkipForward, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  revealClip, useClips, useSetClipStatus, useSetNote, type Clip, type ClipStatus, type Post,
} from "@/api/client";
import { useHotkeys } from "@/lib/hotkeys";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, ago, cn, copyText, formatCount, formatDuration, formatMoney } from "@/lib/utils";
import { PlatformIcon } from "./PlatformIcon";
import { Button, Chip, CopyButton, Kbd, StatusChip, Tip } from "./ui";

const STATUS_WORD: Record<ClipStatus, string> = {
  ready: "ready to post", posted: "posted", submitted: "submitted", skipped: "skipped",
};

/** Change a clip's status with a 5-second undo instead of a confirmation. */
export function useStatusWithUndo() {
  const mutation = useSetClipStatus();
  return (clip: Clip, status: ClipStatus) => {
    const previous = clip.marked as ClipStatus;
    if (previous === status) return;
    mutation.mutate({ id: clip.id, status });
    toast(`Marked ${STATUS_WORD[status]}`, {
      description: clip.title,
      duration: 5000,
      action: { label: "Undo", onClick: () => mutation.mutate({ id: clip.id, status: previous }) },
    });
  };
}

async function showFile(id: number) {
  try {
    await revealClip(id);
    toast("Opened in File Explorer", { description: "Drag it into TikTok or Instagram to upload." });
  } catch (e) {
    toast.error((e as Error).message);
  }
}

/* ---------- Thumbnail with hover preview ---------- */

function Preview({ clip, className }: { clip: Clip; className?: string }) {
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  return (
    <div
      className={cn("relative aspect-[9/16] overflow-hidden rounded-md bg-black", className)}
      onPointerEnter={() => { timer.current = window.setTimeout(() => setPlaying(true), 400); }}
      onPointerLeave={() => { window.clearTimeout(timer.current); setPlaying(false); }}
    >
      {clip.file_exists ? (
        <img src={clip.thumb} alt="" loading="lazy" decoding="async"
             className="absolute inset-0 size-full object-cover" />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-xs text-subtle">File missing</div>
      )}
      {playing && clip.file_exists && (
        <video src={clip.video} muted autoPlay playsInline loop
               className="fade-in absolute inset-0 size-full object-cover" />
      )}
      {clip.duration_s ? (
        <span className="tabular absolute right-1.5 bottom-1.5 rounded-[4px] bg-black/70 px-1.5 py-px text-[11px] font-medium text-white">
          {formatDuration(clip.duration_s)}
        </span>
      ) : null}
    </div>
  );
}

/* ---------- One copy-link button per post ---------- */

export function LinkButtons({ posts, size = "sm", withLabel = true }: {
  posts: Post[]; size?: "sm" | "md"; withLabel?: boolean;
}) {
  return (
    <>
      {posts.map((p) => (
        <Tip key={p.url} label={`Copy ${PLATFORM_NAME[p.platform] ?? p.platform} link`}>
          <Button
            size={size}
            variant="secondary"
            aria-label={`Copy ${PLATFORM_NAME[p.platform] ?? p.platform} link`}
            onClick={(e) => { e.stopPropagation(); void copyText(p.url, `${PLATFORM_NAME[p.platform] ?? p.platform} link`); }}
          >
            <PlatformIcon platform={p.platform} className="size-3.5" />
            {withLabel && "Link"}
          </Button>
        </Tip>
      ))}
    </>
  );
}

function bestViews(clip: Clip) {
  const views = clip.posts.reduce((sum, p) => sum + (p.views ?? 0), 0);
  const best = clip.posts.reduce<number | null>((m, p) =>
    p.x_median !== null && p.x_median !== undefined && (m === null || p.x_median > m) ? p.x_median : m, null);
  return { views, best };
}

/* ---------- Card ---------- */

export function ClipCard({ clip, showCampaign = false, focused = false }: {
  clip: Clip; showCampaign?: boolean; focused?: boolean;
}) {
  const open = useUI((s) => s.setOpenClip);
  const { views, best } = bestViews(clip);
  return (
    <article
      data-clip={clip.id}
      tabIndex={0}
      onClick={() => open(clip.id)}
      onKeyDown={(e) => e.key === "Enter" && open(clip.id)}
      className={cn(
        "group flex cursor-pointer flex-col gap-3 rounded-lg border border-line bg-surface-1 p-3 shadow-1 outline-none",
        "transition-[border-color,background-color] duration-[var(--dur-base)] hover:border-line-strong hover:bg-surface-2",
        focused && "border-accent ring-1 ring-accent",
      )}
    >
      <Preview clip={clip} />
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <StatusChip status={clip.status} />
          {clip.posts.length > 0 && (
            <span className="tabular text-xs text-muted">
              {formatCount(views)} views
              {best !== null && best >= 1.5 && <span className="ml-1 text-money">{best}×</span>}
            </span>
          )}
        </div>
        <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{clip.title}</h3>
        <div className="truncate text-xs text-muted" title={`${clip.campaign} · ${clip.source_title}`}>
          {showCampaign ? clip.campaign : clip.source_title}
        </div>
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-1.5">
        {clip.caption && <CopyButton text={clip.caption} what="Caption" label="Caption" />}
        <LinkButtons posts={clip.posts} />
      </div>
    </article>
  );
}

/* ---------- Detail sheet ---------- */

function PostStats({ post }: { post: Post }) {
  const na = (why: string) => (
    <Tip label={why}><span className="text-subtle">n/a</span></Tip>
  );
  const tiktokNa = "TikTok's API doesn't provide this";
  return (
    <div className="rounded-md border border-line bg-surface-2 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium">
          <PlatformIcon platform={post.platform} />
          {PLATFORM_NAME[post.platform] ?? post.platform}
          {post.account && <span className="text-muted">@{post.account}</span>}
        </span>
        <span className="flex items-center gap-1.5">
          <CopyButton text={post.url} what={`${PLATFORM_NAME[post.platform] ?? post.platform} link`} label="Copy link" />
          <Tip label="Open the post">
            <a href={post.url} target="_blank" rel="noopener noreferrer"
               className="grid size-7 place-items-center rounded-sm text-muted hover:bg-surface-3 hover:text-fg"
               aria-label="Open the post">
              <ExternalLink className="size-3.5" />
            </a>
          </Tip>
        </span>
      </div>
      <dl className="tabular grid grid-cols-3 gap-x-3 gap-y-2 text-sm sm:grid-cols-4">
        <Stat label="Views" value={formatCount(post.views)} extra={post.x_median ? `${post.x_median}× median` : undefined} />
        <Stat label="Avg watch" value={post.avg_watch_s !== null && post.avg_watch_s !== undefined ? `${post.avg_watch_s}s` : na(tiktokNa)} />
        <Stat label="Skipped in 3s" value={post.skip_rate_pct !== null && post.skip_rate_pct !== undefined ? `${post.skip_rate_pct}%` : na(post.platform === "tiktok" ? tiktokNa : "Not reported yet")} />
        <Stat label="Est. earnings" value={post.est_earnings !== null && post.est_earnings !== undefined ? <span className="text-money">{formatMoney(post.est_earnings)}</span> : na("Set the campaign's pay rate to estimate")} />
        <Stat label="Likes" value={formatCount(post.likes)} />
        <Stat label="Comments" value={formatCount(post.comments)} />
        <Stat label="Shares" value={formatCount(post.shares)} />
        <Stat label="Saves" value={post.saves !== null && post.saves !== undefined ? formatCount(post.saves) : na(tiktokNa)} />
      </dl>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>Posted {ago(post.posted_at)}</span>
        {post.settling && <Chip tone="warning">Stats still settling (Instagram reports up to 48 h late)</Chip>}
        {post.submitted_at && <Chip tone="success">Submitted</Chip>}
      </div>
    </div>
  );
}

const Stat = ({ label, value, extra }: { label: string; value: React.ReactNode; extra?: string }) => (
  <div className="flex flex-col">
    <dt className="text-xs text-muted">{label}</dt>
    <dd className="font-medium">{value}{extra && <span className="ml-1 text-xs text-money">{extra}</span>}</dd>
  </div>
);

export function ClipSheet() {
  const { data: clips = [] } = useClips();
  const openId = useUI((s) => s.openClip);
  const listIds = useUI((s) => s.listIds);
  const setOpen = useUI((s) => s.setOpenClip);
  const clip = clips.find((c) => c.id === openId) ?? null;
  const setStatus = useStatusWithUndo();
  const setNote = useSetNote();
  const [note, setNoteText] = useState("");
  useEffect(() => setNoteText(clip?.notes ?? ""), [clip?.id, clip?.notes]);

  const go = (step: number) => {
    const order = listIds.length ? listIds : clips.map((c) => c.id);
    const next = order[order.indexOf(openId ?? -1) + step];
    if (next !== undefined) setOpen(next);
  };

  useHotkeys({
    j: () => go(1),
    k: () => go(-1),
    c: () => clip?.caption && void copyText(clip.caption, "Caption"),
    l: () => clip?.posts[0] && void copyText(clip.posts[0].url, "Link"),
    p: () => clip && setStatus(clip, "posted"),
    x: () => clip && setStatus(clip, "skipped"),
    r: () => clip && setStatus(clip, "ready"),
    f: () => clip && void showFile(clip.id),
  }, { enabled: clip !== null, inDialog: true });

  return (
    <Dialog.Root open={clip !== null} onOpenChange={(o) => !o && setOpen(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade-in fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[860px] flex-col overflow-y-auto border-l border-line bg-bg shadow-3 outline-none data-[state=open]:animate-[sheet-in_var(--dur-panel)_var(--ease-decelerate)]"
        >
          {clip && (
            <div className="flex flex-col gap-5 p-5 md:flex-row">
              <div className="flex shrink-0 flex-col gap-3 md:w-[300px]">
                {clip.file_exists ? (
                  <video key={clip.id} src={clip.video} poster={clip.thumb} controls playsInline preload="metadata"
                         className="aspect-[9/16] max-h-[78vh] w-full rounded-lg bg-black object-contain" />
                ) : (
                  <div className="grid aspect-[9/16] place-items-center rounded-lg bg-surface-2 text-sm text-muted">File missing</div>
                )}
                <Button variant="secondary" onClick={() => void showFile(clip.id)}>
                  <FolderOpen className="size-4" /> Show file <Kbd className="ml-auto">F</Kbd>
                </Button>
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <StatusChip status={clip.status} />
                      <span className="text-xs text-muted">{clip.campaign} · {clip.source_title} · {formatDuration(clip.duration_s)}</span>
                    </div>
                    <Dialog.Title className="text-lg font-semibold">{clip.title}</Dialog.Title>
                    {clip.hook && clip.hook !== clip.title && (
                      <p className="mt-1 text-sm text-muted">On screen: {clip.hook}</p>
                    )}
                  </div>
                  <Dialog.Close asChild>
                    <Button variant="ghost" size="icon" aria-label="Close"><X className="size-4" /></Button>
                  </Dialog.Close>
                </div>

                <div className="flex flex-wrap gap-2">
                  {clip.status === "ready" || clip.status === "skipped" ? (
                    <Button variant="primary" onClick={() => setStatus(clip, "posted")}>
                      <Upload className="size-4" /> Mark posted <Kbd className="border-white/30 bg-white/10 text-white">P</Kbd>
                    </Button>
                  ) : (
                    <Button variant="secondary" onClick={() => setStatus(clip, "ready")}>Back to ready <Kbd>R</Kbd></Button>
                  )}
                  {clip.status !== "skipped" && (
                    <Button variant="ghost" onClick={() => setStatus(clip, "skipped")}>
                      <SkipForward className="size-4" /> Skip <Kbd>X</Kbd>
                    </Button>
                  )}
                </div>

                {clip.caption && (
                  <section>
                    <div className="mb-1.5 flex items-center justify-between">
                      <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">Caption</h3>
                      <CopyButton text={clip.caption} what="Caption" label="Copy caption" keys="C" />
                    </div>
                    <p className="max-h-56 overflow-auto rounded-md border border-line bg-surface-1 p-3 text-sm whitespace-pre-wrap">
                      {clip.caption}
                    </p>
                  </section>
                )}

                <section>
                  <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Posts</h3>
                  {clip.posts.length ? (
                    <div className="flex flex-col gap-2">{clip.posts.map((p) => <PostStats key={p.url} post={p} />)}</div>
                  ) : (
                    <p className="flex items-center gap-2 rounded-md border border-dashed border-line p-3 text-sm text-muted">
                      <Info className="size-4 shrink-0" />
                      Not posted yet. Once it's up with this caption, the next sync links it here automatically.
                    </p>
                  )}
                </section>

                <section>
                  <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Note</h3>
                  <textarea
                    value={note}
                    onChange={(e) => setNoteText(e.target.value)}
                    onBlur={() => note !== clip.notes && setNote.mutate({ id: clip.id, notes: note },
                      { onSuccess: () => toast.success("Note saved", { duration: 1500 }) })}
                    placeholder="Add a note…"
                    rows={3}
                    className="w-full resize-y rounded-md border border-line bg-surface-1 p-3 text-sm placeholder:text-subtle focus:border-accent focus:outline-none"
                  />
                </section>

                <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-subtle">
                  <span><Kbd>J</Kbd> <Kbd>K</Kbd> next / previous</span>
                  <span><Kbd>L</Kbd> copy link</span>
                  <span><Kbd>Esc</Kbd> close</span>
                </p>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
