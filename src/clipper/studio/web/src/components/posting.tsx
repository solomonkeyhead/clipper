import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { AlertTriangle, BadgeDollarSign, CheckCircle2, Clock, Pin, ExternalLink, Link2, Loader2, ShieldCheck, Upload, Wand2, XCircle } from "lucide-react";
import { toast } from "sonner";
import {
  useAccountGroups, useAccounts, useAddPostLink, useCampaigns, usePosts, useRecheckRules,
  type Account, type Clip, type PostCopy,
} from "@/api/client";
import { useUI } from "@/lib/store";
import { PLATFORM_NAME, cn, openBehind } from "@/lib/utils";
import { PlatformIcon } from "./PlatformIcon";
import { Button, CopyButton, Tip } from "./ui";

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
/** Each upload page, and the paid-content switch to turn on there (D95): campaigns
 *  pay per view, so every post is an ad. On TikTok #ad without the switch gets
 *  the post kept off the For You feed; the others say the label costs no reach. */
const UPLOAD: Record<string, { name: string; url: string; how: string; label: string; why?: string }> = {
  tiktok: {
    name: "TikTok", url: "https://www.tiktok.com/tiktokstudio/upload",
    how: "Drop the video in", label: "More options → Disclose commercial content → Branded content",
    why: "#ad alone isn't enough: without this switch TikTok keeps the post off the For You feed.",
  },
  instagram: {
    name: "Instagram", url: "https://www.instagram.com/",
    how: "Press Create (+), then Post",
    label: "Advanced settings → Add paid partnership label, if the campaign gave you a brand account to tag",
  },
  youtube: {
    name: "YouTube Shorts", url: "https://www.youtube.com/upload",
    how: "Pick the channel you post Shorts on, then drop the video in", label: "Details → Show more → Paid promotion",
  },
  x: {
    name: "X", url: "https://x.com/compose/post",
    how: "Attach the video with the picture icon", label: "X has no switch: the brief's #ad in the post covers it",
  },
};

const key = (platform: string) => platform.split("_")[0];
const ORDER = Object.keys(UPLOAD);

/** Download, copy each platform's text, open each platform the campaign pays for. */
export function PostPanel({ clip }: { clip: Clip }) {
  const { data: campaigns = [] } = useCampaigns();
  const postOn = usePostOn(clip.campaign);
  const lastPost = useLastPost();
  const campaign = campaigns.find((c) => c.name === clip.campaign);
  const copies = [...(clip.post_copy ?? [])].sort((a, b) => ORDER.indexOf(key(a.platform)) - ORDER.indexOf(key(b.platform)));
  const allowed = campaign?.platforms ?? [];
  const targets = [...new Set(allowed.map(key))].filter((p) => p in UPLOAD);
  const shown = (targets.length ? targets : ORDER).sort((a, b) => ORDER.indexOf(a) - ORDER.indexOf(b));

  // Behind Clipper, which stays in front: you come back here to copy and check.
  const open = (platforms: string[]) => {
    platforms.forEach((p) => openBehind(UPLOAD[p].url));
    toast(`${platforms.map((p) => UPLOAD[p].name).join(", ")} opened in ${platforms.length > 1 ? "tabs" : "a tab"} behind this one`,
          { duration: 2500 });
  };

  return (
    <section className="rounded-md border border-line p-3">
      <h3 className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Post it</h3>
      <BriefCheck clip={clip} />
      <div className="flex flex-col gap-2.5 text-sm">
        {copies.length ? (
          // One line per platform, the same shape every time: the Open buttons sit in
          // one column, so posting everywhere is a run down it (then "Open all" below).
          <div className="flex min-w-0 flex-col">
            <div className="divide-y divide-line rounded-sm border border-line bg-surface-1">
              {copies.map((c) => (
                <PlatformRow key={c.platform} copy={c} accounts={postOn(key(c.platform))}
                             url={UPLOAD[key(c.platform)]?.url} onOpen={() => key(c.platform) in UPLOAD && open([key(c.platform)])} />
              ))}
            </div>
            {shown.length > 1 && (
              <div className="mt-1.5 flex justify-end pr-2.5">
                <Button size="sm" variant="ghost" className="w-[8.5rem]" onClick={() => open(shown)}>
                  <Upload className="size-3.5" /> Open all {shown.length}
                </Button>
              </div>
            )}
          </div>
        ) : clip.caption ? <CopyButton text={clip.caption} what="Caption" label="Copy the caption" /> : <span className="text-muted">No caption.</span>}
        <BeforePosting platforms={copies.length ? copies.map((c) => key(c.platform)) : shown} lastPost={lastPost}
                       paid={!campaign?.own_channel} />
        {clip.pinned_comment && (
          <div className="flex items-start gap-2 rounded-sm border border-line bg-surface-1 px-2.5 py-2">
            <Pin className="mt-0.5 size-3.5 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="text-xs text-muted">Pin as the first comment, on each platform</div>
              <p className="text-sm">{clip.pinned_comment}</p>
            </div>
            <CopyButton text={clip.pinned_comment} what="Pinned comment" label="Copy" />
          </div>
        )}
        {(campaign?.posting_rules?.length ?? 0) > 0 && <Checklist rules={campaign!.posting_rules!} />}
        <PasteLink clip={clip} compact />
      </div>
    </section>
  );
}

