import { useEffect, useRef } from "react";
import { isTyping } from "./utils";
import { useUI } from "./store";

type Handler = (e: KeyboardEvent) => void;

interface Options {
  enabled?: boolean;
  /** Keep working while a dialog is open (the dialog's own keys). */
  inDialog?: boolean;
  /** The "G then a key" bindings (AppShell's): the only ones that answer the key after a G (D196). */
  goto?: boolean;
}

/** When G was last pressed: the key after it belongs to "go to", so G then D went to the Dashboard and also
 *  downloaded the clip you were on (D196). */
let lastG = 0;

/**
 * Single-key shortcuts, off while typing and when the user turns them off
 * (WCAG 2.1.4). `bindings` maps a key ("a", "?", "Enter") to its handler.
 * Keys with Ctrl/Cmd/Alt are left to the browser.
 */
export function useHotkeys(bindings: Record<string, Handler>, { enabled = true, inDialog = false, goto = false }: Options = {}) {
  const ref = useRef(bindings);
  ref.current = bindings;
  const on = useUI((s) => s.shortcuts);

  useEffect(() => {
    if (!enabled || !on) return;
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e)) return;
      const dialogOpen = document.querySelector("[role=dialog][data-state=open]") !== null;
      if (dialogOpen !== inDialog) return;
      if (e.key === "g" || e.key === "G") lastG = e.timeStamp;
      else if (!goto && e.timeStamp - lastG < 1200) return;
      const fn = ref.current[e.key] ?? ref.current[e.key.toLowerCase()];
      if (fn) {
        e.preventDefault();
        fn(e);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled, on, inDialog, goto]);
}
