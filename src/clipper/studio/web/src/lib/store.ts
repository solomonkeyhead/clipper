import { create } from "zustand";

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

interface UI {
  paletteOpen: boolean;
  openClip: number | null;
  /** The order of the list the open clip came from (next and previous in the detail sheet). */
  listIds: number[];
  syncing: boolean;
  online: boolean;
  collapsed: boolean;
  setPalette: (open: boolean) => void;
  setOpenClip: (id: number | null) => void;
  setListIds: (ids: number[]) => void;
  setSyncing: (v: boolean) => void;
  setOnline: (v: boolean) => void;
  toggleCollapsed: () => void;
  /** "Viewing": "all", "group:<id>" or "account:<platform>:<handle>" (D89). */
  accountScope: string;
  setAccountScope: (scope: string) => void;
}

export const useUI = create<UI>((set) => ({
  paletteOpen: false,
  openClip: null,
  listIds: [],
  syncing: false,
  online: true,
  collapsed: read<string>("clipper.collapsed", "0") === "1",
  setPalette: (paletteOpen) => set({ paletteOpen }),
  setOpenClip: (openClip) => set({ openClip }),
  setListIds: (listIds) => set({ listIds }),
  setSyncing: (syncing) => set({ syncing }),
  setOnline: (online) => set({ online }),
  toggleCollapsed: () => set((s) => {
    write("clipper.collapsed", s.collapsed ? "0" : "1");
    return { collapsed: !s.collapsed };
  }),
  accountScope: read<string>("clipper.scope", "all"),
  setAccountScope: (accountScope) => {
    write("clipper.scope", accountScope);
    set({ accountScope });
  },
}));
