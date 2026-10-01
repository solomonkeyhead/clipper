import { Download, ExternalLink, Upload } from "lucide-react";
import { toast } from "sonner";
import { downloadUrl, useCampaigns, type Clip } from "@/api/client";
import { PlatformIcon } from "./PlatformIcon";
import { Button, CopyButton } from "./ui";

/**
 * Posting by hand, in one place (D77): the platforms' own upload pages, opened
 * in tabs. They can't be shown inside Clipper -- TikTok, Instagram and YouTube
 * all refuse to be framed by another site, and getting around that would break
 * their logins and their terms -- and official-API posting waits on each
 * platform's app review.
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
};

const key = (platform: string) => platform.split("_")[0];

/** Download, copy the caption, open each platform the campaign pays for. */
export function PostPanel({ clip }: { clip: Clip }) {
  const { data: campaigns = [] } = useCampaigns();
  const allowed = campaigns.find((c) => c.name === clip.campaign)?.platforms ?? [];
  const targets = [...new Set(allowed.map(key))].filter((p) => p in UPLOAD);
  const order = Object.keys(UPLOAD);
  const shown = (targets.length ? targets : order).sort((a, b) => order.indexOf(a) - order.indexOf(b));

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
        <li className="flex flex-wrap items-center gap-2">
          <span className="w-5 text-muted">2.</span>
          {clip.caption ? <CopyButton text={clip.caption} what="Caption" label="Copy the caption" /> : <span className="text-muted">No caption.</span>}
        </li>
        <li className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-5 text-muted">3.</span>
            {shown.map((p) => (
              <Button key={p} size="sm" variant="secondary" onClick={() => open([p])}>
                <PlatformIcon platform={p} className="size-3.5" /> {UPLOAD[p].name} <ExternalLink className="size-3" />
              </Button>
            ))}
            {shown.length > 1 && (
              <Button size="sm" variant="ghost" onClick={() => open(shown)}><Upload className="size-3.5" /> Open all</Button>
            )}
          </div>
          <p className="pl-7 text-xs text-muted">
            {shown.map((p) => `${UPLOAD[p].name}: ${UPLOAD[p].how}.`).join(" ")} Each opens in whichever account
            you're signed in to in this browser.
          </p>
        </li>
        <li className="flex gap-2">
          <span className="w-5 shrink-0 text-muted">4.</span>
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
