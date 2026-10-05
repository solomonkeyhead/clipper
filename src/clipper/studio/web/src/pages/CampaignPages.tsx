import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { AlertTriangle, Archive, ArchiveRestore, BarChart3, CalendarClock, ExternalLink, Megaphone, Pencil, Plus, Scissors, Search, ShieldCheck, Trash2, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { useState } from "react";
import {
  useAddPayout, useCampaign, useCampaigns, useDeletePayout, useFound, usePayouts, useSetCampaign, useUses, type Brief, type Campaign,
} from "@/api/client";
import { ClipGrid } from "@/components/ClipGrid";
import { PlatformIcon } from "@/components/PlatformIcon";
import { BackLink, Button, Card, Chip, CopyButton, EmptyState, PageHeader, Skeleton, Tip } from "@/components/ui";
import { FindCampaigns } from "./FindCampaigns";
import { PostTable } from "./StatsPage";
import { ClipFilters, NoClips, firstFilter, inFilter, type ClipFilter } from "./WorkPages";
import { PLATFORM_NAME, ago, cn, formatCount, formatMoney } from "@/lib/utils";

function CampaignCard({ c }: { c: Campaign }) {
  return (
    <Link to="/campaigns/$name" params={{ name: c.name }}
          className="group flex flex-col gap-4 rounded-lg border border-line bg-surface-1 p-5 shadow-1 transition-colors duration-[var(--dur-base)] hover:border-line-strong hover:bg-surface-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-md font-semibold">{c.title}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-muted">
            {c.platforms.map((p) => (
              <Tip key={p} label={PLATFORM_NAME[p] ?? p}><span><PlatformIcon platform={p} /></span></Tip>
            ))}
            {c.marketplace && <span className="ml-1 text-xs whitespace-nowrap">{c.marketplace}</span>}
            {c.last_post && <span className="text-xs whitespace-nowrap">· last post {ago(c.last_post)}</span>}
          </div>
        </div>
        {c.reward_per_1k_usd !== null && c.reward_per_1k_usd !== undefined && (
          <div className="text-right">
            <div className="tabular text-lg font-semibold text-money">{formatMoney(c.reward_per_1k_usd)}</div>
            <div className="text-xs text-muted">per 1K views</div>
          </div>
        )}
      </div>
      <div className="tabular grid grid-cols-4 gap-2 text-center">
        {([["Ready", c.counts.ready], ["To submit", c.counts.posted], ["Submitted", c.counts.submitted],
           ["Views", formatCount(c.views)]] as const).map(([label, value]) => (
          <div key={label} className="rounded-md bg-surface-2 py-2">
            <div className="text-md font-semibold">{value}</div>
            <div className="truncate px-1 text-[11px] text-muted">{label}</div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted">
          {c.est_earnings !== null && c.est_earnings !== undefined
            ? <>Est. <span className="text-money">{formatMoney(c.est_earnings)}</span> so far</> : " "}
        </span>
        <span className="flex flex-wrap justify-end gap-1.5">
          {c.warning && <Chip tone="danger" title={c.warning}>{c.warning.split(" · ")[0]}</Chip>}
          {c.locked_usd > 0 && <Chip tone="warning" title="Earned on paper, but under this campaign's minimum payout, so it pays nothing yet">{formatMoney(c.locked_usd)} under minimum</Chip>}
          {c.to_submit > 0 && <Chip tone="warning">{c.to_submit} link{c.to_submit === 1 ? "" : "s"} to submit</Chip>}
        </span>
      </div>
    </Link>
  );
}

function NewCampaignButton() {
  return (
    <Link to="/campaigns/new"
          className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
      <Plus className="size-4" /> New campaign
    </Link>
  );
}

export function CampaignsPage() {
  const { data, isLoading } = useCampaigns();
  const { data: found = [] } = useFound();
  const search = useSearch({ from: "/campaigns" });
  const navigate = useNavigate({ from: "/campaigns" });
  const uses = useUses();
  const finding = search.find === "1" && uses.finder;
  const [showArchived, setShowArchived] = useState(false);
  // The user's own channel (German Professor) isn't a paid campaign: listed on its own (D140).
  const list = (data ?? []).filter((c) => c.archived === showArchived && !c.own_channel);
  const channels = (data ?? []).filter((c) => c.own_channel && !c.archived);
  const archivedCount = (data ?? []).filter((c) => c.archived).length;
  return (
    <div className="fade-in">
      <PageHeader title="Campaigns" subtitle="Each campaign's pay, rules and progress."
        actions={<>
          {archivedCount > 0 && (
            <Button variant="ghost" onClick={() => setShowArchived((v) => !v)}>
              {showArchived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
              {showArchived ? "Active campaigns" : `Archived (${archivedCount})`}
            </Button>
          )}
          {uses.finder && (
            <Button variant={finding ? "secondary" : "ghost"}
                    onClick={() => void navigate({ search: { find: finding ? undefined : "1" } })}>
              <Search className="size-4" /> Find campaigns
              {found.length > 0 && <span className="tabular rounded-full bg-accent-soft px-1.5 text-[11px] font-semibold text-accent">{found.length}</span>}
            </Button>
          )}
          <NewCampaignButton />
        </>} />
      {finding && <FindCampaigns />}
      {isLoading ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-48" />)}</div>
      ) : list.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">{list.map((c) => <CampaignCard key={c.name} c={c} />)}</div>
      ) : (
        <EmptyState icon={<Megaphone className="size-5" />} title={showArchived ? "Nothing archived" : "No campaigns yet"}
          body={showArchived ? undefined : "Joined a campaign on Content Rewards or Vyro? Add it here: paste its brief and Clipper fills in the rules."}
          action={showArchived ? undefined : <NewCampaignButton />} />
      )}
      {!showArchived && !isLoading && channels.length > 0 && (
        <section className="mt-8 flex flex-col gap-3">
          <div>
            <h2 className="text-sm font-semibold">Your channel</h2>
            <p className="text-sm text-muted">Your own videos, made in Create. Not a paid campaign, so no pay or rules.</p>
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">{channels.map((c) => <CampaignCard key={c.name} c={c} />)}</div>
        </section>
      )}
    </div>
  );
}

function Rule({ ok = true, children }: { ok?: boolean; children: React.ReactNode }) {
  return (
    <li className="flex gap-2 text-sm">
      <ShieldCheck className={cn("mt-0.5 size-4 shrink-0", ok ? "text-success" : "text-subtle")} />
      <span>{children}</span>
    </li>
  );
}

function BriefPanel({ brief }: { brief: Brief }) {
  return (
    <aside className="flex flex-col gap-5 lg:sticky lg:top-0 lg:max-h-[calc(100dvh-8rem)] lg:overflow-y-auto">
      <Card className="flex flex-col gap-4 p-4">
        <h2 className="text-sm font-semibold">Brief checklist</h2>
        <ul className="flex flex-col gap-2">
          <Rule>{brief.min_seconds}–{brief.max_seconds}s long, vertical 9:16</Rule>
          {brief.required_text && <Rule>Caption includes <b>{brief.required_text}</b>{brief.required_text.includes("#ad") && " and TikTok's paid-partnership toggle is on"}</Rule>}
          {brief.credit && <Rule>Credit: {brief.credit}</Rule>}
          {brief.original_audio && <Rule>Original audio kept</Rule>}
          {(brief.caption_rules ?? []).map((r) => (
            <Rule key={`${r.must}${r.text}${r.place}${(r.platforms ?? []).join()}`}>
              {r.must === "avoid" ? "Never says" : r.place === "title" ? "YouTube title includes" : "Caption includes"}{" "}
              <b>{r.text}</b>
              {(r.platforms ?? []).length > 0 && r.place !== "title" && <> on {(r.platforms ?? []).map((p) => PLATFORM_NAME[p] ?? p).join(", ")}</>}
            </Rule>
          ))}
          {(brief.posting_rules ?? []).map((r) => <Rule key={r}>{r}</Rule>)}
        </ul>
        {brief.hashtags.length > 0 && (
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs font-medium text-muted">Required hashtags</span>
              <CopyButton text={brief.hashtags.join(" ")} what="Hashtags" variant="ghost" />
            </div>
            <div className="flex flex-wrap gap-1.5">{brief.hashtags.map((h) => <Chip key={h}>{h}</Chip>)}</div>
          </div>
        )}
      </Card>
      {brief.captions.length > 0 && (
        <Card className="flex flex-col gap-2 p-4">
          <h2 className="text-sm font-semibold">Approved captions</h2>
          {brief.captions.map((c) => (
            <div key={c} className="group flex items-start gap-2 rounded-md p-1.5 hover:bg-surface-2">
              <span className="flex-1 text-sm">{c}</span>
              <CopyButton text={c} what="Caption" variant="ghost" />
            </div>
          ))}
        </Card>
      )}
      {brief.hook_texts.length > 0 && (
        <Card className="flex flex-col gap-1.5 p-4">
          <h2 className="text-sm font-semibold">On-screen lines</h2>
          {brief.hook_texts.map((h) => <p key={h} className="text-sm text-muted">“{h}”</p>)}
        </Card>
      )}
      {brief.notes && (
        <Card className="p-4">
          <h2 className="mb-1.5 text-sm font-semibold">Notes</h2>
          <p className="text-sm whitespace-pre-wrap text-muted">{brief.notes}</p>
        </Card>
      )}
    </aside>
  );
}

/** Estimated vs actually paid, the real rate, and whether it's about to stop paying (D99, D100). */
function MoneyCard({ c }: { c: Campaign }) {
  const { data: payouts = [] } = usePayouts(c.name);
  const add = useAddPayout();
  const remove = useDeletePayout();
  const setCampaign = useSetCampaign();
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [budget, setBudget] = useState<string | null>(null);
  const saveBudget = () => {
    if (budget === null) return;
    const value = budget.trim() === "" ? null : Number(budget.replace(/[$,]/g, ""));
    if (value !== null && (!Number.isFinite(value) || value < 0)) return void toast.error("Budget left is a dollar amount");
    setCampaign.mutate({ name: c.name, budget_left: value }, { onSuccess: () => setBudget(null) });
  };
  return (
    <Card className="mb-5 flex flex-col gap-4 p-4">
      {c.warning && (
        <p className="flex items-center gap-2 rounded-md bg-[color-mix(in_oklch,var(--warning)_12%,transparent)] px-3 py-2 text-sm text-warning">
          <AlertTriangle className="size-4 shrink-0" /> {c.warning}. Clips posted after a campaign stops paying earn nothing.
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <div><div className="text-xs text-muted">Estimated</div>
          <div className="tabular text-lg font-semibold">{c.est_earnings != null ? formatMoney(c.est_earnings) : "–"}</div></div>
        <div><div className="text-xs text-muted">Paid</div>
          <div className="tabular text-lg font-semibold text-money">{c.paid_usd != null ? formatMoney(c.paid_usd) : "–"}</div></div>
        <Tip label="What it actually paid per 1,000 of your posts' views, against its stated rate">
          <div><div className="text-xs text-muted">Real rate</div>
            <div className="tabular text-lg font-semibold">{c.paid_per_1k != null ? `${formatMoney(c.paid_per_1k)}/1K` : "–"}</div></div>
        </Tip>
        <div>
          <div className="text-xs text-muted">Budget left{c.deadline && <> · ends {c.deadline}</>}</div>
          {budget === null ? (
            <button type="button" className="tabular text-lg font-semibold hover:text-accent" onClick={() => setBudget(c.budget_left != null ? String(c.budget_left) : "")}
                    title={c.budget_checked_at ? `Checked ${ago(c.budget_checked_at)}` : "Not set: copy it from the campaign's page"}>
              {c.budget_left != null ? formatMoney(c.budget_left) : <span className="text-sm font-normal text-accent">Set</span>}
            </button>
          ) : (
            <input autoFocus value={budget} onChange={(e) => setBudget(e.target.value)} onBlur={saveBudget}
                   onKeyDown={(e) => { e.stopPropagation(); if (e.key === "Enter") saveBudget(); if (e.key === "Escape") setBudget(null); }}
                   placeholder="$ left" aria-label="Budget left"
                   className="h-8 w-28 rounded-sm border border-line bg-surface-1 px-2 text-sm focus:border-accent focus:outline-none" />
          )}
        </div>
      </div>
      {c.locked_usd > 0 && (
        <p className="text-xs text-muted">
          <b className="text-warning">{formatMoney(c.locked_usd)}</b> is earned on paper by {c.locked_posts} post{c.locked_posts === 1 ? "" : "s"} still under
          the {c.min_payout_usd != null ? formatMoney(c.min_payout_usd) : "campaign's"} minimum payout. It pays nothing until each reaches it, so it isn't in Estimated.
        </p>
      )}
      <details>
        <summary className="cursor-pointer text-xs text-muted hover:text-fg">
          Payouts {payouts.length > 0 && `(${payouts.length})`}: record what the campaign actually paid
        </summary>
        <form className="mt-2 flex flex-wrap items-center gap-2" onSubmit={(e) => {
          e.preventDefault();
          const value = Number(amount.replace(/[$,]/g, ""));
          if (!Number.isFinite(value) || value <= 0) return void toast.error("Enter what it paid, in dollars");
          add.mutate({ campaign: c.name, amount: value, paid_on: paidOn },
                     { onSuccess: () => setAmount(""), onError: (err) => toast.error((err as Error).message) });
        }}>
          <input value={amount} onChange={(e) => setAmount(e.target.value)} onKeyDown={(e) => e.stopPropagation()}
                 placeholder="$ amount" aria-label="Amount paid"
                 className="h-8 w-28 rounded-sm border border-line bg-surface-1 px-2 text-sm focus:border-accent focus:outline-none" />
          <input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} aria-label="Paid on"
                 className="h-8 rounded-sm border border-line bg-surface-1 px-2 text-sm focus:border-accent focus:outline-none" />
          <Button size="sm" type="submit" variant="secondary" disabled={!amount.trim() || add.isPending}>Add payout</Button>
        </form>
        {payouts.length > 0 && (
          <ul className="mt-2 flex flex-col divide-y divide-line text-sm">
            {payouts.map((p) => (
              <li key={p.id} className="flex items-center gap-3 py-1.5">
                <span className="tabular w-24 text-money">{formatMoney(p.amount)}</span>
                <span className="flex-1 text-muted">{p.paid_on}{p.note && ` · ${p.note}`}</span>
                <Button size="icon" variant="ghost" className="size-7" aria-label="Remove payout" onClick={() => remove.mutate(p.id)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </details>
    </Card>
  );
}

export function CampaignPage() {
  const { name } = useParams({ from: "/campaigns/$name" });
  const { data, isLoading, error } = useCampaign(name);
  const setCampaign = useSetCampaign();
  const [tab, setTab] = useState<"clips" | "posts">("clips");
  const [filter, setFilter] = useState<ClipFilter | null>(null);

  if (isLoading) return <div className="flex flex-col gap-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-96" /></div>;
  if (error || !data) return <EmptyState icon={<Megaphone className="size-5" />} title="Campaign not found" />;
  const c = data.campaign;
  const brief = data.brief;
  const clips = data.clips;
  const posts = data.clips.flatMap((x) => x.posts);
  const showing = filter ?? firstFilter(clips);
  const shown = clips.filter((x) => inFilter(x, showing));

  return (
    <div className="fade-in">
      <BackLink to="/campaigns">All campaigns</BackLink>
      <PageHeader
        title={<span className="flex items-center gap-3">{c.title}{c.archived && <Chip>Archived</Chip>}</span>}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {c.reward_per_1k_usd != null && <span><b className="text-money">{formatMoney(c.reward_per_1k_usd)}</b> per 1K views</span>}
            {brief?.min_payout_usd != null && <span>min payout {formatMoney(brief.min_payout_usd)}</span>}
            {brief?.max_payout_usd != null && <span>max {formatMoney(brief.max_payout_usd)} / post</span>}
            {brief?.deadline && <span className="flex items-center gap-1"><CalendarClock className="size-3.5" /> ends {brief.deadline}</span>}
            <span className="flex items-center gap-1.5">{c.platforms.map((p) => <PlatformIcon key={p} platform={p} />)}</span>
          </span>
        }
        actions={<>
          {/* The next step from here: clip this campaign's footage, or (your own channel) write a Short. */}
          {!c.archived && (c.own_channel
            ? <Link to="/create" className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
                <Wand2 className="size-4" /> Make a Short
              </Link>
            : <Link to="/new" search={{ campaign: c.name }} className="inline-flex h-9 items-center gap-1.5 rounded-sm bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover">
                <Scissors className="size-4" /> Make clips
              </Link>)}
          {posts.length > 0 && (
            <Link to="/stats" search={{ campaign: c.name }}
                  className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
              <BarChart3 className="size-4" /> Stats
            </Link>
          )}
          {brief?.campaign_url && (
            <a href={brief.campaign_url} target="_blank" rel="noopener noreferrer"
               className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
              <ExternalLink className="size-4" /> Campaign page
            </a>
          )}
          {c.has_brief && (
            <Link to="/campaigns/$name/edit" params={{ name: c.name }}
                  className="inline-flex h-9 items-center gap-1.5 rounded-sm border border-line bg-surface-2 px-3.5 text-sm font-medium hover:bg-surface-3">
              <Pencil className="size-4" /> Edit
            </Link>
          )}
          <Button variant="ghost" onClick={() => setCampaign.mutate({ name: c.name, archived: !c.archived })}>
            {c.archived ? <ArchiveRestore className="size-4" /> : <Archive className="size-4" />}
            {c.archived ? "Unarchive" : "Archive"}
          </Button>
        </>}
      />

      <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
        {brief ? <BriefPanel brief={brief} /> : <div />}
        <div className="min-w-0">
          <MoneyCard c={c} />
          <div className="mb-4 flex gap-1 border-b border-line" role="tablist">
            {([["clips", `Clips ${data.clips.length}`], ["posts", `Posts ${posts.length}`]] as const).map(([key, label]) => (
              <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
                className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium",
                  tab === key ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg")}>
                {label}
              </button>
            ))}
          </div>
          {tab === "clips" ? (
            clips.length ? <>
              <ClipFilters clips={clips} value={showing} onChange={setFilter} />
              {shown.length ? <ClipGrid clips={shown} /> : <NoClips filter={showing} campaign={c.name} />}
            </> : <EmptyState icon={<Megaphone className="size-5" />} title="No clips yet"
              body="Give Clipper this campaign's footage and the clips land here."
              action={<Link to="/new" search={{ campaign: c.name }} className="text-sm font-medium text-accent hover:underline">Make clips →</Link>} />
          ) : (
            <PostTable posts={posts} />
          )}
        </div>
      </div>
    </div>
  );
}
