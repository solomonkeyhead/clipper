import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, Link2, Loader2, Scissors } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  inspectLink, startImport, startJob, useImports, type FootageImport, type Job, type LinkContents, type RunReport,
} from "@/api/client";
import { cn } from "@/lib/utils";
import { Button } from "./ui";

const size = (bytes: number) =>
  bytes >= 1 << 30 ? `${(bytes / (1 << 30)).toFixed(1)} GB` : `${Math.round(bytes / (1 << 20))} MB`;

/* ---------- import from a shared link ---------- */

function ImportRow({ item }: { item: FootageImport }) {
  const pct = item.total_bytes ? Math.min(100, (item.done_bytes / item.total_bytes) * 100) : null;
  return (
    <div className="rounded-md border border-line p-2.5 text-sm">
      {item.status === "done" ? (
        <span className="flex items-center gap-2 text-success"><CheckCircle2 className="size-4" /> {item.message}: {item.saved.join(", ")}</span>
      ) : item.status === "failed" ? (
        <span className="flex items-start gap-2 text-warning"><AlertTriangle className="mt-0.5 size-4 shrink-0" /> {item.message}</span>
      ) : (
        <div>
          <div className="mb-1 flex items-center justify-between gap-2 text-xs text-muted">
            <span className="flex min-w-0 items-center gap-1.5"><Loader2 className="size-3.5 shrink-0 animate-spin text-accent" />
              <span className="truncate">Downloading {item.current || item.names[0]}</span>
              {item.names.length > 1 && <span>({item.files_done + 1} of {item.names.length})</span>}
            </span>
            <span className="tabular shrink-0">{size(item.done_bytes)}{item.total_bytes ? ` of ${size(item.total_bytes)}` : ""}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div className={cn("h-full rounded-full bg-accent transition-[width]", pct === null && "w-1/3 animate-pulse")}
                 style={pct === null ? undefined : { width: `${Math.max(2, pct)}%` }} />
          </div>
        </div>
      )}
    </div>
  );
}

