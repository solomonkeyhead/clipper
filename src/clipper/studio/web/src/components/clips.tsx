import * as Dialog from "@radix-ui/react-dialog";
import {
  AlertTriangle, Check, CheckCircle2, Download, ThumbsDown, ThumbsUp, ExternalLink, FileCheck2, FolderOpen, Info, Loader2, Send, SkipForward, Trash2, Undo2,
  Lock, Pencil, Scissors, ShieldAlert, Upload, X, XCircle,
} from "lucide-react";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  downloadUrl, useStatus, markNotGood, proofUrl, revealClip, useCampaignTitle, usePostHistory, useSetTask, useCampaigns, useClips, useDeleteClip, useRateClip, useSetClipStatus, useEditCaption, useSetClipSubmitted, useSetNote,
  type Clip, type ClipStatus, type Post,
} from "@/api/client";
import { useHotkeys } from "@/lib/hotkeys";
import { useUI } from "@/lib/store";
import { NO_STATS } from "@/api/platforms.gen";
import { PLATFORM_NAME, ago, cn, copyText, formatCount, formatDuration, formatMoney, openTab } from "@/lib/utils";
import { PlatformIcon } from "./PlatformIcon";
import { CaptionChoice, HookControl, usePaid } from "./lines";
import { PasteLink, PostPanel } from "./posting";
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

/** "Not good": skip a ready clip and teach Clipper from it, with a 5-second undo (D74).
 *  The card's button and the open clip's B key both do this. */
export function useNotGood() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["clips"] });
  return async (clip: Clip) => {
    const before = clip.marked;
    try {
      await markNotGood(clip.id);
      void refresh();
      toast("Marked not good", {
        description: "Skipped. Say why, and what was good about it, so Clipper learns the right thing.", duration: 6000,
        action: { label: "Say why", onClick: () => useUI.getState().setOpenClip(clip.id) },
        cancel: { label: "Undo", onClick: () => void markNotGood(clip.id, { status: before }).then(refresh) },
      });
    } catch (err) {
      toast.error((err as Error).message);
    }
  };
}

/** Whether a campaign is your own channel (no submitting links there: its clips are just "Posted"). */
function useIsOwn() {
  const { data: campaigns = [] } = useCampaigns();
  const own = new Set(campaigns.filter((c) => c.submits === false).map((c) => c.name));
  return (name: string) => own.has(name);
}

/** Good, from the card itself (D143): the same rating the sheet's Good button and Y give. */
function GoodButton({ clip }: { clip: Clip }) {
  const rate = useRateClip();
  const good = (clip.rating ?? 0) >= 4;
  return (
    <Tip label={good ? "Rated good (click to undo)" : "Good: Clipper learns what you like"}>
      <Button size="sm" variant="ghost" aria-label="Good" aria-pressed={good} className={cn(good && "text-success")}
              onClick={(e) => { e.stopPropagation(); rate.mutate({ id: clip.id, rating: good ? null : 5, reasons: clip.reasons }); }}>
        <ThumbsUp className="size-3.5" />
      </Button>
    </Tip>
  );
}

function NotGoodButton({ clip, size = "sm" }: { clip: Clip; size?: "sm" | "md" }) {
  const notGood = useNotGood();
  const button = (
    <Button size={size} variant="ghost" onClick={(e) => { e.stopPropagation(); void notGood(clip); }} aria-label="Not good">
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
        openTab(campaignUrl);
      }}>
        <Send className="size-3.5" /> {label}
      </Button>
    </Tip>
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

/** A clip the rule checks or the AI check found breaking its brief (D81). */
const breaksBrief = (clip: Clip) => Boolean(clip.rules && (clip.rules.failed.length || clip.rules.brief.length));

