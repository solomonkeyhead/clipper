import { create } from "zustand";

export type Theme = "dark" | "light" | "system";

function read<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}
function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the choice lasts this session */
  }
}

function applyTheme(theme: Theme) {
  const dark = theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.classList.toggle("light", !dark);
}

interface UI {
  paletteOpen: boolean;
  /** The Ask panel (research chat), open over any page. */
  askOpen: boolean;
  setAsk: (open: boolean) => void;
  shortcutsOpen: boolean;
  openClip: number | null;
  /** The order of the list the open clip came from, for J/K in the detail sheet. */
  listIds: number[];
  syncing: boolean;
  online: boolean;
  collapsed: boolean;
  theme: Theme;
  shortcuts: boolean;
  setPalette: (open: boolean) => void;
  setShortcuts: (open: boolean) => void;
  setOpenClip: (id: number | null) => void;
  setListIds: (ids: number[]) => void;
  setSyncing: (v: boolean) => void;
  setOnline: (v: boolean) => void;
  toggleCollapsed: () => void;
  setTheme: (t: Theme) => void;
  setShortcutKeys: (on: boolean) => void;
}

export const useUI = create<UI>((set) => ({
  paletteOpen: false,
  askOpen: false,
  setAsk: (askOpen) => set({ askOpen }),
  shortcutsOpen: false,
  openClip: null,
  listIds: [],
  syncing: false,
  online: true,
  collapsed: read<string>("clipper.collapsed", "0") === "1",
  theme: read<Theme>("clipper.theme", "dark"),
  shortcuts: read<string>("clipper.shortcuts", "1") === "1",
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setShortcuts: (shortcutsOpen) => set({ shortcutsOpen }),
  setOpenClip: (openClip) => set({ openClip }),
  setListIds: (listIds) => set({ listIds }),
  setSyncing: (syncing) => set({ syncing }),
  setOnline: (online) => set({ online }),
  toggleCollapsed: () => set((s) => {
    write("clipper.collapsed", s.collapsed ? "0" : "1");
    return { collapsed: !s.collapsed };
  }),
  setTheme: (theme) => {
    write("clipper.theme", theme);
    applyTheme(theme);
    set({ theme });
  },
  setShortcutKeys: (shortcuts) => {
    write("clipper.shortcuts", shortcuts ? "1" : "0");
    set({ shortcuts });
  },
}));
