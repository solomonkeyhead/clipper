import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Archive, ArchiveRestore, CalendarClock, ExternalLink, Megaphone, Pencil, Plus, Search, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useCampaign, useCampaigns, useFound, useSetCampaign, type Brief, type Campaign } from "@/api/client";
import { ClipGrid } from "@/components/ClipGrid";
import { PlatformIcon } from "@/components/PlatformIcon";
import { Button, Card, Chip, CopyButton, EmptyState, PageHeader, Skeleton, Tip } from "@/components/ui";
import { FindCampaigns } from "./FindCampaigns";
import { PostTable } from "./StatsPage";
import { PLATFORM_NAME, ago, cn, formatCount, formatMoney } from "@/lib/utils";

function CampaignCard({ c }: { c: Campaign }) {
  return (
    <Link to="/campaigns/$name" params={{ name: c.name }}
          className="group flex flex-col gap-4 rounded-lg border border-line bg-surface-1 p-5 shadow-1 transition-colors duration-[var(--dur-base)] hover:border-line-strong hover:bg-surface-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-md font-semibold">{c.title}</h3>
          <div className="mt-1 flex items-center gap-1.5 text-muted">
            {c.platforms.map((p) => (
              <Tip key={p} label={PLATFORM_NAME[p] ?? p}><span><PlatformIcon platform={p} /></span></Tip>
            ))}
            {c.marketplace && <span className="ml-1 text-xs">{c.marketplace}</span>}
            {c.last_post && <span className="ml-1 text-xs">· last post {ago(c.last_post)}</span>}
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
        {([["Ready", c.counts.ready], ["Posted", c.counts.posted], ["Submitted", c.counts.submitted],
           ["Views", formatCount(c.views)]] as const).map(([label, value]) => (
          <div key={label} className="rounded-md bg-surface-2 py-2">
            <div className="text-md font-semibold">{value}</div>
            <div className="text-[11px] text-muted">{label}</div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted">
          {c.est_earnings !== null && c.est_earnings !== undefined
            ? <>Est. <span className="text-money">{formatMoney(c.est_earnings)}</span> so far</> : " "}
        </span>
        {c.to_submit > 0 && <Chip tone="warning">{c.to_submit} to submit</Chip>}
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
  const finding = search.find === "1";
  const [showArchived, setShowArchived] = useState(false);
  const list = (data ?? []).filter((c) => c.archived === showArchived);
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
          <Button variant={finding ? "secondary" : "ghost"}
                  onClick={() => void navigate({ search: { find: finding ? undefined : "1" } })}>
            <Search className="size-4" /> Find campaigns
            {found.length > 0 && <span className="tabular rounded-full bg-accent-soft px-1.5 text-[11px] font-semibold text-accent">{found.length}</span>}
          </Button>
          <NewCampaignButton />
        </>} />
      {finding && <FindCampaigns />}
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-48" />)}</div>
      ) : list.length ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{list.map((c) => <CampaignCard key={c.name} c={c} />)}</div>
      ) : (
        <EmptyState icon={<Megaphone className="size-5" />} title={showArchived ? "Nothing archived" : "No campaigns yet"}
          body={showArchived ? undefined : "Joined a campaign on Content Rewards or Vyro? Add it here: paste its brief and Clipper fills in the rules."}
          action={showArchived ? undefined : <NewCampaignButton />} />
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

export function CampaignPage() {
  const { name } = useParams({ from: "/campaigns/$name" });
  const { data, isLoading, error } = useCampaign(name);
  const setCampaign = useSetCampaign();
  const [tab, setTab] = useState<"clips" | "posts">("clips");

  if (isLoading) return <div className="flex flex-col gap-4"><Skeleton className="h-10 w-72" /><Skeleton className="h-96" /></div>;
  if (error || !data) return <EmptyState icon={<Megaphone className="size-5" />} title="Campaign not found" />;
  const c = data.campaign;
  const brief = data.brief;
  const clips = data.clips;
  const posts = data.clips.flatMap((x) => x.posts);

  return (
    <div className="fade-in">
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
            clips.length ? <ClipGrid clips={clips} /> : <EmptyState icon={<Megaphone className="size-5" />} title="No clips yet"
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
