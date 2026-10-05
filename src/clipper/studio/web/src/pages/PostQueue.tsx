import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { CheckCircle2, Download, ExternalLink, PartyPopper, SkipForward, ThumbsDown } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { downloadUrl, useCampaigns, useClips, useSetClipStatus, useSetClipSubmitted, type Clip } from "@/api/client";
import { PlatformIcon } from "@/components/PlatformIcon";
import { PostPanel } from "@/components/posting";
import { Button, Card, CopyButton, EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";

/**
 * The post queue (D149): one clip at a time, with everything for it on one screen. Two kinds of step,
 * in the order money moves: post a ready clip (download, text, upload pages, "I posted it"), then submit
 * a posted clip's links to its campaign ("Mark submitted"). Done moves to the next; Skip puts it
 * at the back for now. Nothing here is new: it's the clip sheet's Post panel and Submitted switch, in a row.
 */
type Step = "post" | "submit";
const LABEL: Record<Step, string> = { post: "To post", submit: "To submit" };

export function PostQueuePage() {
  const search = useSearch({ from: "/post" });
  const navigate = useNavigate({ from: "/post" });
  const { data: clips, isLoading } = useClips();
  const { data: campaigns = [] } = useCampaigns();
  const setStatus = useSetClipStatus();
  const setSubmitted = useSetClipSubmitted();
  const [later, setLater] = useState<number[]>([]);          // skipped for now: the back of the line

  const archived = useMemo(() => new Set(campaigns.filter((c) => c.archived).map((c) => c.name)), [campaigns]);
  const mine = (clips ?? []).filter((c) => !archived.has(c.campaign) && (!search.campaign || c.campaign === search.campaign));
  const lists: Record<Step, Clip[]> = {
    post: mine.filter((c) => c.status === "ready" && c.file_exists),
    submit: mine.filter((c) => c.status === "posted" && c.submits !== false),
  };
  const step: Step = search.step ?? (lists.post.length ? "post" : "submit");
  const line = [...lists[step].filter((c) => !later.includes(c.id)), ...later.flatMap((id) => lists[step].filter((c) => c.id === id))];
  const clip = line[0];
  const campaign = campaigns.find((c) => c.name === clip?.campaign);
  const go = (to: Step) => void navigate({ search: (s) => ({ ...s, step: to }) });
  const skip = () => clip && setLater((l) => [...l.filter((id) => id !== clip.id), clip.id]);

  return (
    <div className="fade-in max-w-5xl">
      <PageHeader title="Post queue" subtitle="One clip at a time: post it, then submit its link. Done moves to the next." />
      <div className="mb-5 flex flex-wrap items-center gap-1.5" role="tablist">
        {(["post", "submit"] as const).map((s) => (
          <button key={s} role="tab" aria-selected={step === s} onClick={() => go(s)}
                  className={cn("h-8 rounded-full border px-3 text-sm font-medium transition-colors",
                    step === s ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
            {LABEL[s]} <span className="tabular ml-1 opacity-70">{lists[s].length}</span>
          </button>
        ))}
        {search.campaign && (
          <Link to="/post" search={{}} className="ml-2 text-sm text-accent hover:underline">Everything, not only {campaign?.title ?? search.campaign}</Link>
        )}
      </div>
      {isLoading ? <Skeleton className="h-96" /> : !clip ? (
        <EmptyState icon={<PartyPopper className="size-5 text-money" />}
          title={step === "post" ? "Nothing waiting to be posted" : "Every posted clip is submitted"}
          body={step === "post" && lists.submit.length ? `${lists.submit.length} posted clip${lists.submit.length === 1 ? "" : "s"} still need their link submitted.` : undefined}
          action={step === "post" && lists.submit.length
            ? <Button variant="primary" onClick={() => go("submit")}>Submit links</Button>
            : <Link to="/new" className="inline-flex h-9 items-center rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">Make clips</Link>} />
      ) : (
        <div className="grid gap-5 md:grid-cols-[17rem_1fr]">
          <div className="flex flex-col gap-2">
            <video key={clip.id} src={clip.video} poster={clip.thumb} controls playsInline preload="metadata"
                   className="aspect-[9/16] w-full rounded-md bg-black object-contain" />
            <div className="text-center text-xs text-muted">{line.length} left in this step</div>
          </div>
          <Card className="flex flex-col gap-4 p-5">
            <div>
              <div className="text-xs text-muted">{campaign?.title ?? clip.campaign}</div>
              <h2 className="text-md font-semibold">{clip.title || clip.hook || `Clip ${clip.id}`}</h2>
            </div>
            {step === "post" ? (
              <>
                <div className="flex flex-wrap gap-2">
                  <a href={downloadUrl(clip.id)} download
                     className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
                    <Download className="size-4" /> Download
                  </a>
                </div>
                <PostPanel clip={clip} />
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
                  <Button variant="primary" onClick={() => setStatus.mutate({ id: clip.id, status: "posted" },
                    { onSuccess: () => toast.success("Marked posted", { description: "Its link is picked up on the next sync, then it shows under To submit." }) })}>
                    <CheckCircle2 className="size-4" /> I posted it
                  </Button>
                  <Button variant="ghost" onClick={skip}><SkipForward className="size-4" /> Skip for now</Button>
                  <Button variant="ghost" onClick={() => setStatus.mutate({ id: clip.id, status: "skipped" })}><ThumbsDown className="size-4" /> Won't post this</Button>
                </div>
              </>
            ) : (
              <>
                <p className="text-sm text-muted">Copy each link into {campaign?.title ?? "the campaign"}'s submission form, then press Mark submitted.</p>
                <ul className="flex flex-col gap-2">
                  {clip.posts.map((p) => (
                    <li key={p.url} className="flex items-center gap-2 rounded-sm border border-line bg-surface-1 px-3 py-2 text-sm">
                      <PlatformIcon platform={p.platform} className="size-4 shrink-0" />
                      <span className="min-w-0 flex-1 truncate">{p.url}</span>
                      <CopyButton text={p.url} what="Link" label="Copy" />
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
                  {campaign?.campaign_url && (
                    <a href={campaign.campaign_url} target="_blank" rel="noopener noreferrer"
                       className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
                      <ExternalLink className="size-4" /> Open the submission page
                    </a>
                  )}
                  <Button variant="primary" onClick={() => setSubmitted.mutate({ id: clip.id, submitted: true })}>
                    <CheckCircle2 className="size-4" /> Mark submitted
                  </Button>
                  <Button variant="ghost" onClick={skip}><SkipForward className="size-4" /> Skip for now</Button>
                </div>
              </>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
