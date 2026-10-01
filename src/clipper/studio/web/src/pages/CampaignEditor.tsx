import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { ArrowLeft, Loader2, Plus, Save, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  readBrief, saveCampaignBrief, useCampaign, useCampaignBrief, useCampaignForm, useDeleteCampaign, useSaveCampaign,
  type CampaignForm, type CaptionRule,
} from "@/api/client";
import {
  Field, LinesInput, NumberInput, Section, Segmented, SwitchRow, TextArea, TextInput,
} from "@/components/form";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, PageHeader, Skeleton } from "@/components/ui";
import { cn } from "@/lib/utils";

const BLANK: CampaignForm = {
  title: "", name: "", marketplace: "", campaign_url: "", reward_per_1k_usd: null,
  min_payout_usd: null, max_payout_usd: null, deadline: "", source_authorization: "",
  platform_targets: ["tiktok", "instagram_reels"], content_type: "scripted",
  min_seconds: 15, max_seconds: 60, selection_focus: "", required_caption_text: "",
  required_hashtags: [], only_required_hashtags: false, required_credit_text: "",
  fallback_captions: [], fixed_captions: false, hook_texts: [], hook_overlay: true,
  keep_original_audio: false, censor_flagged_words: true, brief_rules: "", caption_rules: [], posting_rules: [],
  long_description: true, description_context: "",
  description_keywords: [], max_clips_per_source: null, notes: "",
};

const MARKETS = ["Content Rewards", "Vyro"];
const PLATFORMS: [string, string][] = [
  ["tiktok", "TikTok"], ["instagram_reels", "Instagram Reels"], ["youtube_shorts", "YouTube Shorts"], ["x", "X"],
];

const RULE_SELECT = "h-9 rounded-md border border-line bg-surface-1 px-2 text-sm";