/** One platform: its text to copy, its rule checks, and its upload page. */
/** Which accounts a campaign's clips go to on a platform (D89): the viewing
 *  switcher's, else those in groups posting for the campaign, else all of them.
 *  Empty when there's only one account there: nothing to choose. */
function usePostOn(campaign: string) {
  const { data: accounts = [] } = useAccounts();
  const { data: groups = [] } = useAccountGroups();
  const scope = useUI((s) => s.accountScope);
  return (platform: string) => {
    const here = accounts.filter((a) => a.platform === platform);
    if (here.length < 2) return [];
    const scoped = scope.startsWith("account:") ? [scope.slice(8)]
      : groups.find((g) => `group:${g.id}` === scope)?.members;
    const assigned = groups.filter((g) => g.campaigns.includes(campaign)).flatMap((g) => g.members);
    const keys = scoped ?? (assigned.length ? assigned : null);
    return keys ? here.filter((a) => keys.includes(a.key)) : here;
  };
}

/** Leave this long between posts on a platform, so each gets its own test
 *  audience and none reads as spam (best guess from the research, D96). */
const SPACING_HOURS = 3;

/** Each platform's most recent post time, in the accounts being viewed. */
function useLastPost() {
  const { data: posts = [] } = usePosts();
  const last = new Map<string, number>();
  for (const p of posts) {
    const at = p.posted_at ? new Date(p.posted_at.replace(" ", "T")).getTime() : NaN;
    if (Number.isFinite(at) && at > (last.get(p.platform) ?? 0)) last.set(p.platform, at);
  }
  return last;
}

/** Hours since `last`, or null: under SPACING_HOURS is worth a word before posting. */
const recentHours = (last?: number) => (last ? (Date.now() - last) / 3_600_000 : null);
const ago = (hours: number) => (hours < 1 ? `${Math.max(1, Math.round(hours * 60))} min` : `${hours.toFixed(1)} h`);

/** The platform's short name, so every row fits on one line ("YouTube", not "YouTube Shorts"). */
const SHORT: Record<string, string> = { tiktok: "TikTok", instagram: "Instagram", youtube: "YouTube", x: "X" };

