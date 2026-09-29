import { Link } from "@tanstack/react-router";
import { AlertTriangle, CheckCircle2, Film, Loader2, Scissors, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import {
  startJob, uploadVideo, useCampaigns, useJobs, useSources, type Job, type Source,
} from "@/api/client";
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

function JobCard({ job }: { job: Job }) {
  const running = job.status === "running" || job.status === "queued";
  return (
    <Card className="flex flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold">{job.name}</div>
          <div className="text-xs text-muted">{job.campaign} · {job.top} clips · started {ago(job.created)}</div>
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
          View the clips →
        </Link>
      )}
    </Card>
  );
}

export function NewClipsPage() {
  const { data: campaigns = [] } = useCampaigns();
  const { data: sources = [] } = useSources();
  const { data: jobs = [] } = useJobs();
  const qc = useQueryClient();
  const active = campaigns.filter((c) => c.has_brief && !c.archived);
  const [campaign, setCampaign] = useState("");
  const [source, setSource] = useState("");
  const [top, setTop] = useState(4);
  const [busy, setBusy] = useState(false);
  const chosen = active.find((c) => c.name === campaign);

  const make = async () => {
    setBusy(true);
    try {
      await startJob(campaign, source, top);
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
        <Step n={1} title="Campaign">
          {active.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {active.map((c) => (
                <button key={c.name} onClick={() => setCampaign(c.name)}
                  className={cn("flex items-center justify-between gap-3 rounded-md border px-3 py-2.5 text-left transition-colors",
                    campaign === c.name ? "border-accent bg-accent-soft" : "border-line hover:bg-surface-2")}>
                  <span className="truncate text-sm font-medium">{c.name}</span>
                  <span className="flex items-center gap-1.5 text-muted">
                    {c.platforms.map((p) => <PlatformIcon key={p} platform={p} className="size-3.5" />)}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted">No active campaigns with a brief. Add the campaign's brief under campaigns/ first.</p>
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

        <Step n={3} title="How many clips">
          <div className="flex flex-wrap items-center gap-2">
            {[2, 3, 4, 5, 6, 8].map((n) => (
              <button key={n} onClick={() => setTop(n)}
                className={cn("h-9 min-w-11 rounded-md border px-3 text-sm font-medium",
                  top === n ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
                {n}
              </button>
            ))}
            <span className="text-xs text-muted">the best moments, up to this many; fewer if nothing else is good enough</span>
          </div>
        </Step>

        <div className="flex items-center justify-end gap-3">
          {!campaign || !source ? <span className="text-xs text-muted">Pick a campaign and a video</span> : null}
          <Button variant="primary" disabled={!campaign || !source || busy} onClick={() => void make()}>
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