/** The stored caption; editable until the clip is posted, the brief's rules still applied. */
function CaptionSection({ clip }: { clip: Clip }) {
  const [draft, setDraft] = useState<string | null>(null);
  const edit = useEditCaption();
  const paid = usePaid();
  const editable = clip.status === "ready" || clip.status === "skipped";
  const save = () => {
    if (draft === null) return;
    edit.mutate({ id: clip.id, caption: draft }, {
      onSuccess: () => { setDraft(null); toast.success("Caption saved", { description: "Anything the brief requires is added back, and the AI checks it again." }); },
      onError: (e) => toast.error("Couldn't save the caption", { description: String(e.message ?? e) }),
    });
  };
  return (
    <section>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">Caption</h3>
        <span className="flex items-center gap-1.5">
          {draft === null && <CaptionChoice clip={clip} />}
          {editable && draft === null && (paid
            ? <Button size="sm" variant="ghost" onClick={() => setDraft(clip.caption)}><Pencil className="size-3.5" /> Edit</Button>
            : <Tip label="Writing your own caption is part of the paid plans"><span className="inline-flex items-center gap-1 text-xs text-subtle"><Lock className="size-3" /> Edit</span></Tip>)}
          {draft === null && !(clip.post_copy?.length && clip.status === "ready") &&
            <CopyButton text={clip.caption} what="Caption" label="Copy caption" keys="C" />}
        </span>
      </div>
      {draft === null ? (
        <p className="max-h-56 overflow-auto rounded-md border border-line bg-surface-1 p-3 text-sm whitespace-pre-wrap">
          {clip.caption}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={7} autoFocus
                    onKeyDown={(e) => e.stopPropagation()}
                    className="w-full rounded-md border border-line bg-surface-1 p-3 text-sm outline-none focus:border-accent" />
          <span className="flex gap-2">
            <Button size="sm" variant="primary" disabled={edit.isPending || !draft.trim()} onClick={save}>Save</Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>Cancel</Button>
          </span>
        </div>
      )}
    </section>
  );
}

