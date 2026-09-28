import { cn } from "@/lib/utils";

/** Small monochrome platform glyphs (platform identity without platform colours). */
export function PlatformIcon({ platform, className }: { platform: string; className?: string }) {
  const cls = cn("size-4 shrink-0", className);
  if (platform.startsWith("instagram")) {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" aria-label="Instagram" role="img">
        <rect x="3" y="3" width="18" height="18" rx="5" />
        <circle cx="12" cy="12" r="4.2" />
        <circle cx="17.4" cy="6.6" r="1.1" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  if (platform.startsWith("youtube")) {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" aria-label="YouTube" role="img">
        <rect x="2.5" y="5.5" width="19" height="13" rx="4" />
        <path d="M10 9.2v5.6l4.8-2.8z" fill="currentColor" stroke="none" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" className={cls} fill="currentColor" aria-label="TikTok" role="img">
      <path d="M16.6 3c.4 2.2 1.8 3.7 4 4v3.1c-1.5 0-2.9-.4-4-1.1v6.4a6 6 0 1 1-6-6h.5v3.2a2.9 2.9 0 1 0 2.3 2.8V3h3.2z" />
    </svg>
  );
}
