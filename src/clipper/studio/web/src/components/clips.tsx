import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertTriangle, CheckCircle2, Download, ThumbsDown, ExternalLink, FileCheck2, FolderOpen, Info, Link2, Loader2, Send, SkipForward, Trash2, Undo2,
  Upload, X, XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  downloadUrl, markNotGood, proofUrl, revealClip, useAddPostLink, useCampaignTitle, useCampaigns, useClips, useDeleteClip, useRateClip, useSetClipStatus, useSetClipSubmitted, useSetNote,
  type Clip, type ClipStatus, type Post,
} from "@/api/client";
import { useHotkeys } from "@/lib/hotkeys";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, ago, cn, copyText, formatCount, formatDuration, formatMoney } from "@/lib/utils";
import { PlatformIcon } from "./PlatformIcon";
import { PostPanel } from "./posting";
import { RatingMark, RatingPanel, ScoreBadge, ScoreBreakdown } from "./scoring";
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

/** "Not good": skip a ready clip and teach Clipper from it, with a 5-second undo (D74). */
function NotGoodButton({ clip, size = "sm" }: { clip: Clip; size?: "sm" | "md" }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["clips"] });
  const mark = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const before = clip.marked;
    try {
      await markNotGood(clip.id);
      void refresh();
      toast("Marked not good", {
        description: "Skipped. Clipper will pick fewer clips like it.", duration: 5000,
        action: { label: "Undo", onClick: () => void markNotGood(clip.id, { status: before }).then(refresh) },
      });
    } catch (err) {
      toast.error((err as Error).message);
    }
  };
  const button = (
    <Button size={size} variant="ghost" onClick={(e) => void mark(e)} aria-label="Not good">
      <ThumbsDown className={size === "md" ? "size-4" : "size-3.5"} />{size === "md" && " Not good"}
    </Button>
  );
  return size === "md" ? button : <Tip label="Not good: skip it, and Clipper learns from it">{button}</Tip>;
}

/** Mark a clip (all its post links) submitted to its campaign, with a 5-second undo. */
function useSubmittedWithUndo() {
  const mutation = useSetClipSubmitted();
  return (clip: Clip, submitted = true) => {
    mutation.mutate({ id: clip.id, submitted });
    if (submitted) {
      toast("Marked submitted", {
        description: clip.title, duration: 5000,
        action: { label: "Undo", onClick: () => mutation.mutate({ id: clip.id, submitted: false }) },
      });
    }
  };
}

function SubmitButton({ clip, size = "sm" }: { clip: Clip; size?: "sm" | "md" }) {
  const submit = useSubmittedWithUndo();
  if (clip.status === "submitted") {
    return (
      <Tip label="Undo: back to not submitted">
        <Button size={size} variant="ghost" onClick={(e) => { e.stopPropagation(); submit(clip, false); }}>
          <Undo2 className="size-3.5" /> Unmark submitted
        </Button>
      </Tip>
    );
  }
  return (
    <Tip label="You've pasted its link into the campaign's submission form" keys="S">
      <Button size={size} variant="primary" onClick={(e) => { e.stopPropagation(); submit(clip); }}>
        <CheckCircle2 className="size-3.5" /> Mark submitted
      </Button>
    </Tip>
  );
}

/** The campaign's own page, where post links are submitted. */
function useCampaignUrl() {
  const { data: campaigns = [] } = useCampaigns();
  const urls = new Map(campaigns.map((c) => [c.name, c.campaign_url]));
  return (name: string) => urls.get(name) || "";
}

/** Copy the post's link and open the campaign's page to paste it into, in one click. */
function SubmitLinkButton({ post, campaignUrl, size = "sm", label = "Copy link & submit" }: {
  post: Post; campaignUrl: string; size?: "sm" | "md"; label?: string;
}) {
  if (!campaignUrl) return null;
  return (
    <Tip label="Copies the post's link and opens the campaign page to paste it">
      <Button size={size} variant="secondary" onClick={(e) => {
        e.stopPropagation();
        void copyText(post.url, `${PLATFORM_NAME[post.platform] ?? post.platform} link`);
        window.open(campaignUrl, "_blank", "noopener");
      }}>
        <Send className="size-3.5" /> {label}
      </Button>
    </Tip>
  );
}

