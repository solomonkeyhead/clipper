import { useNavigate } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleHelp, ExternalLink, Loader2, Plus, Search, X, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  checkCampaign, checkFound, dismissFound, prefillCampaign, useFound,
  type CampaignCheck, type FoundCampaign,
} from "@/api/client";
import { CampaignAlerts } from "@/components/alerts";
import { TextArea } from "@/components/form";
import { Button, Card, Chip } from "@/components/ui";
import { ago, cn } from "@/lib/utils";

const VERDICT = {
  good: { text: "Good fit", tone: "success" as const },
  check: { text: "Check these first", tone: "warning" as const },
  poor: { text: "Poor fit", tone: "danger" as const },
};

function FitResult({ result, onDismiss }: { result: CampaignCheck; onDismiss?: () => void }) {
  const navigate = useNavigate();
  const v = VERDICT[result.fit.verdict as keyof typeof VERDICT] ?? VERDICT.check;
  const add = () => {
    prefillCampaign({ form: result.form });
    void navigate({ to: "/campaigns/new" });
  };
  return (
    <div className="flex flex-col gap-3 rounded-md border border-line bg-surface-1 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-md font-semibold">{result.form.title || "Untitled campaign"}</div>
          <div className="text-xs text-muted">
            {[result.form.marketplace, result.form.reward_per_1k_usd != null && `$${result.form.reward_per_1k_usd.toFixed(2)} per 1K views`]
              .filter(Boolean).join(" · ")}
          </div>
        </div>
        <Chip tone={v.tone}>{v.text}</Chip>
      </div>
      <ul className="flex flex-col gap-1.5">
        {result.fit.checks.map((c, i) => (
          <li key={i} className="flex gap-2 text-sm">
            {c.ok === true ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
              : c.ok === false ? <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />
              : <CircleHelp className="mt-0.5 size-4 shrink-0 text-subtle" />}
            <span className={cn(c.ok === null && "text-muted")}>{c.text}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-subtle">Grey items are rules to check yourself before joining.</p>
      <div className="flex flex-wrap justify-end gap-2">
        {onDismiss && <Button variant="ghost" onClick={onDismiss}>Not for me</Button>}
        <Button variant="primary" onClick={add}><Plus className="size-4" /> Add campaign</Button>
      </div>
    </div>
  );
}

function CheckBox() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CampaignCheck | null>(null);
  const run = async () => {
    setBusy(true);
    try {
      setResult(await checkCampaign(text));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div>
        <h2 className="text-md font-semibold">Check a campaign</h2>
        <p className="mt-0.5 text-sm text-muted">
          Found one on Content Rewards, Vyro, Discord or X? Paste its page or brief to see how it fits you before joining.
        </p>
      </div>
      <TextArea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the campaign here…" aria-label="Campaign to check" />
      <div className="flex justify-end">
        <Button variant="primary" disabled={busy || text.trim().length < 40} onClick={() => void run()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />} Check the fit
        </Button>
      </div>
      {result && <FitResult result={result} />}
    </Card>
  );
}

function FoundRow({ found }: { found: FoundCampaign }) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CampaignCheck | null>(null);
  const dismiss = () => void dismissFound(found.key).then(() => qc.invalidateQueries({ queryKey: ["found"] }));
  const check = async () => {
    setBusy(true);
    try {
      setResult(await checkFound(found.key));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col gap-2 border-t border-line py-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{found.name || "Unnamed campaign"}</div>
          <div className="text-xs text-muted">
            {[found.source !== "other" && found.source[0].toUpperCase() + found.source.slice(1), found.rate,
              found.platforms.join(", "), `found ${ago(found.found_at)}${found.via === "discord" ? " on Discord" : " by email"}`]
              .filter(Boolean).join(" · ")}
          </div>
          {found.why && <p className="mt-1 text-xs text-muted">{found.why}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {found.link && (
            <a href={found.link} target="_blank" rel="noopener noreferrer" aria-label="Open the campaign"
               className="grid size-8 place-items-center rounded-sm text-muted hover:bg-surface-2 hover:text-fg">
              <ExternalLink className="size-4" />
            </a>
          )}
          {!result && (
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => void check()}>
              {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />} Check the fit
            </Button>
          )}
          <Button size="icon" variant="ghost" className="size-8" aria-label="Dismiss" onClick={dismiss}><X className="size-4" /></Button>
        </div>
      </div>
      {result && <FitResult result={result} onDismiss={dismiss} />}
    </div>
  );
}

export function FindCampaigns() {
  const { data: found = [] } = useFound();
  return (
    <>
    <CampaignAlerts />
    <div className={cn("mb-6 grid gap-4", found.length > 0 && "lg:grid-cols-2")}>
      <CheckBox />
      {found.length > 0 && (
        <Card className="p-5">
          <h2 className="mb-3 text-md font-semibold">New campaigns from your alerts</h2>
          {found.map((f) => <FoundRow key={f.key} found={f} />)}
        </Card>
      )}
    </div>
    </>
  );
}