/** A note: a link to add one, the box once there's something in it. */
function NoteField({ note, saved, onChange, onSave }: {
  note: string; saved: string; onChange: (v: string) => void; onSave: () => void;
}) {
  const [open, setOpen] = useState(Boolean(saved));
  if (!open) {
    return <button type="button" className="w-fit text-xs text-muted hover:text-fg" onClick={() => setOpen(true)}>+ Add a note</button>;
  }
  return (
    <textarea value={note} onChange={(e) => onChange(e.target.value)} onBlur={onSave} autoFocus={!saved}
      placeholder="Add a note…" rows={2} aria-label="Note"
      className="w-full resize-y rounded-md border border-line bg-surface-1 p-3 text-sm placeholder:text-subtle focus:border-accent focus:outline-none" />
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

/** What can be done to several picked clips at once, each with one undo for all.
 *  One at a time, so the server never handles a pile of writes together. */
export function useBulk() {
  const status = useSetClipStatus();
  const submitted = useSetClipSubmitted();
  const del = useDeleteClip();
  const qc = useQueryClient();
  const each = async (clips: Clip[], fn: (c: Clip) => Promise<unknown>) => {
    for (const c of clips) await fn(c).catch(() => undefined);
  };
  const done = (n: number, what: string, undo: () => void, description?: string) => toast(
    `${n} clip${n === 1 ? "" : "s"} ${what}`, { description, duration: 6000, action: { label: "Undo", onClick: undo } });
  return {
    setStatus: (clips: Clip[], to: ClipStatus) => {
      void each(clips, (c) => status.mutateAsync({ id: c.id, status: to }));
      done(clips.length, STATUS_WORD[to] === "ready to post" ? "back to ready" : STATUS_WORD[to],
        () => void each(clips, (c) => status.mutateAsync({ id: c.id, status: c.marked as ClipStatus })));
    },
    notGood: (clips: Clip[]) => {
      const refresh = () => qc.invalidateQueries({ queryKey: ["clips"] });
      void each(clips, (c) => markNotGood(c.id)).then(refresh);
      done(clips.length, "marked not good and skipped", () => void each(clips, (c) => markNotGood(c.id, { status: c.marked })).then(refresh),
        "Open one to say why, so Clipper learns the right thing");
    },
    submit: (clips: Clip[]) => {
      void each(clips, (c) => submitted.mutateAsync({ id: c.id, submitted: true }));
      done(clips.length, "marked submitted", () => void each(clips, (c) => submitted.mutateAsync({ id: c.id, submitted: false })));
    },
    remove: (clips: Clip[]) => {
      void each(clips, (c) => del.mutateAsync({ id: c.id }));
      done(clips.length, "deleted", () => void each(clips, (c) => del.mutateAsync({ id: c.id, restore: true })), "Kept in the trash for 30 days");
    },
  };
}

/** The bar that floats over a grid while clips are picked. */
export function SelectionBar({ clips, onClear }: { clips: Clip[]; onClear: () => void }) {
  const bulk = useBulk();
  if (!clips.length) return null;
  const ready = clips.filter((c) => c.status === "ready");
  const skipped = clips.filter((c) => c.status === "skipped");
  const posted = clips.filter((c) => c.status === "posted" && c.posts.length > 0);
  const act = (fn: () => void) => () => { fn(); onClear(); };
  const count = (n: number) => n < clips.length ? ` ${n}` : "";
  return (
    <div role="toolbar" aria-label="Picked clips"
      className="fade-in fixed bottom-6 left-1/2 z-30 flex -translate-x-1/2 items-center gap-1.5 rounded-xl border border-line-strong bg-surface-1 p-1.5 pl-4 shadow-3">
      <span className="tabular mr-2 text-sm font-medium whitespace-nowrap">{clips.length} selected</span>
      {ready.length > 0 && (
        <Button size="sm" variant="secondary" onClick={act(() => bulk.setStatus(ready, "posted"))}>
          <Upload className="size-3.5" /> Mark posted{count(ready.length)}
        </Button>
      )}
      {ready.length > 0 && (
        <Tip label="Skip them, and Clipper learns they weren't good">
          <Button size="sm" variant="secondary" onClick={act(() => bulk.notGood(ready))}>
            <ThumbsDown className="size-3.5" /> Not good{count(ready.length)}
          </Button>
        </Tip>
      )}
      {ready.length > 0 && (
        <Button size="sm" variant="secondary" onClick={act(() => bulk.setStatus(ready, "skipped"))}>
          <SkipForward className="size-3.5" /> Skip{count(ready.length)} <Kbd>X</Kbd>
        </Button>
      )}
      {skipped.length > 0 && (
        <Button size="sm" variant="secondary" onClick={act(() => bulk.setStatus(skipped, "ready"))}>
          <Undo2 className="size-3.5" /> Back to ready{count(skipped.length)}
        </Button>
      )}
      {posted.length > 0 && (
        <Tip label="You've pasted their links into the campaign's submission form">
          <Button size="sm" variant="secondary" onClick={act(() => bulk.submit(posted))}>
            <CheckCircle2 className="size-3.5" /> Mark submitted{count(posted.length)}
          </Button>
        </Tip>
      )}
      <Button size="sm" variant="danger" onClick={act(() => bulk.remove(clips))}>
        <Trash2 className="size-3.5" /> Delete <Kbd>Del</Kbd>
      </Button>
      <Tip label="Clear the selection" keys="Esc">
        <Button size="icon" variant="ghost" aria-label="Clear the selection" onClick={onClear}><X className="size-4" /></Button>
      </Tip>
    </div>
  );
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
        <div className="absolute inset-0 grid place-items-center px-2 text-center text-xs text-subtle">
          {clip.picked_by === "channel" ? "Already on your channel" : "File missing"}
        </div>
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

/** A skipped clip's way back, with the same 5-second undo as skipping. */
function BackToReady({ clip }: { clip: Clip }) {
  const setStatus = useStatusWithUndo();
  return (
    <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); setStatus(clip, "ready"); }}>
      <Undo2 className="size-3.5" /> Back to ready
    </Button>
  );
}

/* ---------- Card ---------- */