function PasteLink({ clip, compact = false }: { clip: Clip; compact?: boolean }) {
  const add = useAddPostLink();
  const [open, setOpen] = useState(!compact);
  const [url, setUrl] = useState("");
  if (!open) {
    return <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><Link2 className="size-3.5" /> Add a post link</Button>;
  }
  return (
    <form className="flex gap-2" onSubmit={(e) => {
      e.preventDefault();
      add.mutate({ id: clip.id, url }, {
        onSuccess: (res) => { setUrl(""); if (compact) setOpen(false); toast.success(`${PLATFORM_NAME[(res as { platform: string }).platform] ?? "Post"} link added`, { description: "Its stats update on the next sync." }); },
        onError: (err) => toast.error((err as Error).message),
      });
    }}>
      <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste the post's link (TikTok, Instagram or YouTube)"
             aria-label="Post link" className="h-9 flex-1 rounded-sm border border-line bg-surface-1 px-3 text-sm placeholder:text-subtle focus:border-accent focus:outline-none" />
      <Button type="submit" variant="secondary" disabled={!url.trim() || add.isPending}>Add</Button>
    </form>
  );
}

/** Already posted: the clips this one repeats, and where they're up. */
function DuplicateWarning({ clip }: { clip: Clip }) {
  const open = useUI((s) => s.setOpenClip);
  if (!clip.duplicates.length || clip.status === "skipped") return null;
  const accounts = [...new Set(clip.duplicates.flatMap((d) => d.posted_on))];
  return (
    <div className="flex gap-2.5 rounded-md border border-warning/40 bg-[color-mix(in_oklch,var(--warning)_8%,transparent)] p-3 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
      <div className="flex flex-col gap-1">
        <p><b>Already posted.</b> This repeats {clip.duplicates.length === 1 ? "a clip" : `${clip.duplicates.length} clips`} you've put up on {accounts.join(", ")}:</p>
        <ul className="flex flex-col">
          {clip.duplicates.map((d) => (
            <li key={d.id}>
              <button className="text-left text-accent hover:underline" onClick={() => open(d.id)}>“{d.title}”</button>
              <span className="text-muted"> · {d.how}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted">
          Posting it again on the same account breaks Vyro's rules, and Instagram stops recommending accounts that repeat
          themselves. Post it on a different account, or skip it.
        </p>
      </div>
    </div>
  );
}

function ProofPanel({ clip }: { clip: Clip }) {
  const proof = clip.proof;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-line bg-surface-1 p-3">
      <ul className="flex flex-col gap-1 text-sm">
        <li className="flex gap-2">
          <FileCheck2 className="mt-0.5 size-4 shrink-0 text-success" />
          <span>
            Campaign rules saved {proof?.saved_at ? ago(proof.saved_at) : "—"}
            {proof?.late && <span className="text-muted"> (from the campaign's rules then; this clip was made before Clipper saved them)</span>}
          </span>
        </li>
        {proof && proof.total > 0 && (
          <li className="flex gap-2">
            {proof.passed === proof.total ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />}
            <span>{proof.passed} of {proof.total} brief checks passed when it was made</span>
          </li>
        )}
        <li className="flex gap-2">
          {proof?.posted_ok === true ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
            : proof?.posted_ok === false ? <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />
            : <Info className="mt-0.5 size-4 shrink-0 text-subtle" />}
          <span className={cn(proof?.posted_ok == null && "text-muted")}>
            {proof?.posted_ok === true ? "The caption as posted meets the brief"
              : proof?.posted_ok === false ? "The caption as posted is missing something the brief requires (see the pack)"
              : clip.posts.length ? "The caption as posted is checked on the next sync" : "The caption as posted is checked once the post is found"}
          </span>
        </li>
      </ul>
      <a href={proofUrl(clip.id)} download
         className="inline-flex h-8 w-fit items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-3 text-xs font-medium hover:bg-surface-3">
        <Download className="size-3.5" /> Download proof pack
      </a>
      <p className="text-xs text-subtle">If a campaign rejects this clip after it gets views, send them the pack: their brief as it was, the checks, the posts and their views.</p>
    </div>
  );
}

async function showFile(id: number) {
  try {
    await revealClip(id);
    toast("Opened in File Explorer", { description: "Drag it into TikTok or Instagram to upload." });
  } catch (e) {
    toast.error((e as Error).message);
  }
}

/** Delete to the trash at once, with a 5-second Undo instead of "are you sure?". */
export function useDeleteWithUndo() {
  const mutation = useDeleteClip();
  const setOpen = useUI((s) => s.setOpenClip);
  return (clip: Clip) => {
    mutation.mutate({ id: clip.id });
    setOpen(null);
    toast("Clip deleted", {
      description: `${clip.title} · kept in the trash for 30 days`,
      duration: 5000,
      action: { label: "Undo", onClick: () => mutation.mutate({ id: clip.id, restore: true }) },
    });
  };
}

function DownloadButton({ clip, label = true, size = "sm" }: {
  clip: Clip; label?: boolean; size?: "sm" | "md";
}) {
  return (
    <Tip label="Download the video" keys="D">
      <a
        href={downloadUrl(clip.id)}
        download
        onClick={(e) => e.stopPropagation()}
        aria-label="Download the video"
        className={cn(
          "inline-flex items-center justify-center gap-1.5 rounded-sm border border-line bg-surface-2 font-medium text-fg",
          "transition-colors duration-[var(--dur-fast)] hover:border-line-strong hover:bg-surface-3",
          size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm",
        )}
      >
        <Download className="size-3.5" />{label && "Download"}
      </a>
    </Tip>
  );
}

function DeleteButton({ clip, label = false, size = "sm" }: {
  clip: Clip; label?: boolean; size?: "sm" | "md";
}) {
  const remove = useDeleteWithUndo();
  return (
    <Tip label="Delete (you can undo)" keys="Del">
      <Button size={label ? size : "icon"} variant="ghost"
              className={cn(!label && "size-7", "hover:text-danger")}
              aria-label="Delete clip"
              onClick={(e) => { e.stopPropagation(); remove(clip); }}>
        <Trash2 className="size-3.5" />{label && "Delete"}
      </Button>
    </Tip>
  );
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

function LinkButtons({ posts, size = "sm", withLabel = true }: {
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
  const title = useCampaignTitle();
  const campaignUrl = useCampaignUrl()(clip.campaign);
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
          <span className="flex items-center gap-1.5">
            <StatusChip status={clip.status} />
            {clip.duplicates.length > 0 && clip.status === "ready" && (
              <Tip label={`Already posted: repeats “${clip.duplicates[0].title}” (${clip.duplicates[0].posted_on.join(", ")})`}>
                <span aria-label="Already posted" className="text-warning"><AlertTriangle className="size-3.5" /></span>
              </Tip>
            )}
            <ScoreBadge clip={clip} />
            <RatingMark rating={clip.rating} />
          </span>
          {clip.posts.length > 0 && (
            <span className="tabular text-xs text-muted">
              {formatCount(views)} views
              {best !== null && best >= 1.5 && <span className="ml-1 text-money">{best}×</span>}
            </span>
          )}
        </div>
        <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{clip.title}</h3>
        <div className="truncate text-xs text-muted" title={`${title(clip.campaign)} · ${clip.source_title}`}>
          {showCampaign ? title(clip.campaign) : clip.source_title}
        </div>
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-1.5">
        {clip.status === "posted" && clip.posts.length === 0 && clip.watching ? (
          <span className="flex items-center gap-1.5 text-xs text-muted"><Loader2 className="size-3.5 animate-spin" /> Finding your post…</span>
        ) : clip.status === "posted" ? (
          <>
            {clip.posts.length === 1 && campaignUrl
              ? <SubmitLinkButton post={clip.posts[0]} campaignUrl={campaignUrl} label="Submit" />
              : <LinkButtons posts={clip.posts} withLabel={clip.posts.length < 2} />}
            <SubmitButton clip={clip} />
          </>
        ) : clip.status === "submitted" ? (
          <LinkButtons posts={clip.posts} />
        ) : (
          <>
            {clip.caption && <CopyButton text={clip.caption} what="Caption" label="Caption" />}
            {clip.file_exists && <DownloadButton clip={clip} />}
            {clip.status === "ready" && <NotGoodButton clip={clip} />}
          </>
        )}
        <span className="ml-auto"><DeleteButton clip={clip} /></span>
      </div>
    </article>
  );
}

/* ---------- Detail sheet ---------- */

function PostStats({ post, campaignUrl }: { post: Post; campaignUrl: string }) {
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
          {!post.submitted_at && <SubmitLinkButton post={post} campaignUrl={campaignUrl} />}
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
  const submit = useSubmittedWithUndo();
  const rate = useRateClip();
  const title = useCampaignTitle();
  const campaignUrlOf = useCampaignUrl();
  const campaignUrl = clip ? campaignUrlOf(clip.campaign) : "";
  const remove = useDeleteWithUndo();
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
    s: () => clip && clip.status !== "submitted" && submit(clip),
    ...Object.fromEntries([1, 2, 3, 4, 5].map((n) => [String(n), () =>
      clip && rate.mutate({ id: clip.id, rating: clip.rating === n ? null : n, reasons: clip.rating === n ? [] : clip.reasons })])),
    f: () => clip && void showFile(clip.id),
    d: () => clip?.file_exists && window.location.assign(downloadUrl(clip.id)),
    Delete: () => clip && remove(clip),
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
                {clip.file_exists && (
                  <a href={downloadUrl(clip.id)} download
                     className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
                    <Download className="size-4" /> Download video <Kbd className="ml-auto border-white/30 bg-white/10 text-white">D</Kbd>
                  </a>
                )}
                <div className="flex gap-2">
                  <Button variant="secondary" className="flex-1" onClick={() => void showFile(clip.id)}>
                    <FolderOpen className="size-4" /> Show in folder
                  </Button>
                  <DeleteButton clip={clip} label size="md" />
                </div>
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <StatusChip status={clip.status} />
                      <span className="text-xs text-muted">{title(clip.campaign)} · {clip.source_title} · {formatDuration(clip.duration_s)}</span>
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

                <DuplicateWarning clip={clip} />

                <div className="flex flex-wrap gap-2">
                  {(clip.status === "posted" || clip.status === "submitted") && <SubmitButton clip={clip} size="md" />}
                  {clip.status === "ready" || clip.status === "skipped" ? (
                    <Button variant="primary" onClick={() => setStatus(clip, "posted")}>
                      <Upload className="size-4" /> Mark posted <Kbd className="border-white/30 bg-white/10 text-white">P</Kbd>
                    </Button>
                  ) : clip.posts.length === 0 && (
                    <Button variant="ghost" onClick={() => setStatus(clip, "ready")}>Back to ready <Kbd>R</Kbd></Button>
                  )}
                  {clip.status !== "skipped" && (
                    <Button variant="ghost" onClick={() => setStatus(clip, "skipped")}>
                      <SkipForward className="size-4" /> Skip <Kbd>X</Kbd>
                    </Button>
                  )}
                  {clip.status === "ready" && <NotGoodButton clip={clip} size="md" />}
                </div>

                {clip.status === "ready" && <PostPanel clip={clip} />}

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
                  <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Your rating</h3>
                  <RatingPanel clip={clip} />
                </section>

                <section>
                  <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Clipper's score</h3>
                  <ScoreBreakdown clip={clip} />
                </section>

                <section>
                  <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Posts</h3>
                  {clip.posts.length ? (
                    <div className="flex flex-col gap-2">
                      {clip.posts.map((p) => <PostStats key={p.url} post={p} campaignUrl={campaignUrl} />)}
                      <PasteLink clip={clip} compact />
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2 rounded-md border border-dashed border-line p-3">
                      <p className="flex items-center gap-2 text-sm text-muted">
                        {clip.watching ? <Loader2 className="size-4 shrink-0 animate-spin text-accent" /> : <Info className="size-4 shrink-0" />}
                        {clip.watching
                          ? "Looking for your post every 2 minutes for the next half hour, so you can submit it fast."
                          : "Not posted yet. Press Mark posted once it's up and Clipper finds it within minutes, or paste its link."}
                      </p>
                      <PasteLink clip={clip} />
                    </div>
                  )}
                </section>

                <section>
                  <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Proof for disputes</h3>
                  <ProofPanel clip={clip} />
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
                  <span><Kbd>1</Kbd>–<Kbd>5</Kbd> rate</span>
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
