import { Link, useSearch } from "@tanstack/react-router";
import { AlertTriangle, CheckCircle2, CheckSquare, ChevronDown, ChevronRight, Film, Loader2, Plus, Scissors, Square, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  sourceVideoUrl, startJob, uploadVideo, useCampaignTitle, useCampaigns, useJobs, useSettings, useSetup, useSources,
  type Job, type JobMode, type Source,
} from "@/api/client";
import { JobResults, LinkImport } from "@/components/footage";
import { Segmented, TextInput } from "@/components/form";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, Chip, EmptyState, PageHeader } from "@/components/ui";
import { useQueryClient } from "@tanstack/react-query";
import { ago, cn } from "@/lib/utils";

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <Card className="p-5">
      <h2 className="mb-3 flex items-center gap-2.5 text-md font-semibold">
        <span className="grid size-6 place-items-center rounded-full bg-accent-soft text-xs font-bold text-accent">{n}</span>
        {title}
      </h2>
      {children}
    </Card>
  );
}

function Dropzone({ onUploaded, campaign }: { onUploaded: (s: Source) => void; campaign: string }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState<{ name: string; pct: number } | null>(null);
  const qc = useQueryClient();

  const send = async (file: File | undefined) => {
    if (!file) return;
    setProgress({ name: file.name, pct: 0 });
    try {
      const source = await uploadVideo(file, (f) => setProgress({ name: file.name, pct: f * 100 }), campaign);
      await qc.invalidateQueries({ queryKey: ["sources"] });
      toast.success(`${file.name} uploaded`);
      onUploaded(source);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setProgress(null);
    }
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); void send(e.dataTransfer.files[0]); }}
      onClick={() => !progress && input.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors",
        over ? "border-accent bg-accent-soft" : "border-line hover:border-line-strong hover:bg-surface-2",
      )}
    >
      <input ref={input} type="file" accept="video/*,.mov,.mkv" hidden
             onChange={(e) => void send(e.target.files?.[0])} />
      {progress ? (
        <div className="w-full max-w-md">
          <div className="mb-2 truncate text-sm">Uploading {progress.name}…</div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-3">
            <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${progress.pct}%` }} />
          </div>
          <div className="tabular mt-1 text-xs text-muted">{Math.round(progress.pct)}%</div>
        </div>
      ) : (
        <>
          <UploadCloud className="size-7 text-muted" />
          <div className="text-sm font-medium">Drop the campaign's video here, or click to choose</div>
          <div className="text-xs text-muted">MP4, MOV or MKV · any size · saved to Clipper's uploads folder</div>
        </>
      )}
    </div>
  );
}

const modeLabel = (job: Job) =>
  job.mode === "manual" ? `${job.ranges.length} hand-picked`
    : job.mode === "top" ? `up to ${job.top} clips` : "Clipper decides how many";

/** A finished video: one line, its details on a click (the list can run to dozens after a batch). */
function FinishedRow({ job }: { job: Job }) {
  const title = useCampaignTitle();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-line bg-surface-1">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-surface-2">
        <ChevronRight className={cn("size-3.5 shrink-0 text-muted transition-transform", open && "rotate-90")} />
        <span className="min-w-0 flex-1 truncate text-sm">{job.name}</span>
        <span className="hidden shrink-0 text-xs text-muted sm:inline">{title(job.campaign)} · {ago(job.created)}</span>
        {job.status === "failed" ? <Chip tone="danger">Failed</Chip>
          : job.clips > 0 ? <Chip tone="success">{job.clips} clip{job.clips === 1 ? "" : "s"}</Chip>
          : <Chip tone="warning">No clips</Chip>}
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-line px-3 py-2.5 pl-9">
          <div className="text-xs text-muted">{modeLabel(job)}</div>
          {job.message && <p className="text-xs text-muted">{job.message}</p>}
          {job.status === "done" && <JobResults job={job} />}
          {job.clips > 0 && (
            <Link to="/campaigns/$name" params={{ name: job.campaign }} className="text-sm font-medium text-accent hover:underline">
              See its clips →
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

/** How many finished videos show before "Show all". */
const FINISHED_SHOWN = 8;

function FinishedList({ jobs }: { jobs: Job[] }) {
  const [all, setAll] = useState(false);
  const made = jobs.reduce((n, j) => n + j.clips, 0);
  return (
    <>
      <h2 className="mb-3 flex items-baseline gap-2 text-md font-semibold">
        Finished <span className="text-xs font-normal text-muted">{jobs.length} video{jobs.length === 1 ? "" : "s"} · {made} clip{made === 1 ? "" : "s"}</span>
      </h2>
      <div className="flex flex-col gap-1.5">
        {(all ? jobs : jobs.slice(0, FINISHED_SHOWN)).map((j) => <FinishedRow key={j.id} job={j} />)}
      </div>
      {jobs.length > FINISHED_SHOWN && (
        <Button variant="ghost" size="sm" className="mt-2" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${jobs.length}`}
        </Button>
      )}
    </>
  );
}

