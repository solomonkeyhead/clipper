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
  if (platform === "x") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="currentColor" aria-label="X" role="img">
        <path d="M17.8 3h3.1l-6.8 7.8L22 21h-6.2l-4.9-6.4L5.3 21H2.2l7.3-8.3L2 3h6.4l4.4 5.8L17.8 3zm-1.1 16.2h1.7L7.4 4.7H5.6l11.1 14.5z" />
      </svg>
    );
  }
  if (platform === "facebook") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" aria-label="Facebook" role="img">
        <circle cx="12" cy="12" r="9.5" />
        <path d="M13.2 21.5v-7.3h2.4l.4-2.8h-2.8V9.6c0-.8.3-1.4 1.4-1.4H16V5.7a17 17 0 0 0-2.2-.1c-2.2 0-3.6 1.3-3.6 3.7v2.1H7.8v2.8h2.4v7.3" />
      </svg>
    );
  }
  if (platform === "snapchat") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" aria-label="Snapchat" role="img">
        <path d="M12 3c3 0 5 2.2 5 5v2.6l1.8-.6.6 1.2-2.3 1.3c.6 1.9 2 3.3 3.9 3.8l-.3 1.1-2 .4-.5 1.6-2.2-.3c-1.2.8-2.4 1.9-4 1.9s-2.8-1.1-4-1.9l-2.2.3-.5-1.6-2-.4-.3-1.1c1.9-.5 3.3-1.9 3.9-3.8L4.6 12.2l.6-1.2 1.8.6V8c0-2.8 2-5 5-5z" />
      </svg>
    );
  }
  if (platform === "threads") {
    return (
      <svg viewBox="0 0 24 24" className={cls} fill="none" stroke="currentColor" strokeWidth="1.8" aria-label="Threads" role="img">
        <path d="M16.5 11.2c-.3-2.4-1.9-3.7-4.3-3.7-1.6 0-2.9.7-3.6 1.9M15.9 11c-3.4-.6-6.4.2-6.4 2.5 0 1.3 1.2 2.2 2.8 2.1 2.4-.1 3.8-1.8 3.6-5.6M18.6 6.2A8 8 0 1 0 19 17.6" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" className={cls} fill="currentColor" aria-label="TikTok" role="img">
      <path d="M16.6 3c.4 2.2 1.8 3.7 4 4v3.1c-1.5 0-2.9-.4-4-1.1v6.4a6 6 0 1 1-6-6h.5v3.2a2.9 2.9 0 1 0 2.3 2.8V3h3.2z" />
    </svg>
  );
}