/** Posted, but the brief wants more views before the link is submitted (D154): how far it has to go. */
function WaitingForViews({ clip }: { clip: Clip }) {
  const need = clip.submit_at_views ?? 0;
  const best = Math.max(0, ...clip.posts.map((p) => p.views ?? 0));
  return (
    <Tip label={`The brief says to submit once a post passes ${need.toLocaleString()} views. Your best post here has ${best.toLocaleString()}.`}>
      <div className="flex flex-col gap-1">
        <div className="flex justify-between text-[11px] text-muted">
          <span>Submit at {formatCount(need)} views</span>
          <span className="tabular">best post {formatCount(best)}</span>
        </div>
        <div className="h-1 overflow-hidden rounded-full bg-surface-3">
          <div className="h-full rounded-full bg-warning" style={{ width: `${Math.min(100, (best / need) * 100)}%` }} />
        </div>
      </div>
    </Tip>
  );
}

export function ClipCard({ clip, showCampaign = false, focused = false, selected = false, selecting = false, onSelect }: {
  clip: Clip; showCampaign?: boolean; focused?: boolean;
  /** Picking several clips: once any is picked, a click picks instead of opening. */
  selected?: boolean; selecting?: boolean; onSelect?: (e: React.MouseEvent) => void;
}) {
  const open = useUI((s) => s.setOpenClip);
  const title = useCampaignTitle();
  const isOwn = useIsOwn();
  const { views, best } = bestViews(clip);
  return (
    <article
      data-clip={clip.id}
      tabIndex={0}
      onClick={(e) => onSelect && (selecting || e.shiftKey || e.ctrlKey || e.metaKey) ? onSelect(e) : open(clip.id)}
      onKeyDown={(e) => e.key === "Enter" && open(clip.id)}
      className={cn(
        "group relative flex cursor-pointer flex-col gap-3 rounded-lg border border-line bg-surface-1 p-3 shadow-1 outline-none",
        "transition-[border-color,background-color] duration-[var(--dur-base)] hover:border-line-strong hover:bg-surface-2",
        focused && "border-accent ring-1 ring-accent",
        selected && "border-accent bg-accent-soft hover:bg-accent-soft",
      )}
    >
      {onSelect && (
        <Tip label={selected ? "Unselect" : "Select (Shift-click for a range)"}>
          <button type="button" role="checkbox" aria-checked={selected} aria-label="Select"
            onClick={(e) => { e.stopPropagation(); onSelect(e); }}
            className={cn("absolute top-5 left-5 z-10 grid size-5 place-items-center rounded-[5px] border transition-opacity duration-[var(--dur-fast)]",
              selected ? "border-accent bg-accent text-accent-fg" : "border-white/70 bg-black/40 text-transparent hover:bg-black/60",
              selected || selecting ? "opacity-100" : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100")}>
            <Check className="size-3.5" strokeWidth={3} />
          </button>
        </Tip>
      )}
      <Preview clip={clip} />
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5">
            <StatusChip status={clip.status} own={isOwn(clip.campaign)} />
            {clip.duplicates.length > 0 && clip.status === "ready" && (
              <Tip label={`Already posted: repeats “${clip.duplicates[0].title}” (${clip.duplicates[0].posted_on.join(", ")})`}>
                <span aria-label="Already posted" className="text-warning"><AlertTriangle className="size-3.5" /></span>
              </Tip>
            )}
            {clip.status === "ready" && breaksBrief(clip) && (
              <Tip label={`Breaks the brief: ${[...(clip.rules?.failed ?? []), ...(clip.rules?.brief ?? []).map((p) => p.problem)].join("; ")}`}>
                <span aria-label="Breaks the brief" className="text-danger"><ShieldAlert className="size-4" /></span>
              </Tip>
            )}
            <ScoreBadge clip={clip} />
            <RatingMark rating={clip.rating} />
          </span>
          {clip.posts.length > 0 && (
            <span className="tabular text-xs text-muted">
              {formatCount(views)} view{views === 1 ? "" : "s"}
              {best !== null && best >= 1.5 && <span className="ml-1 text-money" title={`${best}× the median views for this campaign on that platform`}>{best}×</span>}
            </span>
          )}
        </div>
        <h3 className="line-clamp-2 text-sm leading-snug font-semibold">{clip.title}</h3>
        <div className="truncate text-xs text-muted" title={`${title(clip.campaign)} · ${clip.source_title}`}>
          {showCampaign ? title(clip.campaign) : clip.source_title}
        </div>
        {clip.submit_at_views ? <WaitingForViews clip={clip} /> : null}
      </div>
      <div className="mt-auto flex flex-wrap items-center gap-1.5">
        {clip.status === "posted" && clip.posts.length === 0 && clip.watching ? (
          <span className="flex items-center gap-1.5 text-xs text-muted"><Loader2 className="size-3.5 animate-spin" /> Finding your post…</span>
        ) : clip.status === "posted" ? (
          // The same buttons whatever the number of posts (D154): one post showed "Submit", three showed links.
          <>
            <LinkButtons posts={clip.posts} withLabel={clip.posts.length < 2} />
            {!isOwn(clip.campaign) && <SubmitButton clip={clip} />}
          </>
        ) : clip.status === "submitted" ? (
          <LinkButtons posts={clip.posts} />
        ) : clip.status === "skipped" ? (
          <BackToReady clip={clip} />
        ) : (
          <>
            {clip.caption && <CopyButton text={clip.caption} what="Caption" label="Caption" />}
            {clip.file_exists && <DownloadButton clip={clip} />}
            {clip.status === "ready" && <><GoodButton clip={clip} /><NotGoodButton clip={clip} /></>}
          </>
        )}
        <span className="ml-auto"><DeleteButton clip={clip} /></span>
      </div>
    </article>
  );
}

/* ---------- Detail sheet ---------- */

/** Platforms a link is filed for without numbers (studio/posts.py). */
// Platforms with no official way to read a post's numbers (clipper/platforms.py).


function PostStats({ post, campaignUrl }: { post: Post; campaignUrl: string }) {
  const na = (why: string) => (
    <Tip label={why}><span className="text-subtle">n/a</span></Tip>
  );
  const tiktokNa = post.platform === "x" ? "X's API doesn't provide this" : "TikTok's API doesn't provide this";
  const noStats = NO_STATS.includes(post.platform);
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
      {noStats ? (
        <p className="text-sm text-muted">
          {PLATFORM_NAME[post.platform] ?? post.platform} has no official way for Clipper to read a post's numbers: check its views in the app.
        </p>
      ) : (
      <dl className="tabular grid grid-cols-3 gap-x-3 gap-y-2 text-sm sm:grid-cols-4">
        <Stat label="Views" value={formatCount(post.views)} extra={post.x_median ? `${post.x_median}× median` : undefined}
              good={(post.x_median ?? 0) >= 1.5} />
        <Stat label="Avg watch" value={post.avg_watch_s !== null && post.avg_watch_s !== undefined ? `${post.avg_watch_s}s` : na(tiktokNa)} />
        <Stat label="Skipped in 3s" value={post.skip_rate_pct !== null && post.skip_rate_pct !== undefined ? `${post.skip_rate_pct}%` : na(post.platform === "tiktok" ? tiktokNa : "Not reported yet")} />
        <Stat label="Est. earnings" value={post.est_earnings !== null && post.est_earnings !== undefined ? <span className="text-money">{formatMoney(post.est_earnings)}</span> : na("Set the campaign's pay rate to estimate")} />
        <Stat label="Likes" value={formatCount(post.likes)} />
        <Stat label="Comments" value={formatCount(post.comments)} />
        <Stat label="Shares" value={formatCount(post.shares)} />
        <Stat label="Saves" value={post.saves !== null && post.saves !== undefined ? formatCount(post.saves) : na(tiktokNa)} />
      </dl>
      )}
      {!noStats && <ViewsLine post={post} />}
      {post.tasks?.length ? <PostTasks post={post} /> : null}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>Posted {ago(post.posted_at)}</span>
        {post.settling && <Chip tone="warning">Stats still settling (Instagram reports up to 48 h late)</Chip>}
        {post.submitted_at && <Chip tone="success">Submitted</Chip>}
      </div>
    </div>
  );
}

/** What the brief asks now this post has passed a view count (campaign/milestones.py, D98). */
function PostTasks({ post }: { post: Post }) {
  const setTask = useSetTask();
  return (
    <ul className="mt-2 flex flex-col gap-1.5">
      {(post.tasks ?? []).map((t) => (
        <li key={t.views} className={cn("flex items-start gap-2 rounded-sm border p-2 text-xs",
          t.done ? "border-line text-muted" : "border-warning/50 bg-[color-mix(in_oklch,var(--warning)_8%,transparent)]")}>
          <input type="checkbox" className="mt-0.5 accent-[var(--color-accent)]" checked={t.done}
                 aria-label={`Done: ${t.task}`}
                 onChange={(e) => setTask.mutate({ url: post.url, views: t.views, done: e.target.checked })} />
          <span className={cn(t.done && "line-through")}>
            <b>Passed {formatCount(t.views)} views.</b> The brief says: “{t.task}”
          </span>
        </li>
      ))}
    </ul>
  );
}

const DAY_MS = 86_400_000;
const when = (at: string) => new Date(at.replace(" ", "T")).getTime();

/** Views over time, from each sync that changed them: is it still growing, or has it stalled? */
function ViewsLine({ post }: { post: Post }) {
  const { data: points = [] } = usePostHistory(post.url);
  const start = post.posted_at ? when(post.posted_at) : NaN;
  const now = Date.now();
  // Steps from 0 at posting to now: the snapshots only mark changes.
  const series = [
    ...(Number.isFinite(start) ? [{ t: start, v: 0 }] : []),
    ...points.filter((p) => p.views != null).map((p) => ({ t: when(p.at), v: p.views ?? 0 })),
    { t: now, v: post.views ?? 0 },
  ].filter((p) => Number.isFinite(p.t)).sort((a, b) => a.t - b.t);
  if (series.length < 3 || (post.views ?? 0) === 0) return null;
  const t0 = series[0].t, t1 = now, top = Math.max(1, ...series.map((p) => p.v));
  const W = 160, H = 32;
  const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W;
  const y = (v: number) => H - 2 - (v / top) * (H - 4);
  const path = series.map((p, i) => (i === 0 ? `M${x(p.t)},${y(p.v)}`
    : `H${x(p.t)}V${y(p.v)}`)).join("");
  const dayAgo = [...series].reverse().find((p) => p.t <= now - DAY_MS)?.v ?? 0;
  const gain = (post.views ?? 0) - dayAgo;
  return (
    <div className="mt-2 flex items-center gap-3 text-xs text-muted">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="shrink-0 overflow-visible" aria-hidden>
        <path d={path} fill="none" stroke="var(--accent)" strokeWidth="1.5" />
      </svg>
      <span>{gain > 0 ? <><b className="text-money">+{formatCount(gain)}</b> in the last day</> : "No new views in the last day"}</span>
    </div>
  );
}

const Stat = ({ label, value, extra, good }: { label: string; value: React.ReactNode; extra?: string; good?: boolean }) => (
  <div className="flex flex-col">
    <dt className="text-xs text-muted">{label}</dt>
    <dd className="font-medium">{value}{extra && <span className={cn("ml-1 text-xs", good ? "text-money" : "text-muted")}>{extra}</span>}</dd>
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
  const notGood = useNotGood();
  const title = useCampaignTitle();
  const campaignUrlOf = useCampaignUrl();
  const campaignUrl = clip ? campaignUrlOf(clip.campaign) : "";
  const isOwn = useIsOwn();
  const hosted = useStatus().data?.hosted === true;   // on a server there is no folder of yours to open (D148)
  const remove = useDeleteWithUndo();
  const setNote = useSetNote();
  const [note, setNoteText] = useState("");
  useEffect(() => setNoteText(clip?.notes ?? ""), [clip?.id, clip?.notes]);

  const neighbour = (step: number) => {
    const order = listIds.length ? listIds : clips.map((c) => c.id);
    return order[order.indexOf(openId ?? -1) + step];
  };
  const go = (step: number) => {
    const next = neighbour(step);
    if (next !== undefined) setOpen(next);
  };
  // Skipping is a triage decision: move straight on to the next clip. (Not good
  // stays, so you can say why; posting stays, as posts are spaced hours apart.)
  // The editor (D103) is its own page: the sheet closes on the way.
  const navigate = useNavigate();
  const edit = (c: Clip) => { setOpen(null); void navigate({ to: "/edit", search: { clip: c.id } }); };
  const skip = (c: Clip) => {
    const next = neighbour(1);
    setStatus(c, "skipped");
    if (next !== undefined) setOpen(next);
  };

  useHotkeys({
    j: () => go(1),
    k: () => go(-1),
    c: () => clip?.caption && void copyText(clip.caption, "Caption"),
    l: () => clip?.posts[0] && void copyText(clip.posts[0].url, "Link"),
    p: () => clip && setStatus(clip, "posted"),
    x: () => clip && skip(clip),
    r: () => clip && setStatus(clip, "ready"),
    s: () => clip && clip.status !== "submitted" && submit(clip),
    // Good / Not good (D74), as the buttons: Y toggles Good; B on a ready clip skips it too.
    y: () => clip && rate.mutate({ id: clip.id, rating: (clip.rating ?? 0) >= 4 ? null : 5, reasons: clip.reasons }),
    b: () => {
      if (!clip) return;
      if (clip.status === "ready") void notGood(clip);
      else rate.mutate({ id: clip.id, rating: clip.rating != null && clip.rating <= 2 ? null : 1, reasons: clip.reasons });
    },
    f: () => clip && void showFile(clip.id),
    e: () => clip && (clip.status === "ready" || clip.status === "skipped") && edit(clip),
    d: () => clip?.file_exists && window.location.assign(downloadUrl(clip.id)),
    Delete: () => clip && remove(clip),
  }, { enabled: clip !== null, inDialog: true });

  return (
    <Dialog.Root open={clip !== null} onOpenChange={(o) => !o && setOpen(null)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade-in fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]" />
        <Dialog.Content
          aria-describedby={undefined}
          // A toast's Undo is outside the window: clicking it must not close the window.
          onInteractOutside={(e) => { if ((e.target as Element).closest?.("[data-sonner-toaster]")) e.preventDefault(); }}
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
                <div className="flex gap-2">
                  {clip.file_exists && (
                    <a href={downloadUrl(clip.id)} download
                       className="inline-flex h-9 flex-1 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
                      <Download className="size-4" /> Download <Kbd className="ml-auto border-accent-fg/30 bg-accent-fg/10 text-accent-fg">D</Kbd>
                    </a>
                  )}
                  {!hosted && <Tip label="Show in folder"><Button variant="secondary" size="icon" aria-label="Show in folder" onClick={() => void showFile(clip.id)}>
                    <FolderOpen className="size-4" />
                  </Button></Tip>}
                  <DeleteButton clip={clip} size="md" />
                </div>
                {(clip.status === "ready" || clip.status === "skipped") && (
                  <Tip label="Trim to the frame, cut words out, punch in, fix captions" keys="E">
                    <Button variant="secondary" onClick={() => edit(clip)}>
                      <Scissors className="size-4" /> Edit the clip
                    </Button>
                  </Tip>
                )}
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <StatusChip status={clip.status} own={isOwn(clip.campaign)} />
                      <span className="text-xs text-muted">
                        <Link to="/campaigns/$name" params={{ name: clip.campaign }} onClick={() => setOpen(null)} className="hover:text-accent hover:underline">{title(clip.campaign)}</Link>
                        {" · "}{clip.source_title} · {formatDuration(clip.duration_s)}
                      </span>
                    </div>
                    <Dialog.Title className="text-lg font-semibold">{clip.title}</Dialog.Title>
                    {clip.hook && clip.hook !== clip.title && (
                      <p className="mt-1 text-sm text-muted">On screen: {clip.hook}</p>
                    )}
                    <div className="mt-2"><HookControl clip={clip} /></div>
                  </div>
                  <Dialog.Close asChild>
                    <Button variant="ghost" size="icon" aria-label="Close"><X className="size-4" /></Button>
                  </Dialog.Close>
                </div>

                <DuplicateWarning clip={clip} />

                <div className="flex flex-wrap gap-2">
                  {(clip.status === "posted" || clip.status === "submitted") && !isOwn(clip.campaign) && <SubmitButton clip={clip} size="md" />}
                  {clip.status === "ready" ? (
                    <Button variant="primary" onClick={() => setStatus(clip, "posted")}>
                      <Upload className="size-4" /> Mark posted <Kbd className="border-accent-fg/30 bg-accent-fg/10 text-accent-fg">P</Kbd>
                    </Button>
                  ) : clip.status === "skipped" ? (
                    <Button variant="secondary" onClick={() => setStatus(clip, "ready")}>
                      <Undo2 className="size-4" /> Back to ready <Kbd>R</Kbd>
                    </Button>
                  ) : clip.posts.length === 0 && (
                    <Button variant="ghost" onClick={() => setStatus(clip, "ready")}>Back to ready <Kbd>R</Kbd></Button>
                  )}
                  {clip.status !== "skipped" && (
                    <Button variant="ghost" onClick={() => skip(clip)}>
                      <SkipForward className="size-4" /> Skip <Kbd>X</Kbd>
                    </Button>
                  )}
                </div>

                <RatingPanel clip={clip} />

                {(clip.posts.length > 0 || clip.watching) && (
                  <section>
                    <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Posts</h3>
                    {clip.posts.length ? (
                      <div className="flex flex-col gap-2">
                        {clip.posts.map((p) => <PostStats key={p.url} post={p} campaignUrl={campaignUrl} />)}
                        <PasteLink key={`paste-${clip.id}`} clip={clip} compact />
                      </div>
                    ) : (
                      <div className="flex flex-col gap-2 rounded-md border border-dashed border-line p-3">
                        <p className="flex items-center gap-2 text-sm text-muted">
                          <Loader2 className="size-4 shrink-0 animate-spin text-accent" />
                          Looking for your post every 2 minutes for the next half hour, so you can submit it fast.
                        </p>
                        <PasteLink key={`paste-${clip.id}`} clip={clip} />
                      </div>
                    )}
                  </section>
                )}

                {clip.status === "ready" && <PostPanel key={`post-${clip.id}`} clip={clip} />}

                {clip.caption && <CaptionSection key={`caption-${clip.id}`} clip={clip} />}

                {(clip.status === "posted" || clip.status === "submitted") && (
                  <section>
                    <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Proof for disputes</h3>
                    <ProofPanel clip={clip} />
                  </section>
                )}

                <details className="group">
                  <summary className="cursor-pointer text-xs font-semibold tracking-wide text-muted uppercase hover:text-fg">
                    Why Clipper picked it{clip.score != null && <span className="ml-1.5 normal-case">· {clip.score.toFixed(1)}</span>}
                  </summary>
                  <div className="mt-2"><ScoreBreakdown clip={clip} /></div>
                </details>

                <NoteField key={`note-${clip.id}`} note={note} saved={clip.notes} onChange={setNoteText}
                  onSave={() => note !== clip.notes && setNote.mutate({ id: clip.id, notes: note },
                    { onSuccess: () => toast.success("Note saved", { duration: 1500 }) })} />

                <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-subtle">
                  <span><Kbd>J</Kbd> <Kbd>K</Kbd> next / previous</span>
                  <span><Kbd>Y</Kbd> good · <Kbd>B</Kbd> not good</span>
                  <span><Kbd>P</Kbd> posted</span>
                  <span><Kbd>X</Kbd> skip</span>
                  <span><Kbd>D</Kbd> download</span>
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