/** Paste a campaign's Drive/Dropbox link; pick videos; they download into Clipper's uploads. */
export function LinkImport({ onImported, campaign = "" }: { onImported: (names: string[]) => void; campaign?: string }) {
  const qc = useQueryClient();
  const { data: imports = [] } = useImports();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [contents, setContents] = useState<LinkContents | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [mine, setMine] = useState<number[]>([]);
  const reported = useRef(new Set<number>());

  useEffect(() => {
    for (const item of imports) {
      if (mine.includes(item.id) && item.status === "done" && !reported.current.has(item.id)) {
        reported.current.add(item.id);
        void qc.invalidateQueries({ queryKey: ["sources"] });
        onImported(item.saved);
      }
    }
  }, [imports, mine, onImported, qc]);

  const look = async () => {
    setBusy(true);
    try {
      const found = await inspectLink(url);
      if (found.files.length === 1 || found.zipped) {
        await begin(found.zipped ? [] : found.files.map((f) => f.name));
      } else {
        setContents(found);
        setPicked(found.files.map((f) => f.name));
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const begin = async (files: string[]) => {
    const item = await startImport(url, files, campaign);
    setMine((m) => [...m, item.id]);
    void qc.invalidateQueries({ queryKey: ["imports"] });
    setContents(null);
    setUrl("");
  };
  const visible = imports.filter((i) => mine.includes(i.id) || i.status === "running" || i.status === "queued");

  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="text-xs font-medium text-muted">Or paste the campaign's footage link (Google Drive or Dropbox)</div>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void look(); }}>
        <div className="relative flex-1">
          <Link2 className="pointer-events-none absolute top-2.5 left-3 size-4 text-subtle" />
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://drive.google.com/… or https://www.dropbox.com/…"
                 aria-label="Footage link" className="h-9 w-full rounded-sm border border-line bg-surface-1 pr-3 pl-9 text-sm placeholder:text-subtle focus:border-accent focus:outline-none" />
        </div>
        <Button type="submit" variant="secondary" disabled={busy || !url.trim()}>
          {busy && <Loader2 className="size-4 animate-spin" />} Import
        </Button>
      </form>
      {contents && (
        <div className="rounded-md border border-line p-3">
          <div className="mb-2 text-sm font-medium">{contents.files.length} videos in that folder. Which ones?</div>
          <div className="flex max-h-56 flex-col gap-1 overflow-y-auto">
            {contents.files.map((f) => (
              <label key={f.name} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={picked.includes(f.name)}
                       onChange={(e) => setPicked((p) => e.target.checked ? [...p, f.name] : p.filter((n) => n !== f.name))} />
                <span className="truncate">{f.name}</span>
              </label>
            ))}
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setContents(null)}>Cancel</Button>
            <Button variant="primary" disabled={!picked.length} onClick={() => void begin(picked)}>
              Download {picked.length} video{picked.length === 1 ? "" : "s"}
            </Button>
          </div>
        </div>
      )}
      {visible.map((item) => <ImportRow key={item.id} item={item} />)}
      <p className="text-xs text-subtle">Links must be shared as "anyone with the link". For password-protected links, WeTransfer or Frame.io, download the file and drop it in above.</p>
    </div>
  );
}

/* ---------- what a job did with every moment ---------- */

const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function JobResults({ job }: { job: Job }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(job.clips === 0);
  const [making, setMaking] = useState<number | null>(null);
  const report = job.report as RunReport | undefined;
  if (!report || !("moments" in report)) return null;

  const make = async (i: number, start: number, end: number) => {
    setMaking(i);
    try {
      await startJob(job.campaign, job.source, "manual", { ranges: [[String(start), String(end)]] });
      await qc.invalidateQueries({ queryKey: ["jobs"] });
      toast.success("Making that clip", { description: "It's a new job below." });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setMaking(null);
    }
  };

  const headline = report.mode === "manual"
    ? `${report.made} of ${report.moments} hand-picked moment${report.moments === 1 ? "" : "s"} made`
    : `Found ${report.moments} moments · ${report.cleared} cleared the quality bar${report.bar ? ` (${report.bar}/10)` : ""} · made ${report.made}`;
  return (
    <div className="rounded-md bg-surface-2/60 p-3">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center justify-between gap-2 text-left text-sm">
        <span className="font-medium">{headline}</span>
        <ChevronDown className={cn("size-4 shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-3 flex flex-col gap-3">
          {report.reasons.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Why the others were left out</div>
              <ul className="flex flex-col gap-0.5 text-sm">
                {report.reasons.map((r) => (
                  <li key={r.reason} className="flex justify-between gap-3"><span>{r.reason}</span><span className="tabular text-muted">{r.count}</span></li>
                ))}
              </ul>
            </div>
          )}
          {report.near_misses.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">Closest calls</div>
              <div className="flex flex-col gap-2">
                {report.near_misses.map((m, i) => (
                  <div key={i} className="flex items-start gap-3 rounded-md border border-line bg-bg p-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="text-xs text-muted">
                        <span className="tabular font-medium text-fg">{clock(m.start)}–{clock(m.end)}</span>
                        {m.score != null && <> · scored {m.score}/10</>} · {m.why}
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-sm">“{m.text}”</p>
                    </div>
                    <Button size="sm" variant="secondary" disabled={making !== null} onClick={() => void make(i, m.start, m.end)}>
                      {making === i ? <Loader2 className="size-3.5 animate-spin" /> : <Scissors className="size-3.5" />} Make it anyway
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          )}
          {report.mode === "auto" && report.made === 0 && (
            <p className="text-xs text-muted">Nothing cleared the bar, so Clipper made nothing rather than filler. Make a close call anyway, or pick the moments yourself.</p>
          )}
        </div>
      )}
    </div>
  );
}
