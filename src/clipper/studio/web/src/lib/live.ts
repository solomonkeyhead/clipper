import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";
import { keys } from "@/api/client";
import { useUI } from "./store";

/** Server-Sent Events: the server says what changed, the page refetches just that. */
export function useLiveUpdates() {
  const qc = useQueryClient();
  const setSyncing = useUI((s) => s.setSyncing);
  const setOnline = useUI((s) => s.setOnline);

  useEffect(() => {
    let source: EventSource | null = null;
    let failures = 0;
    let retry: number | undefined;

    const refreshClips = () =>
      [keys.home, keys.campaigns, keys.clips, keys.posts, ["campaign"]].forEach((queryKey) =>
        qc.invalidateQueries({ queryKey }));

    const connect = () => {
      source = new EventSource("/api/events");
      source.onopen = () => {
        failures = 0;
        setOnline(true);
      };
      source.addEventListener("sync.started", () => setSyncing(true));
      source.addEventListener("stats.synced", (e) => {
        setSyncing(false);
        refreshClips();
        qc.invalidateQueries({ queryKey: keys.status });
        const data = JSON.parse((e as MessageEvent).data || "{}") as { problems?: string[] };
        data.problems?.forEach((p) => toast.warning(p, { duration: 8000 }));
      });
      source.addEventListener("clips.changed", refreshClips);
      source.addEventListener("job.progress", (e) => {
        const job = JSON.parse((e as MessageEvent).data) as { id: number; status: string };
        qc.setQueryData<{ id: number }[]>(["jobs"], (old = []) => {
          const rest = old.filter((j) => j.id !== job.id);
          return [job, ...rest].sort((a, b) => b.id - a.id);
        });
      });
      source.addEventListener("campaigns.changed", refreshClips);
      source.addEventListener("import.progress", (e) => {
        const item = JSON.parse((e as MessageEvent).data) as { id: number };
        qc.setQueryData<{ id: number }[]>(["imports"], (old = []) =>
          [item, ...old.filter((i) => i.id !== item.id)].sort((a, b) => b.id - a.id));
      });
      source.addEventListener("sources.changed", () => qc.invalidateQueries({ queryKey: ["sources"] }));
      source.addEventListener("settings.changed", () => {
        qc.invalidateQueries({ queryKey: keys.settings });
        qc.invalidateQueries({ queryKey: keys.status });
        qc.invalidateQueries({ queryKey: keys.setup });
        qc.invalidateQueries({ queryKey: keys.home });
      });
      source.addEventListener("research.progress", (e) => {
        qc.setQueryData(["research", "progress"], JSON.parse((e as MessageEvent).data || "null"));
      });
      source.addEventListener("accounts.changed", () => {
        [keys.accounts, keys.setup, keys.status, keys.home].forEach((queryKey) => qc.invalidateQueries({ queryKey }));
      });
      source.onerror = () => {
        failures += 1;
        if (failures >= 2) setOnline(false);
        source?.close();
        retry = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** failures));
      };
    };
    connect();

    // Activity pings: the server turns a 30-minute gap into "since you were last here".
    const seen = () => {
      if (document.visibilityState === "visible") void fetch("/api/visit", { method: "POST" });
    };
    seen();
    const ping = window.setInterval(seen, 5 * 60_000);
    document.addEventListener("visibilitychange", seen);
    return () => {
      source?.close();
      window.clearTimeout(retry);
      window.clearInterval(ping);
      document.removeEventListener("visibilitychange", seen);
    };
  }, [qc, setSyncing, setOnline]);
}
