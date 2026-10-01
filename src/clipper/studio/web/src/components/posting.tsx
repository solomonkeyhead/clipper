import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, Download, ExternalLink, Loader2, ShieldCheck, Upload, XCircle } from "lucide-react";
import { toast } from "sonner";
import {
  downloadUrl, useAddCaptionRule, useCampaigns, useRecheckRules, type BriefProblem, type Clip, type PostCopy,
} from "@/api/client";
import { cn } from "@/lib/utils";
import { PlatformIcon } from "./PlatformIcon";
import { Button, CopyButton } from "./ui";

/**
 * Posting by hand, in one place (D77): the platforms' own upload pages, opened
 * in tabs. They can't be shown inside Clipper -- TikTok, Instagram and YouTube
 * all refuse to be framed by another site, and getting around that would break
 * their logins and their terms -- and official-API posting waits on each
 * platform's app review.
 *
 * Each platform gets its own text, with every brief rule checked (D81): a
 * brief can ask something of one platform only, and YouTube has a title.
 */
const UPLOAD: Record<string, { name: string; url: string; how: string; label: string }> = {
  tiktok: {
    name: "TikTok", url: "https://www.tiktok.com/tiktokstudio/upload",
    how: "Drop the video in", label: "More options → Content disclosure → Branded content",
  },
  instagram: {
    name: "Instagram", url: "https://www.instagram.com/",
    how: "Press Create (+), then Post", label: "Advanced settings → Add paid partnership label",
  },
  youtube: {
    name: "YouTube Shorts", url: "https://www.youtube.com/upload",
    how: "Pick the channel you post Shorts on, then drop the video in", label: "Details → Show more → Paid promotion",
  },
  x: {
    name: "X", url: "https://x.com/compose/post",
    how: "Attach the video with the picture icon", label: "X has no label switch, so keep the brief's #ad in the post",
  },
};

const key = (platform: string) => platform.split("_")[0];
const ORDER = Object.keys(UPLOAD);

/** Download, copy each platform's text, open each platform the campaign pays for. */
export function PostPanel({ clip }: { clip: Clip }) {
  const { data: campaigns = [] } = useCampaigns();
  const campaign = campaigns.find((c) => c.name === clip.campaign);
  const copies = [...(clip.post_copy ?? [])].sort((a, b) => ORDER.indexOf(key(a.platform)) - ORDER.indexOf(key(b.platform)));
  const allowed = campaign?.platforms ?? [];
  const targets = [...new Set(allowed.map(key))].filter((p) => p in UPLOAD);
  const shown = (targets.length ? targets : ORDER).sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));

  const open = (platforms: string[]) => {
    // Browsers allow one new tab per click unless the page may open pop-ups.
    const blocked = platforms.filter((p) => !window.open(UPLOAD[p].url, "_blank", "noopener"));
    if (blocked.length && blocked.length < platforms.length) {
      toast.warning(`Your browser blocked ${blocked.map((p) => UPLOAD[p].name).join(" and ")}`, {
        description: "Allow pop-ups for Clipper (the icon at the end of the address bar), then press again.", duration: 9000,
      });
    }
  };

  return (
    <section className="rounded-md border border-line p-3">
      <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Post it</h3>
      <BriefCheck clip={clip} />
      <ol className="flex flex-col gap-2.5 text-sm">
        <li className="flex flex-wrap items-center gap-2">
          <span className="w-5 text-muted">1.</span>
          {clip.file_exists ? (
            <a href={downloadUrl(clip.id)} download
               className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-2.5 text-xs font-medium hover:bg-surface-3">
              <Download className="size-3.5" /> Download the video
            </a>
          ) : <span className="text-muted">The video file is missing.</span>}
        </li>
        <li className="flex gap-2">
          <span className="w-5 shrink-0 text-muted">2.</span>
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            {copies.length ? copies.map((c) => (
              <PlatformRow key={c.platform} copy={c} onOpen={() => key(c.platform) in UPLOAD && open([key(c.platform)])} />
            )) : clip.caption ? <CopyButton text={clip.caption} what="Caption" label="Copy the caption" /> : <span className="text-muted">No caption.</span>}
            {shown.length > 1 && (
              <Button size="sm" variant="ghost" className="w-fit" onClick={() => open(shown)}>
                <Upload className="size-3.5" /> Open all {shown.length}
              </Button>
            )}
            <p className="text-xs text-muted">
              {shown.map((p) => `${UPLOAD[p].name}: ${UPLOAD[p].how}.`).join(" ")} Each opens in whichever account
              you're signed in to in this browser.
            </p>
          </div>
        </li>
        {(campaign?.posting_rules?.length ?? 0) > 0 && (
          <li className="flex gap-2">
            <span className="w-5 shrink-0 text-muted">3.</span>
            <Checklist rules={campaign!.posting_rules!} id={clip.id} />
          </li>
        )}
        <li className="flex gap-2">
          <span className="w-5 shrink-0 text-muted">{(campaign?.posting_rules?.length ?? 0) > 0 ? "4." : "3."}</span>
          <span className="text-muted">Back here: <b className="text-fg">Mark posted</b>, or paste the post's link below.</span>
        </li>
      </ol>
      <details className="mt-2 pl-7 text-xs text-muted">
        <summary className="cursor-pointer hover:text-fg">Paid clipping is an ad: turn on the paid-partnership label</summary>
        <p className="mt-1">
          Campaigns pay you to post, so the platforms (and the FTC in the US) expect it labelled, besides any #ad the
          brief asks for.{" "}
          {shown.map((p) => <span key={p}><b>{UPLOAD[p].name}</b>: {UPLOAD[p].label}. </span>)}
        </p>
      </details>
    </section>
  );
}