function PlatformRow({ copy, accounts, url, onOpen }: {
  copy: PostCopy; accounts: Account[]; url?: string; onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const failed = copy.checks.filter((c) => !c.passed);
  const k = key(copy.platform);
  const name = SHORT[k] ?? UPLOAD[k]?.name ?? copy.platform;
  return (
    <div className="px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="flex w-[6.5rem] shrink-0 items-center gap-1.5 text-sm font-medium">
          <PlatformIcon platform={copy.platform} className="size-3.5" /> {name}
          <Tip label={failed.length ? `${failed.length} rule${failed.length > 1 ? "s" : ""} broken: click to see`
            : `All ${copy.checks.length} rules met: click to see them`}>
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
                    aria-label={failed.length ? "Rules broken" : "All rules met"}
                    className={cn("inline-flex", failed.length ? "text-danger" : "text-success")}>
              {failed.length ? <XCircle className="size-3.5" /> : <CheckCircle2 className="size-3.5" />}
            </button>
          </Tip>

        </span>
        <span className="flex min-w-0 flex-1 items-center gap-1.5">
          {copy.title && <CopyButton text={copy.title} what={`${name} title`} label="Title"
                                     tip={<>Copy the title: <b>{copy.title}</b></>} />}
          {/* What the platform's upload page calls it: TikTok's "description" (campaign/rules.py TEXT_NAMES). */}
          <CopyButton text={copy.caption} what={`${name} ${copy.text_name.toLowerCase()}`} label={copy.text_name} />
        </span>
        <Tip label={`${UPLOAD[k]?.how ?? "Upload it"}. Opens in whichever account you're signed in to here.`}>
          {/* A real link, so middle-click and Ctrl-click work as usual too. */}
          <a href={url} target="_blank" rel="noopener noreferrer"
             onClick={(e) => { if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey) { e.preventDefault(); onOpen(); } }}
             className="inline-flex h-7 w-[8.5rem] shrink-0 items-center justify-center gap-1.5 rounded-sm border border-line bg-surface-2 px-2.5 text-xs font-medium hover:bg-surface-3">
            Open {name} <ExternalLink className="size-3" />
          </a>
        </Tip>
      </div>
      {accounts.length > 0 && (
        <p className="mt-1 flex flex-wrap items-center gap-1 pl-[7rem] text-xs text-muted">
          Post from {accounts.map((a) => <span key={a.key} className="rounded-full border border-line px-2 py-0.5 text-fg">@{a.handle}</span>)}
          {accounts.length > 1 && <span>(one post each)</span>}
        </p>
      )}
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

/** What to do on each upload page, out of the rows so they stay one line each:
 *  the paid-content switch (D95), and any platform posted to under 3 hours ago (D96). */
function BeforePosting({ platforms, lastPost, paid }: { platforms: string[]; lastPost: Map<string, number>; paid: boolean }) {
  const shown = [...new Set(platforms)].filter((p) => UPLOAD[p]);
  const soon = shown.map((p) => [p, recentHours(lastPost.get(p))] as const)
    .filter(([, h]) => h !== null && h < SPACING_HOURS);
  // Your own channel isn't paid to post, so it gets no paid-content label (D154).
  if (!shown.length || (!paid && !soon.length)) return null;
  return (
    <div className="flex flex-col gap-1.5 rounded-sm border border-line bg-surface-1 px-2.5 py-2 text-xs">
      {paid && <>
        <span className="flex items-center gap-1.5 font-medium">
          <BadgeDollarSign className="size-3.5 text-warning" /> Turn on the paid-content label
        </span>
        <ul className="flex flex-col gap-0.5 pl-5 text-muted">
          {shown.map((p) => (
            <li key={p}><b className="text-fg">{SHORT[p] ?? UPLOAD[p].name}:</b> {UPLOAD[p].label}.{UPLOAD[p].why && ` ${UPLOAD[p].why}`}</li>
          ))}
        </ul>
      </>}
      {soon.length > 0 && (
        <span className="flex items-start gap-1.5 text-warning">
          <Clock className="mt-px size-3.5 shrink-0" />
          <span>
            You posted {soon.map(([p, h]) => `${SHORT[p] ?? UPLOAD[p].name} ${ago(h!)}`).join(", ")} ago. Waiting until about{" "}
            {new Date(Math.max(...soon.map(([p]) => lastPost.get(p)!)) + SPACING_HOURS * 3_600_000)
              .toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}{" "}
            gives each post its own test audience.
          </span>
        </span>
      )}
    </div>
  );
}

/** The AI check of the post against the brief's own words (campaign/audit.py). */
function BriefCheck({ clip }: { clip: Clip }) {
  const recheck = useRecheckRules();
  const rules = clip.rules;
  if (!rules) return null;
  if (rules.brief.length || rules.failed.length) {
    return (
      <div className="mb-3 flex flex-col gap-2 rounded-md border border-danger/40 bg-danger/5 p-2.5 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 font-medium text-danger">
            <AlertTriangle className="size-4" /> Breaks the brief: fix before posting
          </span>
          <FixButton clip={clip} />
        </div>
        {rules.failed.map((f) => <span key={f}>{f}</span>)}
        {rules.brief.map((p, i) => (
          <div key={i} className="flex flex-col gap-0.5">
            <span>{p.problem}</span>
            <span className="text-xs text-muted">The brief: “{p.rule}”</span>
          </div>
        ))}
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

/** Text the brief says is missing becomes a rule for every clip; anything else,
 *  the AI rewrites the caption to follow (D91). The checks run again after. */
function FixButton({ clip }: { clip: Clip }) {
  const qc = useQueryClient();
  const fix = useMutation({
    mutationFn: async () => {
      const res = await fetch(`/api/clips/${clip.id}/fix`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || res.statusText);
      return data as { fixed: string[] };
    },
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["clips"] });
      toast.success("Fixed", { description: `${res.fixed.join("; ") || "Nothing needed"}. Checking it against the brief again.` });
    },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <Button size="sm" variant="primary" disabled={fix.isPending} onClick={() => fix.mutate()}>
      {fix.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <Wand2 className="size-3.5" />} Fix it
    </Button>
  );
}

/** The brief's rules only the poster can follow; ticks last while the clip is open. */
function Checklist({ rules }: { rules: string[] }) {
  const [done, setDone] = useState<Record<string, boolean>>({});
  return (
    <div className="flex flex-col gap-1">
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

/** A post's link, pasted: filed like a synced post (studio/posts.py). */
export function PasteLink({ clip, compact = false }: { clip: Clip; compact?: boolean }) {
  const add = useAddPostLink();
  const [open, setOpen] = useState(!compact);
  const [url, setUrl] = useState("");
  if (!open) {
    return <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><Link2 className="size-3.5" /> Already posted? Add its link</Button>;
  }
  return (
    <form className="flex gap-2" onSubmit={(e) => {
      e.preventDefault();
      add.mutate({ id: clip.id, url }, {
        onSuccess: (res) => { setUrl(""); if (compact) setOpen(false); toast.success(`${PLATFORM_NAME[(res as { platform: string }).platform] ?? "Post"} link added`, { description: "Its stats update on the next sync." }); },
        onError: (err) => toast.error((err as Error).message),
      });
    }}>
      <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste the post's link (TikTok, Instagram, YouTube, X, Facebook, Snapchat or Threads)"
             aria-label="Post link" className="h-9 flex-1 rounded-sm border border-line bg-surface-1 px-3 text-sm placeholder:text-subtle focus:border-accent focus:outline-none" />
      <Button type="submit" variant="secondary" disabled={!url.trim() || add.isPending}>Add</Button>
    </form>
  );
}