function JobCard({ job }: { job: Job }) {
  const title = useCampaignTitle();
  const running = job.status === "running" || job.status === "queued";
  return (
    <Card className="flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{job.name}</div>
          <div className="text-xs text-muted">{title(job.campaign)} · {modeLabel(job)} · started {ago(job.created)}</div>
        </div>
        {job.status === "done" && (job.clips > 0
          ? <Chip tone="success"><CheckCircle2 className="size-3.5" /> {job.clips} made</Chip>
          : <Chip tone="warning">No clips</Chip>)}
        {job.status === "failed" && <Chip tone="danger"><AlertTriangle className="size-3.5" /> Failed</Chip>}
        {running && <Chip tone="accent"><Loader2 className="size-3.5 animate-spin" /> {job.status === "queued" ? "Queued" : "Working"}</Chip>}
      </div>
      {running && (
        <div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-3">
            <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(3, job.pct)}%` }} />
          </div>
          <div className="mt-1 flex justify-between text-xs text-muted">
            <span>{job.stage}</span><span className="tabular">{Math.round(job.pct)}%</span>
          </div>
        </div>
      )}
      {job.message && <p className="text-xs text-muted">{job.message}</p>}
      {job.status === "done" && <JobResults job={job} />}
      {job.status === "done" && job.clips > 0 && (
        <Link to="/campaigns/$name" params={{ name: job.campaign }} className="text-sm font-medium text-accent hover:underline">
          View and rate the clips →
        </Link>
      )}
    </Card>
  );
}

/* ---------- manual mode: hand-picked moments ---------- */

/** "1:02:03.5" / "24:45" / "90" -> seconds; null if unreadable. */
function toSeconds(text: string): number | null {
  const parts = text.trim().split(":");
  if (!text.trim() || parts.length > 3 || parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  return parts.reduce((total, p) => total * 60 + Number(p), 0);
}

function toClock(seconds: number): string {
  const whole = Math.floor(seconds);
  const tenth = Math.round((seconds - whole) * 10);
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), sec = whole % 60;
  const body = h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  return tenth > 0 && tenth < 10 ? `${body}.${tenth}` : body;
}

type Range = { start: string; end: string };

function rangeProblem(r: Range): string | null {
  const a = toSeconds(r.start), b = toSeconds(r.end);
  if (a === null || b === null) return "Use minutes:seconds, like 24:45";
  if (b <= a) return "The end is before the start";
  if (b - a < 3) return "At least 3 seconds";
  return null;
}

const SORTED_BY: Record<string, string> = {
  clipped: "clipped for this campaign", added: "added for this campaign", name: "by its name", like: "named like its neighbours",
};

function FootageGroup({ title, videos, open: startOpen, hint, picked, manual, clipped, onToggle, onAll }: {
  title: string; videos: Source[]; open: boolean; hint?: string; picked: string[]; manual: boolean;
  clipped: Map<string, number>; onToggle: (path: string) => void; onAll?: () => void;
}) {
  const [open, setOpen] = useState(startOpen);
  useEffect(() => setOpen(startOpen), [startOpen]);
  const chosen = videos.filter((v) => picked.includes(v.path)).length;
  return (
    <div className="rounded-md border border-line">
      <div className="flex items-center gap-2 px-3 py-2">
        <button className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm font-medium" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <ChevronDown className={cn("size-4 shrink-0 text-muted transition-transform", !open && "-rotate-90")} />
          <span className="truncate">{title}</span>
          <span className="shrink-0 text-xs font-normal text-muted">{videos.length} video{videos.length === 1 ? "" : "s"}{chosen ? ` · ${chosen} picked` : ""}</span>
        </button>
        {open && onAll && videos.length > 1 && (
          <button className="shrink-0 text-xs text-accent hover:underline" onClick={onAll}>Select all</button>
        )}
      </div>
      {open && (
        <div className="flex max-h-72 flex-col gap-1 overflow-y-auto border-t border-line p-1">
          {hint && <p className="px-2 py-1.5 text-xs text-muted">{hint}</p>}
          {videos.map((s) => (
            <button key={s.path} onClick={() => onToggle(s.path)} aria-pressed={picked.includes(s.path)}
              className={cn("flex items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                picked.includes(s.path) ? "border-accent bg-accent-soft" : "border-transparent hover:bg-surface-2")}>
              {manual ? <Film className="size-4 shrink-0 text-muted" />
                : picked.includes(s.path) ? <CheckSquare className="size-4 shrink-0 text-accent" />
                : <Square className="size-4 shrink-0 text-muted" />}
              <span className="min-w-0 flex-1 truncate text-sm" title={s.sorted_by ? `Sorted ${SORTED_BY[s.sorted_by] ?? ""}` : undefined}>{s.name}</span>
              {clipped.has(s.path.toLowerCase()) && (
                <Chip tone="success" className="shrink-0" title="Already clipped: clipping it again makes near-duplicates">
                  <CheckCircle2 className="size-3" /> {clipped.get(s.path.toLowerCase())} clip{clipped.get(s.path.toLowerCase()) === 1 ? "" : "s"}
                </Chip>
              )}
              <span className="tabular shrink-0 text-xs text-muted">
                {s.size_mb >= 1024 ? `${(s.size_mb / 1024).toFixed(1)} GB` : `${Math.round(s.size_mb)} MB`} · {s.folder} · {ago(s.modified)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function RangeEditor({ source, ranges, setRanges }: {
  source: string; ranges: Range[]; setRanges: (r: Range[]) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [playable, setPlayable] = useState(true);
  const [active, setActive] = useState(0);
  useEffect(() => setPlayable(true), [source]);
  const update = (i: number, key: keyof Range, value: string) =>
    setRanges(ranges.map((r, j) => (j === i ? { ...r, [key]: value } : r)));
  const mark = (key: keyof Range) => video.current && update(active, key, toClock(video.current.currentTime));

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted">
        Clipper cuts exactly these moments, then frames, captions and checks them like any other clip. Good for scenes
        without much talking, which it can't find by itself.
      </p>
      {source && playable ? (
        <div className="flex flex-col gap-2">
          <video ref={video} src={sourceVideoUrl(source)} controls preload="metadata" onError={() => setPlayable(false)}
                 className="max-h-80 w-full rounded-md bg-black" />
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>Pause where moment {active + 1} should</span>
            <Button size="sm" variant="secondary" onClick={() => mark("start")}>Start here</Button>
            <Button size="sm" variant="secondary" onClick={() => mark("end")}>End here</Button>
          </div>
        </div>
      ) : source ? (
        <p className="rounded-md border border-dashed border-line p-3 text-xs text-muted">
          This file can't play in the browser (common for .mov files from editing software). Find the times in your usual
          video player and type them below.
        </p>
      ) : (
        <p className="text-xs text-muted">Pick the video above to watch it here and mark times as you go.</p>
      )}
      <div className="flex flex-col gap-2">
        {ranges.map((r, i) => {
          const problem = (r.start || r.end) ? rangeProblem(r) : null;
          const a = toSeconds(r.start), b = toSeconds(r.end);
          return (
            <div key={i} onFocus={() => setActive(i)} onClick={() => setActive(i)}
                 className={cn("flex flex-wrap items-center gap-2 rounded-md border p-2",
                   active === i ? "border-accent/60 bg-accent-soft/30" : "border-line")}>
              <span className="w-20 text-xs font-medium text-muted">Moment {i + 1}</span>
              <TextInput className="w-28" value={r.start} placeholder="24:45" aria-label={`Moment ${i + 1} start`}
                         onChange={(e) => update(i, "start", e.target.value)} />
              <span className="text-muted">to</span>
              <TextInput className="w-28" value={r.end} placeholder="26:05" aria-label={`Moment ${i + 1} end`}
                         onChange={(e) => update(i, "end", e.target.value)} />
              {!problem && a !== null && b !== null && <span className="tabular text-xs text-muted">{Math.round(b - a)}s</span>}
              {problem && <span className="text-xs text-warning">{problem}</span>}
              {ranges.length > 1 && (
                <Button size="icon" variant="ghost" className="ml-auto size-7" aria-label={`Remove moment ${i + 1}`}
                        onClick={(e) => { e.stopPropagation(); setRanges(ranges.filter((_, j) => j !== i)); setActive(0); }}>
                  <X className="size-3.5" />
                </Button>
              )}
            </div>
          );
        })}
        <Button variant="ghost" className="w-fit"
                onClick={() => { setRanges([...ranges, { start: "", end: "" }]); setActive(ranges.length); }}>
          <Plus className="size-4" /> Add a moment
        </Button>
      </div>
    </div>
  );
}

export function NewClipsPage() {
  const search = useSearch({ from: "/new" });
  const { data: campaigns = [] } = useCampaigns();
  const { data: sources = [] } = useSources();
  const { data: jobs = [] } = useJobs();
  const { data: setup } = useSetup();
  const { data: settings } = useSettings();
  const qc = useQueryClient();
  const active = campaigns.filter((c) => c.has_brief && !c.archived);
  const [campaign, setCampaign] = useState(search.campaign ?? "");
  // The videos to clip, in the order picked; one job each (D72).
  const [picked, setPicked] = useState<string[]>([]);
  const source = picked[0] ?? "";
  const [mode, setMode] = useState<JobMode>("auto");
  const [top, setTop] = useState(4);
  const [ranges, setRanges] = useState<Range[]>([{ start: "", end: "" }]);
  const [busy, setBusy] = useState(false);
  const chosen = active.find((c) => c.name === campaign);
  // Below Pro a video gives at most 10 clips; Pro gets every moment that qualifies (D71).
  const pro = (settings?.plan ?? "pro") === "pro";
  const counts = pro ? [1, 3, 5, 8, 10, 15, 20, 30] : [1, 2, 3, 4, 5, 6, 8, 10];
  const autoCap = pro ? chosen?.max_clips ?? null : Math.min(chosen?.max_clips ?? 10, 10);
  const batchCap = { free: 3, research: 10 }[settings?.plan ?? "pro"] ?? null;
  // Hand-picked times belong to one video, so that mode picks one.
  const toggle = (path: string) => setPicked((p) =>
    mode === "manual" ? [path] : p.includes(path) ? p.filter((x) => x !== path) : [...p, path]);
  useEffect(() => { if (mode === "manual") setPicked((p) => p.slice(0, 1)); }, [mode]);
  // An import's videos join the list a moment after it finishes; select the first then.
  const [imported, setImported] = useState<string[]>([]);
  const onImported = useCallback((names: string[]) => {
    setImported(names);
    toast.success(`Imported ${names.join(", ")}`, { description: "Selected below, ready to clip." });
  }, []);
  useEffect(() => {
    // Every video an import brought in is picked: a footage folder is a batch.
    const found = sources.filter((s) => imported.includes(s.name)).map((s) => s.path);
    if (found.length) {
      setPicked((p) => mode === "manual" ? found.slice(0, 1) : [...new Set([...p, ...found])]);
      setImported([]);
    }
  }, [sources, imported, mode]);
  const filled = ranges.filter((r) => r.start || r.end);
  const rangesOk = filled.length > 0 && filled.every((r) => !rangeProblem(r));
  const overCap = batchCap !== null && picked.length > batchCap;
  // The queue reads top to bottom in the order it runs; finished jobs, newest first, below (D74).
  const queue = jobs.filter((j) => j.status === "running" || j.status === "queued")
    .sort((a, b) => (a.status === "running" ? -1 : b.status === "running" ? 1 : a.id - b.id));
  const finished = jobs.filter((j) => j.status !== "running" && j.status !== "queued");
  // Videos already clipped, and how many clips each gave, so none is clipped twice by mistake.
  const clipped = new Map<string, number>();
  for (const j of jobs) {
    if (j.status === "done") clipped.set(j.source.toLowerCase(), (clipped.get(j.source.toLowerCase()) ?? 0) + j.clips);
  }
  // Footage by campaign (studio/footage.py): the picked campaign's first and open,
  // then each other campaign folded, then anything not sorted yet (D73).
  const titleOf = (name: string) => campaigns.find((c) => c.name === name)?.title ?? name;
  const groups = (() => {
    const by = new Map<string, Source[]>();
    for (const s of sources) by.set(s.campaign ?? "", [...(by.get(s.campaign ?? "") ?? []), s]);
    const mine = by.get(campaign) ?? [];
    const out = [] as { key: string; title: string; videos: Source[]; open: boolean; hint?: string }[];
    if (campaign) {
      out.push({ key: campaign, title: `For ${titleOf(campaign)}`, videos: mine, open: true,
                 hint: mine.length ? undefined : "None yet. Upload or import above, or pick from the other groups: whatever you clip is filed here." });
    }
    for (const [key, videos] of by) {
      if (key && key !== campaign) out.push({ key, title: titleOf(key), videos, open: !campaign && by.size === 1 });
    }
    if (by.get("")?.length) out.push({ key: "", title: "Not sorted yet", videos: by.get("") ?? [], open: !campaign || !mine.length });
    return out;
  })();
  const ready = Boolean(campaign && picked.length && !overCap && (mode !== "manual" || rangesOk));

  const make = async () => {
    setBusy(true);
    try {
      const started = await startJob(campaign, picked, mode, mode === "manual"
        ? { ranges: filled.map((r) => [r.start, r.end] as [string, string]) }
        : mode === "top" ? { top } : {});
      await qc.invalidateQueries({ queryKey: ["jobs"] });
      toast.success(started.length > 1 ? `Clipping ${started.length} videos` : "Clipping started",
        { description: started.length > 1 ? "One after another; each shows its progress below." : "You can keep using Clipper; progress shows below." });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fade-in max-w-4xl">
      <PageHeader title="New clips"
        subtitle="Give Clipper a campaign's footage and it finds the best moments, frames them, captions them and files them under the campaign." />
      <div className="flex flex-col gap-4">
        {setup && !setup.ai_ready && (
          <Card className="flex flex-wrap items-center gap-3 border-warning/40 p-4 text-sm">
            <AlertTriangle className="size-4 shrink-0 text-warning" />
            <span className="flex-1">Clipper needs your free AI key to find and score moments.</span>
            <Link to="/settings" className="font-medium text-accent hover:underline">Add it in Settings →</Link>
          </Card>
        )}

        <Step n={1} title="Campaign">
          {active.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {active.map((c) => (
                <button key={c.name} onClick={() => setCampaign(c.name)}
                  className={cn("flex items-center justify-between gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
                    campaign === c.name ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2")}>
                  <span className="truncate text-sm font-medium">{c.title}</span>
                  <span className="flex items-center gap-1.5 text-muted">
                    {c.platforms.map((p) => <PlatformIcon key={p} platform={p} className="size-3.5" />)}
                  </span>
                </button>
              ))}
              <Link to="/campaigns/new"
                    className="flex items-center gap-2 rounded-md border border-dashed border-line px-3 py-2.5 text-sm text-muted hover:bg-surface-2 hover:text-fg">
                <Plus className="size-4" /> New campaign
              </Link>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-muted">Clipper follows a campaign's rules, so add the campaign first.</p>
              <Link to="/campaigns/new"
                    className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
                <Plus className="size-4" /> Add a campaign
              </Link>
            </div>
          )}
          {chosen && chosen.reward_per_1k_usd != null && (
            <p className="mt-2 text-xs text-muted">Pays ${chosen.reward_per_1k_usd.toFixed(2)} per 1K views. Clipper follows its brief's rules.</p>
          )}
        </Step>

        <Step n={2} title="Footage">
          <Dropzone onUploaded={(s) => toggle(s.path)} campaign={campaign} />
          <LinkImport onImported={onImported} campaign={campaign} />
          {sources.length > 0 && (
            <>
              <div className="mt-4 mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className="font-medium text-muted">
                  {mode === "manual" ? "Or pick a video already on this PC" : "Or pick videos already on this PC"}
                </span>
                {picked.length > 0 && mode !== "manual" && (
                  <button className="text-muted hover:text-fg" onClick={() => setPicked([])}>Clear ({picked.length} picked)</button>
                )}
              </div>
              <div className="flex flex-col gap-2">
                {groups.map((g) => (
                  <FootageGroup key={g.key} title={g.title} videos={g.videos} open={g.open} hint={g.hint}
                    picked={picked} manual={mode === "manual"} clipped={clipped} onToggle={toggle}
                    onAll={mode === "manual" ? undefined : () => setPicked((p) => [...new Set([...p, ...g.videos.map((v) => v.path)])].slice(0, batchCap ?? undefined))} />
                ))}
              </div>
            </>
          )}
        </Step>

        <Step n={3} title="Which moments">
          <Segmented label="Which moments" value={mode} onChange={setMode}
            options={[["auto", "Let Clipper decide", autoCap ? `Every moment good enough, up to ${autoCap}` : "Every moment good enough, no limit"],
                      ["top", "Set a number", "The best ones, up to your count"],
                      ["manual", "I'll pick them", "Type or mark start and end times"]]} />
          <div className="mt-4">
            {mode === "auto" && (
              <p className="text-sm text-muted">
                Clipper scores every moment in the video and keeps each one that clears its quality bar, so a strong
                episode gives more clips and a weak one fewer, or none. Every clip shows its score, so you can judge it.
              </p>
            )}
            {mode === "top" && (
              <div className="flex flex-wrap items-center gap-2">
                {counts.map((n) => (
                  <button key={n} onClick={() => setTop(n)}
                    className={cn("h-9 min-w-11 rounded-md border px-3 text-sm font-medium",
                      top === n ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
                    {n}
                  </button>
                ))}
                {pro && (
                  <input type="number" min={1} max={500} aria-label="Another number" placeholder="Other"
                         value={counts.includes(top) ? "" : top}
                         onChange={(e) => e.target.value && setTop(Math.max(1, Math.min(500, Number(e.target.value))))}
                         className="h-9 w-20 rounded-md border border-line bg-surface-1 px-2 text-sm focus:border-accent focus:outline-none" />
                )}
                <span className="text-xs text-muted">
                  at most; fewer if nothing else is good enough{pro ? "" : ". Pro makes as many as qualify."}
                </span>
              </div>
            )}
            {mode === "manual" && <RangeEditor source={source} ranges={ranges} setRanges={setRanges} />}
          </div>
        </Step>

        <div className="flex items-center justify-end gap-3">
          {!ready && (
            <span className="text-xs text-muted">
              {!campaign ? "Pick a campaign" : !picked.length ? "Pick a video"
                : overCap ? `Your plan clips up to ${batchCap} videos at a time; Pro has no limit`
                : "Add at least one moment's start and end"}
            </span>
          )}
          <Button variant="primary" disabled={!ready || busy} onClick={() => void make()}>
            <Scissors className="size-4" /> {picked.length > 1 ? `Make clips from ${picked.length} videos` : "Make clips"}
          </Button>
        </div>

        <section className="mt-4">
          {queue.length > 0 && (
            <>
              <h2 className="mb-3 text-md font-semibold">Clipping now{queue.length > 1 ? `, then ${queue.length - 1} more in this order` : ""}</h2>
              <div className="mb-6 flex flex-col gap-2">{queue.map((j) => <JobCard key={j.id} job={j} />)}</div>
            </>
          )}
          {finished.length > 0 && <FinishedList jobs={finished} />}
          {!jobs.length && (
            <EmptyState icon={<Scissors className="size-5" />} title="Nothing clipping yet"
              body="A full episode takes a few minutes: transcribing, finding scenes, scoring moments, then rendering each clip." />
          )}
        </section>
      </div>
    </div>
  );
}