/** One platform: its text to copy, its rule checks, and its upload page. */
function PlatformRow({ copy, onOpen }: { copy: PostCopy; onOpen: () => void }) {
  const [open, setOpen] = useState(false);
  const failed = copy.checks.filter((c) => !c.passed);
  const name = UPLOAD[key(copy.platform)]?.name ?? copy.platform;
  return (
    <div className="rounded-sm border border-line bg-surface-1 px-2.5 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex w-28 items-center gap-1.5 text-sm font-medium">
          <PlatformIcon platform={copy.platform} className="size-3.5" /> {name}
        </span>
        {copy.title && <CopyButton text={copy.title} what={`${name} title`} label="Copy title" />}
        <CopyButton text={copy.caption} what={`${name} ${copy.title ? "description" : "caption"}`}
                    label={copy.title ? "Copy description" : "Copy caption"} />
        <Button size="sm" variant="secondary" onClick={onOpen}>Open {name} <ExternalLink className="size-3" /></Button>
        <button type="button" onClick={() => setOpen(!open)}
                className={cn("ml-auto inline-flex items-center gap-1 text-xs", failed.length ? "text-danger" : "text-success")}>
          {failed.length ? <XCircle className="size-3.5" /> : <CheckCircle2 className="size-3.5" />}
          {failed.length ? `${failed.length} rule${failed.length > 1 ? "s" : ""} broken` : `All ${copy.checks.length} rules met`}
          <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
        </button>
      </div>
      {copy.title && <p className="mt-1.5 truncate text-xs text-muted" title={copy.title}>Title: <span className="text-fg">{copy.title}</span></p>}
      {open && (
        <ul className="mt-2 flex flex-col gap-1 border-t border-line pt-2 text-xs">
          {copy.checks.map((c) => (
            <li key={c.name} className="flex gap-1.5">
              {c.passed ? <CheckCircle2 className="mt-px size-3.5 shrink-0 text-success" /> : <XCircle className="mt-px size-3.5 shrink-0 text-danger" />}
              <span>{c.name}{c.detail && <span className="text-muted"> · {c.detail}</span>}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The AI check of the post against the brief's own words (campaign/audit.py). */
function BriefCheck({ clip }: { clip: Clip }) {
  const recheck = useRecheckRules();
  const rules = clip.rules;
  if (!rules) return null;
  if (rules.brief.length) {
    return (
      <div className="mb-3 flex flex-col gap-2 rounded-md border border-danger/40 bg-danger/5 p-2.5 text-sm">
        <div className="flex items-center gap-1.5 font-medium text-danger">
          <AlertTriangle className="size-4" /> Breaks the brief: fix before posting
        </div>
        {rules.brief.map((p, i) => <Problem key={i} problem={p} campaign={clip.campaign} />)}
      </div>
    );
  }
  return (
    <p className="mb-2 flex items-center gap-1.5 text-xs text-muted">
      {rules.checked ? (
        <><ShieldCheck className="size-3.5 text-success" /> The AI read this post against the brief: nothing broken.</>
      ) : rules.refused ? (
        <><AlertTriangle className="size-3.5 text-warning" /> The AI's content filter wouldn't read this post. The rule checks below still ran; read the brief yourself for anything else.</>
      ) : rules.checking ? (
        <><Loader2 className="size-3.5 animate-spin" /> Reading this post against the brief…</>
      ) : (
        <>
          <Loader2 className="size-3.5" /> Not read against the brief yet.
          <button type="button" className="text-accent hover:underline" disabled={recheck.isPending}
                  onClick={() => recheck.mutate(clip.campaign)}>Check now</button>
        </>
      )}
    </p>
  );
}

const PLACE: Record<string, "caption" | "title"> = { title: "title", caption: "caption" };

function Problem({ problem, campaign }: { problem: BriefProblem; campaign: string }) {
  const add = useAddCaptionRule();
  const platform = problem.platform === "all" ? null : problem.platform;
  const where = platform ? (UPLOAD[key(platform)]?.name ?? platform) : "every platform";
  const place = PLACE[problem.where] ?? "caption";
  return (
    <div className="flex flex-col gap-1">
      <span>{problem.problem}</span>
      <span className="text-xs text-muted">The brief: “{problem.rule}”</span>
      {problem.add ? (
        <Button size="sm" variant="secondary" className="w-fit" disabled={add.isPending}
                onClick={() => add.mutate({ name: campaign, rule: {
                  text: problem.add, must: "include", place, platforms: platform ? [platform] : [], quote: problem.rule,
                } }, {
                  onSuccess: () => toast.success("Rule added", { description: `Every clip of this campaign gets “${problem.add}” in its ${place} on ${where}.` }),
                  onError: (e) => toast.error("Couldn't add the rule", { description: String(e.message ?? e) }),
                })}>
          Add “{problem.add}” to the {place} on {where}, for every clip
        </Button>
      ) : (
        <span className="text-xs text-muted">Edit the caption below, or add the rule on the campaign's page.</span>
      )}
    </div>
  );
}

/** The brief's rules only the poster can follow; ticks last while the clip is open. */
function Checklist({ rules, id }: { rules: string[]; id: number }) {
  const [done, setDone] = useState<Record<string, boolean>>({});
  return (
    <div className="flex flex-col gap-1" key={id}>
      <span className="text-muted">While posting, the brief also says:</span>
      {rules.map((r) => (
        <label key={r} className="flex cursor-pointer items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 accent-[var(--color-accent)]" checked={!!done[r]}
                 onChange={(e) => setDone({ ...done, [r]: e.target.checked })} />
          <span className={cn(done[r] && "text-muted line-through")}>{r}</span>
        </label>
      ))}
    </div>
  );
}
