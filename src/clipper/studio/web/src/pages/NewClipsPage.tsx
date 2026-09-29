import { Link, useSearch } from "@tanstack/react-router";
import { AlertTriangle, CheckCircle2, Film, Loader2, Plus, Scissors, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  sourceVideoUrl, startJob, uploadVideo, useCampaignTitle, useCampaigns, useJobs, useSetup, useSources,
  type Job, type JobMode, type Source,
} from "@/api/client";
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

function Dropzone({ onUploaded }: { onUploaded: (s: Source) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState<{ name: string; pct: number } | null>(null);
  const qc = useQueryClient();

  const send = async (file: File | undefined) => {
    if (!file) return;
    setProgress({ name: file.name, pct: 0 });
    try {
      const source = await uploadVideo(file, (f) => setProgress({ name: file.name, pct: f * 100 }));
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
export function toSeconds(text: string): number | null {
  const parts = text.trim().split(":");
  if (!text.trim() || parts.length > 3 || parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  return parts.reduce((total, p) => total * 60 + Number(p), 0);
}

export function toClock(seconds: number): string {
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
  const qc = useQueryClient();
  const active = campaigns.filter((c) => c.has_brief && !c.archived);
  const [campaign, setCampaign] = useState(search.campaign ?? "");
  const [source, setSource] = useState("");
  const [mode, setMode] = useState<JobMode>("auto");
  const [top, setTop] = useState(4);
  const [ranges, setRanges] = useState<Range[]>([{ start: "", end: "" }]);
  const [busy, setBusy] = useState(false);
  const chosen = active.find((c) => c.name === campaign);
  const filled = ranges.filter((r) => r.start || r.end);
  const rangesOk = filled.length > 0 && filled.every((r) => !rangeProblem(r));
  const ready = Boolean(campaign && source && (mode !== "manual" || rangesOk));

  const make = async () => {
    setBusy(true);
    try {
      await startJob(campaign, source, mode, mode === "manual"
        ? { ranges: filled.map((r) => [r.start, r.end] as [string, string]) }
        : mode === "top" ? { top } : {});
      await qc.invalidateQueries({ queryKey: ["jobs"] });
      toast.success("Clipping started", { description: "You can keep using Clipper; progress shows below." });
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
          <Dropzone onUploaded={(s) => setSource(s.path)} />
          {sources.length > 0 && (
            <>
              <div className="mt-4 mb-2 text-xs font-medium text-muted">Or pick a video already on this PC</div>
              <div className="flex max-h-72 flex-col gap-1 overflow-y-auto">
                {sources.map((s) => (
                  <button key={s.path} onClick={() => setSource(s.path)}
                    className={cn("flex items-center gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                      source === s.path ? "border-accent bg-accent-soft" : "border-transparent hover:bg-surface-2")}>
                    <Film className="size-4 shrink-0 text-muted" />
                    <span className="min-w-0 flex-1 truncate text-sm">{s.name}</span>
                    <span className="tabular shrink-0 text-xs text-muted">
                      {s.size_mb >= 1024 ? `${(s.size_mb / 1024).toFixed(1)} GB` : `${Math.round(s.size_mb)} MB`} · {s.folder} · {ago(s.modified)}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </Step>

        <Step n={3} title="Which moments">
          <Segmented label="Which moments" value={mode} onChange={setMode}
            options={[["auto", "Let Clipper decide", `Every moment good enough, up to ${chosen?.max_clips ?? "the campaign's max"}`],
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
                {[1, 2, 3, 4, 5, 6, 8, 10].map((n) => (
                  <button key={n} onClick={() => setTop(n)}
                    className={cn("h-9 min-w-11 rounded-md border px-3 text-sm font-medium",
                      top === n ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
                    {n}
                  </button>
                ))}
                <span className="text-xs text-muted">at most; fewer if nothing else is good enough</span>
              </div>
            )}
            {mode === "manual" && <RangeEditor source={source} ranges={ranges} setRanges={setRanges} />}
          </div>
        </Step>

        <div className="flex items-center justify-end gap-3">
          {!ready && (
            <span className="text-xs text-muted">
              {!campaign ? "Pick a campaign" : !source ? "Pick a video" : "Add at least one moment's start and end"}
            </span>
          )}
          <Button variant="primary" disabled={!ready || busy} onClick={() => void make()}>
            <Scissors className="size-4" /> Make clips
          </Button>
        </div>

        <section className="mt-4">
          <h2 className="mb-3 text-md font-semibold">Jobs</h2>
          {jobs.length ? (
            <div className="flex flex-col gap-2">{jobs.map((j) => <JobCard key={j.id} job={j} />)}</div>
          ) : (
            <EmptyState icon={<Scissors className="size-5" />} title="Nothing clipping yet"
              body="A full episode takes a few minutes: transcribing, finding scenes, scoring moments, then rendering each clip." />
          )}
        </section>
      </div>
    </div>
  );
}
