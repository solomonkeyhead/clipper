import { clsx, type ClassValue } from "clsx";
import { toast } from "sonner";
import { twMerge } from "tailwind-merge";

export const cn = (...inputs: ClassValue[]) => twMerge(clsx(inputs));

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}K`;
  if (n >= 1_000) return `${(n / 1000).toFixed(1)}K`;
  return Math.round(n).toLocaleString();
}

export function formatMoney(n: number | null | undefined): string {
  if (n === null || n === undefined) return "–";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD",
                                       minimumFractionDigits: n < 100 ? 2 : 0 });
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  const s = Math.round(seconds);
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` : `${s}s`;
}

/** "3 min ago", "2 h ago", "Sep 28" from "YYYY-MM-DD HH:MM[:SS]" local times. */
export function ago(stamp: string | null | undefined, now = Date.now()): string {
  if (!stamp) return "never";
  const t = new Date(stamp.replace(" ", "T")).getTime();
  if (Number.isNaN(t)) return stamp;
  const minutes = Math.max(0, Math.round((now - t) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} d ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export const PLATFORM_NAME: Record<string, string> = {
  tiktok: "TikTok", instagram: "Instagram", instagram_reels: "Instagram",
  youtube: "YouTube", youtube_shorts: "YouTube Shorts", x: "X",
  facebook: "Facebook", snapchat: "Snapchat", threads: "Threads",
};

export async function copyText(text: string, what: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(`${what} copied`, { duration: 1800 });
    return true;
  } catch {
    toast.error("Couldn't copy. Select the text and press Ctrl+C.");
    return false;
  }
}

/** True when a key press is typing, so single-key shortcuts must not fire. */
export function isTyping(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null;
  if (!el) return false;
  return el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
}

/** Open `url` in a new tab; false if the browser blocked it. No features string:
 *  any (even "noopener") makes Chrome and Edge open a separate pop-up window. */
export function openTab(url: string): boolean {
  const tab = window.open(url, "_blank");
  if (!tab) return false;
  tab.opener = null; // the page it opens can't reach back into Clipper
  return true;
}
