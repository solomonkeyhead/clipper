import { useEffect, useRef } from "react";
import { isTyping } from "./utils";
import { useUI } from "./store";

type Handler = (e: KeyboardEvent) => void;

interface Options {
  enabled?: boolean;
  /** Keep working while a dialog is open (the dialog's own keys). */
  inDialog?: boolean;
}

/**
 * Single-key shortcuts, off while typing and when the user turns them off
 * (WCAG 2.1.4). `bindings` maps a key ("a", "?", "Enter") to its handler.
 * Keys with Ctrl/Cmd/Alt are left to the browser.
 */
export function useHotkeys(bindings: Record<string, Handler>, { enabled = true, inDialog = false }: Options = {}) {
  const ref = useRef(bindings);
  ref.current = bindings;
  const on = useUI((s) => s.shortcuts);

  useEffect(() => {
    if (!enabled || !on) return;
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e)) return;
      const dialogOpen = document.querySelector("[role=dialog][data-state=open]") !== null;
      if (dialogOpen !== inDialog) return;
      const fn = ref.current[e.key] ?? ref.current[e.key.toLowerCase()];
      if (fn) {
        e.preventDefault();
        fn(e);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled, on, inDialog]);
}