/** The brief's other caption rules (config.CaptionRule), one row each (D81). */
function CaptionRules({ rules, platforms, onChange }: {
  rules: CaptionRule[]; platforms: string[]; onChange: (rules: CaptionRule[]) => void;
}) {
  const update = (i: number, change: Partial<CaptionRule>) => onChange(rules.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const names = PLATFORMS.filter(([k]) => platforms.includes(k));
  return (
    <div className="flex flex-col gap-2">
      {rules.map((r, i) => (
        <div key={i} className="flex flex-col gap-1 rounded-md border border-line p-2">
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label="Must" className={RULE_SELECT} value={r.must ?? "include"}
                    onChange={(e) => update(i, { must: e.target.value as CaptionRule["must"] })}>
              <option value="include">Must include</option>
              <option value="avoid">Must never say</option>
            </select>
            <TextInput aria-label="Text" className="min-w-40 flex-1" value={r.text} placeholder="@JoshThomasChannel"
                       onChange={(e) => update(i, { text: e.target.value })} />
            <select aria-label="Where" className={RULE_SELECT} value={r.place ?? "caption"}
                    onChange={(e) => update(i, { place: e.target.value as CaptionRule["place"] })}>
              <option value="caption">in the caption</option>
              <option value="title">in the YouTube title</option>
            </select>
            <select aria-label="Platform" className={RULE_SELECT} value={(r.platforms ?? [])[0] ?? ""}
                    onChange={(e) => update(i, { platforms: e.target.value ? [e.target.value] : [] })}>
              <option value="">on every platform</option>
              {names.map(([k, label]) => <option key={k} value={k}>on {label} only</option>)}
            </select>
            <Button type="button" variant="ghost" size="icon" aria-label="Remove rule"
                    onClick={() => onChange(rules.filter((_, j) => j !== i))}><X className="size-4" /></Button>
          </div>
          {r.quote && <span className="pl-1 text-xs text-muted">The brief: “{r.quote}”</span>}
        </div>
      ))}
      <Button type="button" variant="secondary" size="sm" className="w-fit"
              onClick={() => onChange([...rules, { text: "", must: "include", place: "caption", platforms: [], quote: "" }])}>
        <Plus className="size-3.5" /> Add a rule
      </Button>
    </div>
  );
}

/** A value the brief actually gave (not empty, not the blank form's default). */
const given = (key: keyof CampaignForm, v: unknown) =>
  v !== null && v !== "" && !(Array.isArray(v) && v.length === 0) && JSON.stringify(v) !== JSON.stringify(BLANK[key]);

/** How many fields the brief filled, to tell the user what to check. */
function filled(form: CampaignForm) {
  return Object.entries(form).filter(([key, v]) => given(key as keyof CampaignForm, v)).length;
}

/** A new campaign handed over from Research ({title, brief}); read once. */
function takePrefill(): { title?: string; brief?: string; form?: CampaignForm } {
  try {
    const raw = sessionStorage.getItem("clipper.prefill");
    sessionStorage.removeItem("clipper.prefill");
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function BriefReader({ onRead, initial = "", from, saved, editing = false }: {
  onRead: (form: CampaignForm, text: string) => void; initial?: string; from?: CampaignForm;
  saved?: string | null; editing?: boolean;
}) {
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(!editing);
  const read = async () => {
    setBusy(true);
    try {
      const form = await readBrief(text);
      onRead(form, text);
      toast.success(`Filled in ${filled(form)} fields from the brief`,
        { description: "Check them below, then save." });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!open) {
    // Editing: the brief is optional, so it stays folded until asked for.
    return (
      <Card className="flex flex-wrap items-center gap-3 px-4 py-3">
        <Sparkles className="size-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 text-sm">
          {saved ? <>Full brief saved {saved.slice(0, 10)}. <span className="text-muted">Ask can answer questions about it.</span></>
            : <>No brief saved. <span className="text-muted">Paste it so Ask can answer questions about this campaign.</span></>}
        </span>
        <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>{saved ? "Paste an updated brief" : "Paste the brief"}</Button>
      </Card>
    );
  }
  return (
    <Card className="flex flex-col gap-3 border-accent/40 bg-accent-soft/40 p-5">
      <div>
        <h2 className="flex items-center gap-2 text-md font-semibold">
          <Sparkles className="size-4 text-accent" /> {editing ? "Paste the brief" : "Fastest way: paste the brief"}
        </h2>
        {from ? (
          <p className="mt-0.5 text-sm text-muted">
            The alert only had the pay and platforms. Open{" "}
            {from.campaign_url ? (
              <a href={from.campaign_url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">the campaign page</a>
            ) : "the campaign's page"}
            , copy the whole brief, and paste it here to fill in its rules. What's already filled in stays unless the brief says otherwise.
          </p>
        ) : (
          <p className="mt-0.5 text-sm text-muted">
            Copy the campaign's whole page from Content Rewards, Vyro or wherever it's posted, paste it here, and
            Clipper fills in the form for you. It skips passwords and footage links. The whole brief is kept, so
            Ask can answer questions about it later.
          </p>
        )}
      </div>
      <TextArea rows={5} value={text} onChange={(e) => setText(e.target.value)}
                placeholder="Paste the campaign brief here…" aria-label="Campaign brief" />
      <div className="flex items-center justify-end gap-3">
        {busy && <span className="text-xs text-muted">Reading the brief (about 10 seconds)…</span>}
        <Button type="button" variant="primary" disabled={busy || text.trim().length < 40} onClick={() => void read()}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          Fill in the form
        </Button>
      </div>
    </Card>
  );
}

export function CampaignEditorPage() {
  const params = useParams({ strict: false }) as { name?: string };
  const editing = params.name;
  const navigate = useNavigate();
  const { data: loaded, isLoading } = useCampaignForm(editing);
  const { data: detail } = useCampaign(editing ?? "");
  const save = useSaveCampaign();
  const remove = useDeleteCampaign();
  const [prefill] = useState(() => (editing ? {} : takePrefill()));
  const [form, setForm] = useState<CampaignForm>(() => prefill.form ?? { ...BLANK, title: prefill.title ?? "" });
  const [market, setMarket] = useState<string>(() => {
    const m = prefill.form?.marketplace;
    return !m ? "Content Rewards" : MARKETS.includes(m) ? m : "other";
  });

  useEffect(() => {
    if (loaded) {
      setForm(loaded);
      setMarket(MARKETS.includes(loaded.marketplace) ? loaded.marketplace : loaded.marketplace ? "other" : "Content Rewards");
    }
  }, [loaded]);

  const set = <K extends keyof CampaignForm>(key: K, value: CampaignForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  // The brief as pasted, saved with the campaign for Ask (D76).
  const [pasted, setPasted] = useState("");
  const { data: briefStatus } = useCampaignBrief(editing);
  const fromBrief = (read: CampaignForm, text: string) => {
    setPasted(text);
    // The brief fills what it gives; anything it doesn't (an alert's pay or link,
    // or what the user typed) is kept.
    setForm((f) => {
      const next = { ...f };
      for (const key of Object.keys(read) as (keyof CampaignForm)[]) {
        if (given(key, read[key])) (next as Record<string, unknown>)[key] = read[key];
      }
      return { ...next, name: f.name, title: read.title || f.title };
    });
    setMarket(MARKETS.includes(read.marketplace) ? read.marketplace : read.marketplace ? "other" : market);
  };

  const submit = () => {
    const marketplace = market === "other" ? form.marketplace : market;
    save.mutate({ form: { ...form, marketplace, caption_rules: (form.caption_rules ?? []).filter((r) => r.text.trim()) }, name: editing }, {
      onSuccess: async (res) => {
        if (pasted) {
          try {
            await saveCampaignBrief(res.name as string, pasted);
          } catch (e) {
            toast.error(`The brief text wasn't kept: ${(e as Error).message}`);
          }
        }
        toast.success(editing ? "Campaign saved" : `${form.title} added`,
          { description: editing ? undefined : "Next: give it footage on the New clips page." });
        void navigate({ to: "/campaigns/$name", params: { name: res.name as string } });
      },
      onError: (e) => toast.error((e as Error).message),
    });
  };

  if (editing && isLoading) {
    return <div className="flex max-w-3xl flex-col gap-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-96" /></div>;
  }

  const togglePlatform = (p: string) => set("platform_targets",
    form.platform_targets.includes(p) ? form.platform_targets.filter((x) => x !== p) : [...form.platform_targets, p]);
  const canDelete = editing && detail && detail.campaign.clips === 0;

  return (
    <div className="fade-in max-w-3xl pb-24">
      <Link to={editing ? "/campaigns/$name" : "/campaigns"} params={editing ? { name: editing } : undefined}
            className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> {editing ? "Back to the campaign" : "Campaigns"}
      </Link>
      <PageHeader title={editing ? `Edit ${loaded?.title ?? "campaign"}` : "New campaign"}
        subtitle={editing ? "Changes apply to clips made from now on." :
          "Tell Clipper about a campaign you've joined, so it clips and captions to that campaign's rules."} />

      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <BriefReader onRead={fromBrief} initial={prefill.brief} from={prefill.form}
                     editing={!!editing} saved={briefStatus?.saved_at} />

        <Section title="The basics">
          <Field label="Campaign name" hint={editing ? "Rename it any time; its clips stay with it." : "What you'll call it in Clipper, e.g. \"Chad Powers S2\"."}>
            {(id) => <TextInput id={id} required value={form.title} onChange={(e) => set("title", e.target.value)}
                                placeholder="Chad Powers S2" autoFocus={!editing} />}
          </Field>
          <Field label="Where it runs">
            {() => (
              <div className="flex flex-wrap gap-2">
                {[...MARKETS, "other"].map((m) => (
                  <button key={m} type="button" onClick={() => setMarket(m)}
                    className={cn("h-9 rounded-md border px-3 text-sm font-medium",
                      market === m ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
                    {m === "other" ? "Somewhere else" : m}
                  </button>
                ))}
                {market === "other" && (
                  <TextInput className="w-56" value={form.marketplace} aria-label="Where it runs"
                             onChange={(e) => set("marketplace", e.target.value)} placeholder="e.g. Clipping.gg" />
                )}
              </div>
            )}
          </Field>
          <Field label="Campaign page link" optional hint="Opens from the campaign's page, for submitting links.">
            {(id) => <TextInput id={id} type="url" value={form.campaign_url} placeholder="https://whop.com/…"
                                onChange={(e) => set("campaign_url", e.target.value)} />}
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Pay per 1,000 views" optional>
              {(id) => <NumberInput id={id} prefix="$" step="0.01" min="0" value={form.reward_per_1k_usd}
                                    onChange={(v) => set("reward_per_1k_usd", v)} placeholder="2.50" />}
            </Field>
            <Field label="Minimum payout" optional>
              {(id) => <NumberInput id={id} prefix="$" step="0.01" min="0" value={form.min_payout_usd}
                                    onChange={(v) => set("min_payout_usd", v)} />}
            </Field>
            <Field label="Most per post" optional>
              {(id) => <NumberInput id={id} prefix="$" step="1" min="0" value={form.max_payout_usd}
                                    onChange={(v) => set("max_payout_usd", v)} />}
            </Field>
          </div>
          <Field label="Ends on" optional className="sm:w-1/3">
            {(id) => <TextInput id={id} type="date" value={form.deadline} onChange={(e) => set("deadline", e.target.value)} />}
          </Field>
        </Section>

        <Section title="Posting" hint="Where the clips go and what every caption needs.">
          <Field label="Platforms the campaign pays for">
            {() => (
              <div className="flex flex-wrap gap-2">
                {PLATFORMS.map(([key, label]) => (
                  <button key={key} type="button" aria-pressed={form.platform_targets.includes(key)} onClick={() => togglePlatform(key)}
                    className={cn("flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium",
                      form.platform_targets.includes(key) ? "border-accent bg-accent-soft text-fg" : "border-line text-muted hover:text-fg")}>
                    <PlatformIcon platform={key} className="size-4" /> {label}
                  </button>
                ))}
              </div>
            )}
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Every caption must include" optional hint={<>Often <b>#ad</b> for paid partnerships.</>}>
              {(id) => <TextInput id={id} value={form.required_caption_text} placeholder="#ad"
                                  onChange={(e) => set("required_caption_text", e.target.value)} />}
            </Field>
            <Field label="Credit line" optional hint="If the brief asks you to credit the source.">
              {(id) => <TextInput id={id} value={form.required_credit_text} placeholder="Source: @creator"
                                  onChange={(e) => set("required_credit_text", e.target.value)} />}
            </Field>
          </div>
          <Field label="Required hashtags" optional hint="Separate with spaces or commas.">
            {(id) => <TextInput id={id} value={form.required_hashtags.join(" ")} placeholder="#chadpowers #hulu"
                                onChange={(e) => set("required_hashtags", e.target.value.split(/[\s,]+/).filter(Boolean))} />}
          </Field>
          <SwitchRow label="Use only these hashtags" hint="For briefs that ban extra hashtags."
                     checked={form.only_required_hashtags} onChange={(v) => set("only_required_hashtags", v)} />
          <SwitchRow label="Mask words that get posts flagged"
                     hint="Sexual terms, slurs, self-harm and hard drugs get a letter masked (d*ck, p*rn) in captions, titles and hook lines. Swearing stays."
                     checked={form.censor_flagged_words ?? true} onChange={(v) => set("censor_flagged_words", v)} />
          <Field label="Other caption rules" optional
                 hint="Anything else the brief says a post must say or must not, on one platform or all: an @mention, a tag in the YouTube title, a banned word. Every clip is checked against each.">
            {() => <CaptionRules rules={form.caption_rules ?? []} platforms={form.platform_targets}
                                 onChange={(v) => set("caption_rules", v)} />}
          </Field>
          <Field label="Rules for when you post" optional
                 hint="One per line: what only you can do, like keeping comments on or turning on the paid-partnership label. Shown as a checklist when posting.">
            {(id) => <LinesInput id={id} rows={3} value={form.posting_rules ?? []} onChange={(v) => set("posting_rules", v)} />}
          </Field>
        </Section>

        <Section title="What to clip" hint="How Clipper picks and edits the moments.">
          <Field label="Kind of footage">
            {() => (
              <Segmented label="Kind of footage" value={form.content_type}
                onChange={(v) => set("content_type", v)}
                options={[["scripted", "TV show or film", "Scenes kept as they are"],
                          ["podcast", "Podcast or talk", "Pauses and filler words trimmed"],
                          ["other", "Something else", "Light editing"]]} />
            )}
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Shortest clip">
              {(id) => <NumberInput id={id} suffix="sec" min="3" value={form.min_seconds}
                                    onChange={(v) => set("min_seconds", v ?? 15)} />}
            </Field>
            <Field label="Longest clip">
              {(id) => <NumberInput id={id} suffix="sec" min="5" value={form.max_seconds}
                                    onChange={(v) => set("max_seconds", v ?? 60)} />}
            </Field>
            <Field label="Most clips per video" optional hint="When Clipper decides how many. Empty: every moment good enough.">
              {(id) => <NumberInput id={id} min="1" max="500" value={form.max_clips_per_source} placeholder="No limit"
                                    onChange={(v) => set("max_clips_per_source", v)} />}
            </Field>
          </div>
          <Field label="What should the clips be about?" optional
                 hint="In the brief's words: who, what kind of moments, and what gets rejected. Clipper scores every moment against this.">
            {(id) => <TextArea id={id} rows={4} value={form.selection_focus} onChange={(e) => set("selection_focus", e.target.value)}
                               placeholder="Only Ricky and Russ moments: chemistry, banter, tension. Football-only and comedy-only clips get rejected." />}
          </Field>
          <Field label="The brief's rules about editing" optional
                 hint="Paste its wording (e.g. &quot;no jump cuts&quot;, &quot;don't add music&quot;). Clipper won't make an edit it forbids.">
            {(id) => <TextArea id={id} rows={2} value={form.brief_rules} onChange={(e) => set("brief_rules", e.target.value)} />}
          </Field>
          <SwitchRow label="Keep the original audio exactly" hint="No loudness change. For briefs that forbid touching the audio."
                     checked={form.keep_original_audio} onChange={(v) => set("keep_original_audio", v)} />
        </Section>

        <Section title="Captions and on-screen text" hint="Leave empty and Clipper writes its own for each clip.">
          <Field label="Approved captions" optional hint="One per line. Clipper rotates through them.">
            {(id) => <LinesInput id={id} rows={3} value={form.fallback_captions} onChange={(v) => set("fallback_captions", v)} />}
          </Field>
          <SwitchRow label="Always use these captions" hint="Off: only when Clipper has none of its own."
                     checked={form.fixed_captions} onChange={(v) => set("fixed_captions", v)} />
          <Field label="On-screen hook lines" optional hint="One per line: the text shown over a clip's first seconds.">
            {(id) => <LinesInput id={id} rows={3} value={form.hook_texts} onChange={(v) => set("hook_texts", v)} />}
          </Field>
          <SwitchRow label="Show a hook line at the start" checked={form.hook_overlay} onChange={(v) => set("hook_overlay", v)} />
          <SwitchRow label="Add a longer, searchable description" hint="2–3 sentences under the caption. TikTok says longer descriptions get found more."
                     checked={form.long_description} onChange={(v) => set("long_description", v)} />
          {form.long_description && (
            <>
              <Field label="What is it?" optional hint="A sentence or two on the show or creator, so descriptions get the facts right.">
                {(id) => <TextArea id={id} rows={2} value={form.description_context} onChange={(e) => set("description_context", e.target.value)} />}
              </Field>
              <Field label="Search words" optional hint="Names people search for: show, cast, creator. One per line.">
                {(id) => <LinesInput id={id} rows={3} value={form.description_keywords} onChange={(v) => set("description_keywords", v)} />}
              </Field>
            </>
          )}
        </Section>

        <Section title="Permission" hint="Clipper only clips footage you're allowed to use, and keeps this note as the record.">
          <Field label="Why you're allowed to clip this" optional
                 hint="Leave empty to use: official footage supplied by this campaign.">
            {(id) => <TextArea id={id} rows={2} value={form.source_authorization}
                               onChange={(e) => set("source_authorization", e.target.value)}
                               placeholder="Official footage supplied by the campaign on Content Rewards." />}
          </Field>
        </Section>

        <Section title="Notes" hint="Anything else from the brief: eligibility, posting rules, payout conditions.">
          <TextArea rows={4} value={form.notes} onChange={(e) => set("notes", e.target.value)} aria-label="Notes" />
        </Section>

        <div className="sticky bottom-0 z-10 -mx-4 flex items-center justify-between gap-3 border-t border-line bg-bg/90 px-4 py-3 backdrop-blur md:-mx-8 md:px-8">
          <div>
            {canDelete && (
              <Button type="button" variant="ghost" className="hover:text-danger"
                onClick={() => remove.mutate(editing!, {
                  onSuccess: () => { toast("Campaign deleted", { description: "Its file is in the Recycle Bin." }); void navigate({ to: "/campaigns" }); },
                  onError: (e) => toast.error((e as Error).message),
                })}>
                <Trash2 className="size-4" /> Delete campaign
              </Button>
            )}
          </div>
          <div className="flex items-center gap-2">
            {!form.title.trim() && <span className="text-xs text-muted">Give it a name to save</span>}
            <Button type="submit" variant="primary" disabled={!form.title.trim() || save.isPending}>
              {save.isPending ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              {editing ? "Save changes" : "Add campaign"}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}
