import { useNavigate, useSearch } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp, Bookmark, BookmarkCheck, Check, Copy, ExternalLink, KeyRound, Loader2, Lock, MessageSquarePlus,
  Pencil, Plus, Radar, RefreshCw, Sparkles, Trash2, X, Zap,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import {
  askResearch, deleteNiche, deleteThread, refreshNiche, runResearchAction, saveItem, saveNiche, unsaveItem,
  useNiches, useResearchStatus, useSaved, useSetKeys, useThread, useThreads,
  type Niche, type NicheBrief, type ResearchMessage, type ThreadDetail,
} from "@/api/client";
import { Field, TextArea, TextInput } from "@/components/form";
import { Button, Card, CopyButton, EmptyState, PageHeader, Skeleton, Tip } from "@/components/ui";
import { ago, cn, copyText, formatCount } from "@/lib/utils";

type Tab = "ask" | "niches" | "saved";

/** The latest research.progress event (live.ts puts it in the query cache). */
function useProgress() {
  return useQuery<{ thread_id?: number; niche_id?: number; step: string } | null>({
    queryKey: ["research", "progress"], queryFn: () => null, staleTime: Infinity, gcTime: Infinity,
  }).data;
}

/* ---------- setup: the two optional keys ---------- */

function KeySetup({ web, youtube }: { web: boolean; youtube: boolean }) {
  const save = useSetKeys();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [tavily, setTavily] = useState("");
  const [yt, setYt] = useState("");
  if (web && youtube) return null;
  const store = (values: Record<string, string>) => save.mutate(values, {
    onSuccess: () => { setTavily(""); setYt(""); void qc.invalidateQueries({ queryKey: ["research"] }); toast.success("Key saved"); },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <Card className="mb-5 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <Zap className="size-4 text-accent" />
        <p className="flex-1 text-sm">
          <b>Get live results.</b>{" "}
          <span className="text-muted">
            {!web && "Web search (what's trending now, with sources)"}{!web && !youtube && " and "}
            {!youtube && "top Shorts per niche"} need{web || youtube ? "s" : ""} a free key. Without {web || youtube ? "it" : "them"},
            Research still answers from your own clips and the AI's knowledge.
          </span>
        </p>
        <Button size="sm" variant="secondary" onClick={() => setOpen((v) => !v)}><KeyRound className="size-3.5" /> {open ? "Hide" : "Set up"}</Button>
      </div>
      {open && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {!web && (
            <div className="flex flex-col gap-2 rounded-md border border-dashed border-line p-3">
              <div className="text-sm font-semibold">Web search: Tavily <span className="font-normal text-muted">(free, 1,000 searches a month)</span></div>
              <ol className="list-decimal pl-5 text-xs text-muted">
                <li>Sign up at <a className="text-accent hover:underline" href="https://app.tavily.com" target="_blank" rel="noopener noreferrer">app.tavily.com</a> (no card needed).</li>
                <li>Copy the API key from your dashboard (starts with <code>tvly-</code>).</li>
              </ol>
              <div className="flex gap-2">
                <TextInput type="password" autoComplete="off" value={tavily} onChange={(e) => setTavily(e.target.value)} placeholder="tvly-…" aria-label="Tavily key" />
                <Button variant="primary" disabled={tavily.trim().length < 10} onClick={() => store({ TAVILY_API_KEY: tavily })}>Save</Button>
              </div>
            </div>
          )}
          {!youtube && (
            <div className="flex flex-col gap-2 rounded-md border border-dashed border-line p-3">
              <div className="text-sm font-semibold">Top Shorts: YouTube Data API <span className="font-normal text-muted">(free)</span></div>
              <ol className="list-decimal pl-5 text-xs text-muted">
                <li>At <a className="text-accent hover:underline" href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noopener noreferrer">Google Cloud</a>, pick or create a project and press <b>Enable</b>.</li>
                <li>Credentials → <b>Create credentials → API key</b>, then copy it.</li>
              </ol>
              <div className="flex gap-2">
                <TextInput type="password" autoComplete="off" value={yt} onChange={(e) => setYt(e.target.value)} placeholder="AIza…" aria-label="YouTube key" />
                <Button variant="primary" disabled={yt.trim().length < 20} onClick={() => store({ YOUTUBE_API_KEY: yt })}>Save</Button>
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

/* ---------- Ask ---------- */

const SUGGESTIONS = [
  "What's trending in TV show edits on TikTok this week?",
  "Which of my clips kept viewers watching longest, and why?",
  "Write 8 hook lines for my best-paying campaign",
  "What makes a clip go viral in comedy podcast clipping?",
];

/** "[2]" in an answer becomes a link to source 2. */
function linkCitations(text: string, sources: { url: string }[]) {
  return text.replace(/\[(\d{1,2})\](?!\()/g, (m, n) => {
    const s = sources[Number(n) - 1];
    return s ? `[[${n}]](${s.url})` : m;
  });
}

function Answer({ message, canAct }: { message: ResearchMessage; canAct: boolean }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState<number | null>(null);
  const confirm = async (index: number) => {
    setBusy(index);
    try {
      const res = await runResearchAction(message.id, index);
      toast.success(res.done);
      await qc.invalidateQueries({ queryKey: ["research", "thread"] });
      if (res.navigate === "/campaigns/new") {
        try { sessionStorage.setItem("clipper.prefill", JSON.stringify({ title: res.title, brief: res.brief })); } catch { /* private window */ }
        void navigate({ to: "/campaigns/new" });
      } else if (res.navigate) {
        void navigate({ to: res.navigate });
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="prose-answer text-sm leading-relaxed">
        <Markdown remarkPlugins={[remarkGfm]}
          components={{ a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">{children}</a> }}>
          {linkCitations(message.content, message.sources)}
        </Markdown>
      </div>
      {message.sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {message.sources.map((s, i) => (
            <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer" title={s.title}
               className="flex max-w-60 items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs text-muted hover:border-line-strong hover:text-fg">
              <span className="font-semibold">{i + 1}</span>
              <span className="truncate">{(() => { try { return new URL(s.url).hostname.replace(/^www\./, ""); } catch { return s.title; } })()}</span>
            </a>
          ))}
        </div>
      )}
      {message.actions.map((a, i) => (
        <div key={i} className="flex flex-wrap items-center gap-3 rounded-md border border-accent/40 bg-accent-soft/40 p-3">
          <Sparkles className="size-4 shrink-0 text-accent" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{a.label}</div>
            {a.type === "hook_lines" && <div className="mt-0.5 text-xs text-muted">{(a.params.lines as string[]).join(" · ")}</div>}
          </div>
          {a.done ? <span className="flex items-center gap-1 text-xs font-medium text-success"><Check className="size-3.5" /> Done</span>
            : canAct ? (
              <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => void confirm(i)}>
                {busy === i ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />} Confirm
              </Button>
            ) : <Tip label="Letting Research make changes is part of the Pro plan"><span className="flex items-center gap-1 text-xs text-muted"><Lock className="size-3.5" /> Pro</span></Tip>}
        </div>
      ))}
      <div className="flex gap-1">
        <Button size="sm" variant="ghost" onClick={() => void copyText(message.content, "Answer")}><Copy className="size-3.5" /> Copy</Button>
        <Button size="sm" variant="ghost" disabled={saved} onClick={() => void saveItem({ kind: "answer", text: message.content })
          .then(() => { setSaved(true); void qc.invalidateQueries({ queryKey: ["research", "saved"] }); toast.success("Saved"); })}>
          {saved ? <BookmarkCheck className="size-3.5" /> : <Bookmark className="size-3.5" />} {saved ? "Saved" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function Chat({ threadId, nicheId, canAct, onThread }: {
  threadId?: number; nicheId?: number; canAct: boolean; onThread: (id: number) => void;
}) {
  const qc = useQueryClient();
  const { data: thread, isLoading } = useThread(threadId);
  const { data: niches = [] } = useNiches();
  const progress = useProgress();
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [about, setAbout] = useState<number | undefined>(nicheId);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => setAbout(nicheId ?? thread?.niche_id ?? undefined), [nicheId, thread?.niche_id]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [thread?.messages.length, pending]);

  const send = async (question = text) => {
    const q = question.trim();
    if (!q || pending) return;
    setPending(q);
    setText("");
    try {
      const result = await askResearch(q, threadId, about) as ThreadDetail;
      qc.setQueryData(["research", "thread", result.id], result);
      void qc.invalidateQueries({ queryKey: ["research", "threads"] });
      if (result.id !== threadId) onThread(result.id);
    } catch (e) {
      toast.error((e as Error).message);
      setText(q);
    } finally {
      setPending(null);
    }
  };

  const messages = thread?.messages ?? [];
  const step = progress && (progress.thread_id === threadId || !threadId) ? progress.step : "";
  return (
    <div className="flex min-h-[calc(100dvh-15rem)] flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-6 pb-6">
        {threadId && isLoading ? <Skeleton className="h-40" /> : messages.length === 0 && !pending ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-5 py-10 text-center">
            <div className="grid size-12 place-items-center rounded-full bg-accent-soft text-accent"><Sparkles className="size-5" /></div>
            <div>
              <h2 className="text-lg font-semibold">Ask anything about clipping</h2>
              <p className="mt-1 max-w-md text-sm text-muted">Trends, hooks, formats, what's working in a niche, and how your own clips and campaigns are doing.</p>
            </div>
            <div className="grid w-full max-w-2xl gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => void send(s)}
                        className="rounded-md border border-line p-3 text-left text-sm text-muted transition-colors hover:border-line-strong hover:bg-surface-2 hover:text-fg">{s}</button>
              ))}
            </div>
          </div>
        ) : (
          <>
            {messages.map((m) => m.role === "user" ? (
              <div key={m.id} className="ml-auto max-w-[80%] rounded-lg bg-surface-2 px-4 py-2.5 text-sm whitespace-pre-wrap">{m.content}</div>
            ) : (
              <Answer key={m.id} message={m} canAct={canAct} />
            ))}
            {pending && (
              <>
                <div className="ml-auto max-w-[80%] rounded-lg bg-surface-2 px-4 py-2.5 text-sm whitespace-pre-wrap">{pending}</div>
                <div className="flex items-center gap-2 text-sm text-muted">
                  <Loader2 className="size-4 animate-spin text-accent" /> {step || "Thinking"}…
                </div>
              </>
            )}
          </>
        )}
        <div ref={end} />
      </div>
      <div className="sticky bottom-0 -mx-1 bg-bg/90 px-1 pb-1 backdrop-blur">
        <div className="rounded-lg border border-line bg-surface-1 p-2 focus-within:border-accent">
          <textarea
            value={text} rows={2} placeholder="Ask about trends, hooks, your clips…" aria-label="Your question"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }}
            className="w-full resize-none bg-transparent px-2 py-1 text-sm outline-none placeholder:text-subtle"
          />
          <div className="flex items-center justify-between gap-2 px-1">
            <select value={about ?? ""} onChange={(e) => setAbout(e.target.value ? Number(e.target.value) : undefined)}
                    aria-label="Niche" className="h-7 max-w-56 rounded-sm border border-line bg-surface-2 px-2 text-xs text-muted">
              <option value="">Any niche</option>
              {niches.map((n) => <option key={n.id} value={n.id}>About: {n.name}</option>)}
            </select>
            <Button size="icon" variant="primary" disabled={!text.trim() || pending !== null} onClick={() => void send()} aria-label="Send">
              {pending ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
            </Button>
          </div>
        </div>
        <p className="mt-1 text-center text-[11px] text-subtle">AI answers can be wrong; check the sources before you act on them.</p>
      </div>
    </div>
  );
}

function AskTab({ canAct }: { canAct: boolean }) {
  const search = useSearch({ from: "/research" });
  const navigate = useNavigate({ from: "/research" });
  const qc = useQueryClient();
  const { data: threads = [] } = useThreads();
  const open = (thread?: number) => void navigate({ search: (s) => ({ ...s, thread }) });
  return (
    <div className="flex gap-6">
      <aside className="hidden w-56 shrink-0 flex-col gap-1 lg:flex">
        <Button variant="secondary" className="mb-2 justify-start" onClick={() => open(undefined)}>
          <MessageSquarePlus className="size-4" /> New chat
        </Button>
        {threads.map((t) => (
          <div key={t.id} className={cn("group flex items-center rounded-md", search.thread === t.id ? "bg-surface-2" : "hover:bg-surface-2")}>
            <button onClick={() => open(t.id)} className="min-w-0 flex-1 truncate px-2.5 py-2 text-left text-sm" title={t.title}>{t.title}</button>
            <button aria-label="Delete chat" className="mr-1 hidden size-6 place-items-center rounded-sm text-subtle hover:text-danger group-hover:grid"
                    onClick={() => void deleteThread(t.id).then(() => {
                      void qc.invalidateQueries({ queryKey: ["research", "threads"] });
                      if (search.thread === t.id) open(undefined);
                    })}>
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </aside>
      <Chat key={search.thread ?? "new"} threadId={search.thread} nicheId={search.niche} canAct={canAct} onThread={(id) => open(id)} />
    </div>
  );
}

/* ---------- Niches ---------- */

function NicheForm({ niche, onDone }: { niche?: Niche; onDone: (n?: Niche) => void }) {
  const [name, setName] = useState(niche?.name ?? "");
  const [description, setDescription] = useState(niche?.description ?? "");
  const [keywords, setKeywords] = useState((niche?.keywords ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const saved = await saveNiche({ name, description, keywords: keywords.split(",").map((k) => k.trim()).filter(Boolean) }, niche?.id);
      onDone(saved as Niche);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="flex flex-col gap-3 p-5">
      <h2 className="text-md font-semibold">{niche ? "Edit niche" : "Track a niche"}</h2>
      <Field label="Name">{(id) => <TextInput id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="TV romance edits" autoFocus />}</Field>
      <Field label="What it is" optional>{(id) => <TextArea id={id} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Romance and chemistry moments from TV shows, for streaming campaigns" />}</Field>
      <Field label="Search words" optional hint="Comma separated. Used to find top Shorts and trends, e.g. show names, creators.">
        {(id) => <TextInput id={id} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="chad powers, tv edits, romance" />}
      </Field>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => onDone()}>Cancel</Button>
        <Button variant="primary" disabled={!name.trim() || busy} onClick={() => void submit()}>{niche ? "Save" : "Track niche"}</Button>
      </div>
    </Card>
  );
}

function SaveButton({ kind, text, url, nicheId }: { kind: "hook" | "idea" | "link"; text: string; url?: string; nicheId?: number }) {
  const qc = useQueryClient();
  const [done, setDone] = useState(false);
  return (
    <Tip label={done ? "Saved" : "Save"}>
      <button aria-label="Save" disabled={done}
              onClick={() => void saveItem({ kind, text, url, niche_id: nicheId }).then(() => { setDone(true); void qc.invalidateQueries({ queryKey: ["research", "saved"] }); })}
              className="grid size-7 shrink-0 place-items-center rounded-sm text-muted hover:bg-surface-3 hover:text-fg">
        {done ? <BookmarkCheck className="size-3.5 text-success" /> : <Bookmark className="size-3.5" />}
      </button>
    </Tip>
  );
}

function BriefView({ niche }: { niche: Niche }) {
  const brief = niche.brief as NicheBrief | null | undefined;
  if (!brief) return <EmptyState icon={<Radar className="size-5" />} title="No brief yet" body="Press Refresh to build one. After that it updates by itself once a day while Clipper is open." />;
  return (
    <div className="flex flex-col gap-4">
      <Card className="p-4">
        <p className="text-sm">{brief.summary}</p>
        {!brief.live && <p className="mt-2 text-xs text-warning">From the AI's general knowledge, not live data. Add the free keys above for this week's real trends.</p>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="flex flex-col gap-3 p-4">
          <h3 className="text-sm font-semibold">Trending now</h3>
          {brief.topics.length ? brief.topics.map((t) => (
            <div key={t.title} className="flex gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{t.title}</div>
                <div className="text-xs text-muted">{t.why}{" "}
                  {t.sources.map((n) => brief.sources[n - 1] && (
                    <a key={n} href={brief.sources[n - 1].url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">[{n}]</a>
                  ))}
                </div>
              </div>
              <SaveButton kind="idea" text={`${t.title}: ${t.why}`} nicheId={niche.id} />
            </div>
          )) : <p className="text-sm text-muted">Nothing found.</p>}
        </Card>
        <Card className="flex flex-col gap-1 p-4">
          <h3 className="mb-2 text-sm font-semibold">Hook ideas</h3>
          {brief.hooks.map((h) => (
            <div key={h} className="group flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-surface-2">
              <span className="flex-1 text-sm">“{h}”</span>
              <CopyButton text={h} what="Hook" variant="ghost" />
              <SaveButton kind="hook" text={h} nicheId={niche.id} />
            </div>
          ))}
        </Card>
      </div>
      {brief.shorts.length > 0 && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Top Shorts this week</h3>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
            {brief.shorts.map((s) => (
              <a key={s.id} href={s.url} target="_blank" rel="noopener noreferrer" className="group flex flex-col gap-1.5">
                <div className="relative aspect-[9/16] overflow-hidden rounded-md bg-black">
                  {s.thumb && <img src={s.thumb} alt="" loading="lazy" className="absolute inset-0 size-full object-cover transition-transform group-hover:scale-[1.03]" />}
                  <span className="tabular absolute right-1.5 bottom-1.5 rounded-[4px] bg-black/70 px-1.5 py-px text-[11px] font-medium text-white">{formatCount(s.views)} views</span>
                </div>
                <div className="line-clamp-2 text-xs font-medium">{s.title}</div>
                <div className="truncate text-[11px] text-muted">{s.channel}</div>
              </a>
            ))}
          </div>
        </section>
      )}
      {brief.notes.length > 0 && <p className="text-xs text-subtle">{brief.notes.join(" · ")}</p>}
    </div>
  );
}

function NichesTab() {
  const search = useSearch({ from: "/research" });
  const navigate = useNavigate({ from: "/research" });
  const qc = useQueryClient();
  const progress = useProgress();
  const { data: niches = [], isLoading } = useNiches();
  const [editing, setEditing] = useState<"new" | number | null>(null);
  const [refreshing, setRefreshing] = useState<number | null>(null);
  const selected = niches.find((n) => n.id === search.niche) ?? niches[0];
  const select = (id?: number) => void navigate({ search: (s) => ({ ...s, niche: id }) });
  const done = (n?: Niche) => {
    setEditing(null);
    void qc.invalidateQueries({ queryKey: ["research", "niches"] });
    if (n) { select(n.id); if (!n.brief) void refresh(n.id); }
  };
  const refresh = async (id: number) => {
    setRefreshing(id);
    try {
      const n = await refreshNiche(id);
      qc.setQueryData<Niche[]>(["research", "niches"], (old) => old?.map((x) => (x.id === id ? n as Niche : x)));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRefreshing(null);
    }
  };

  if (isLoading) return <Skeleton className="h-64" />;
  if (editing === "new" || (!niches.length && editing === null)) {
    return niches.length || editing === "new" ? <div className="max-w-xl"><NicheForm onDone={done} /></div> : (
      <EmptyState icon={<Radar className="size-5" />} title="Track the niches you clip in"
        body="Add a niche and Clipper briefs you on it every day: the week's top Shorts, what's trending, and hook ideas in its voice."
        action={<Button variant="primary" onClick={() => setEditing("new")}><Plus className="size-4" /> Track a niche</Button>} />
    );
  }
  const step = progress?.niche_id === selected?.id ? progress?.step : "";
  return (
    <div className="flex flex-col gap-6 lg:flex-row">
      <aside className="flex shrink-0 flex-row flex-wrap gap-1 lg:w-56 lg:flex-col">
        {niches.map((n) => (
          <button key={n.id} onClick={() => select(n.id)}
                  className={cn("rounded-md px-2.5 py-2 text-left text-sm", selected?.id === n.id ? "bg-surface-2 font-medium" : "text-muted hover:bg-surface-2 hover:text-fg")}>
            {n.name}
            <div className="text-[11px] font-normal text-subtle">{n.brief_at ? `updated ${ago(n.brief_at)}` : "no brief yet"}</div>
          </button>
        ))}
        <Button variant="ghost" className="justify-start" onClick={() => setEditing("new")}><Plus className="size-4" /> Track a niche</Button>
      </aside>
      {selected && (editing === selected.id ? (
        <div className="max-w-xl flex-1"><NicheForm niche={selected} onDone={done} /></div>
      ) : (
        <div className="min-w-0 flex-1">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold">{selected.name}</h2>
              {selected.description && <p className="text-sm text-muted">{selected.description}</p>}
              {selected.keywords.length > 0 && <p className="mt-1 text-xs text-subtle">Searching: {selected.keywords.join(", ")}</p>}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" onClick={() => void navigate({ search: { tab: "ask", niche: selected.id } })}>
                <Sparkles className="size-4" /> Ask about it
              </Button>
              <Button variant="secondary" disabled={refreshing !== null} onClick={() => void refresh(selected.id)}>
                <RefreshCw className={cn("size-4", refreshing === selected.id && "animate-spin")} />
                {refreshing === selected.id ? step || "Refreshing…" : "Refresh"}
              </Button>
              <Button size="icon" variant="ghost" aria-label="Edit niche" onClick={() => setEditing(selected.id)}><Pencil className="size-4" /></Button>
              <Button size="icon" variant="ghost" aria-label="Stop tracking" className="hover:text-danger"
                      onClick={() => void deleteNiche(selected.id).then(() => { select(undefined); void qc.invalidateQueries({ queryKey: ["research", "niches"] }); })}>
                <Trash2 className="size-4" />
              </Button>
            </div>
          </div>
          {refreshing === selected.id && !selected.brief ? <Skeleton className="h-64" /> : <BriefView niche={selected} />}
        </div>
      ))}
    </div>
  );
}

/* ---------- Saved ---------- */

const KINDS: [string, string][] = [["all", "All"], ["hook", "Hooks"], ["idea", "Ideas"], ["answer", "Answers"], ["link", "Links"]];

function SavedTab() {
  const qc = useQueryClient();
  const { data: items = [], isLoading } = useSaved();
  const { data: niches = [] } = useNiches();
  const [kind, setKind] = useState("all");
  const names = new Map(niches.map((n) => [n.id, n.name]));
  const list = items.filter((i) => kind === "all" || i.kind === kind);
  if (isLoading) return <Skeleton className="h-40" />;
  if (!items.length) {
    return <EmptyState icon={<Bookmark className="size-5" />} title="Nothing saved yet"
      body="Save answers from Ask, and hooks or ideas from a niche's brief. They collect here to use later." />;
  }
  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <div className="flex flex-wrap gap-1.5">
        {KINDS.map(([key, label]) => (
          <button key={key} onClick={() => setKind(key)}
            className={cn("h-8 rounded-full border px-3 text-sm font-medium", kind === key ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:text-fg")}>
            {label} <span className="tabular ml-1 opacity-70">{key === "all" ? items.length : items.filter((i) => i.kind === key).length}</span>
          </button>
        ))}
      </div>
      {list.map((item) => (
        <Card key={item.id} className="flex gap-3 p-3">
          <div className="min-w-0 flex-1">
            <div className="mb-1 text-[11px] font-medium tracking-wide text-subtle uppercase">
              {item.kind}{item.niche_id && names.get(item.niche_id) ? ` · ${names.get(item.niche_id)}` : ""} · {ago(item.created_at)}
            </div>
            {item.kind === "answer" ? (
              <div className="prose-answer line-clamp-6 text-sm"><Markdown remarkPlugins={[remarkGfm]}>{item.text}</Markdown></div>
            ) : <p className="text-sm whitespace-pre-wrap">{item.kind === "hook" ? `“${item.text}”` : item.text}</p>}
          </div>
          <div className="flex shrink-0 items-start gap-0.5">
            <CopyButton text={item.text} what="Text" variant="ghost" />
            {item.url && <a href={item.url} target="_blank" rel="noopener noreferrer" aria-label="Open link" className="grid size-7 place-items-center rounded-sm text-muted hover:bg-surface-3 hover:text-fg"><ExternalLink className="size-3.5" /></a>}
            <Button size="icon" variant="ghost" className="size-7 hover:text-danger" aria-label="Remove"
                    onClick={() => void unsaveItem(item.id).then(() => qc.invalidateQueries({ queryKey: ["research", "saved"] }))}>
              <X className="size-3.5" />
            </Button>
          </div>
        </Card>
      ))}
    </div>
  );
}

/* ---------- page ---------- */

export function ResearchPage() {
  const search = useSearch({ from: "/research" });
  const navigate = useNavigate({ from: "/research" });
  const { data: status, isLoading } = useResearchStatus();
  const tab: Tab = search.tab ?? "ask";
  const tabs: [Tab, string, ReactNode][] = [
    ["ask", "Ask", <Sparkles key="a" className="size-4" />],
    ["niches", "Niches", <Radar key="n" className="size-4" />],
    ["saved", "Saved", <Bookmark key="s" className="size-4" />],
  ];
  if (isLoading || !status) return <Skeleton className="h-64" />;
  if (!status.can_research) {
    return (
      <div className="fade-in">
        <PageHeader title="Research" />
        <EmptyState icon={<Lock className="size-5" />} title="Research is part of the Research plan"
          body="Chat about trends and your results, daily briefs on the niches you clip in, and a place to keep hooks and ideas."
          action={<Button variant="primary" onClick={() => void navigate({ to: "/settings" })}>See plans</Button>} />
      </div>
    );
  }
  return (
    <div className="fade-in">
      <PageHeader title="Research" subtitle="Ask questions, follow your niches, and keep the hooks and ideas worth using." />
      {!status.ai ? (
        <EmptyState icon={<KeyRound className="size-5" />} title="Research needs your AI key"
          body="It's free and takes a minute." action={<Button variant="primary" onClick={() => void navigate({ to: "/settings" })}>Add it in Settings</Button>} />
      ) : (
        <>
          <KeySetup web={status.web} youtube={status.youtube} />
          <div className="mb-5 flex gap-1 border-b border-line" role="tablist">
            {tabs.map(([key, label, icon]) => (
              <button key={key} role="tab" aria-selected={tab === key}
                      onClick={() => void navigate({ search: (s) => ({ ...s, tab: key }) })}
                      className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium",
                        tab === key ? "border-accent text-fg" : "border-transparent text-muted hover:text-fg")}>
                {icon}{label}
              </button>
            ))}
          </div>
          {tab === "ask" && <AskTab canAct={status.can_act} />}
          {tab === "niches" && <NichesTab />}
          {tab === "saved" && <SavedTab />}
        </>
      )}
    </div>
  );
}
