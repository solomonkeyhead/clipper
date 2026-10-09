import * as Dialog from "@radix-ui/react-dialog";
import { useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Copy, KeyRound, Loader2, Lock, MessageSquarePlus, Sparkles, Trash2, X } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import {
  askResearch, deleteThread, useResearchStatus, useSetKeys, useThread, useThreads,
  type ResearchMessage, type ThreadDetail,
} from "@/api/client";
import { useUI } from "@/lib/store";
import { copyText } from "@/lib/utils";
import { Waited } from "./progress";
import { Button, Tip } from "./ui";

const SUGGESTIONS = [
  "Which of my clips kept viewers watching longest, and why?",
  "Which of my campaigns earns the most per clip?",
  "What's working in TV show clips on TikTok right now?",
  "Write 8 hook lines for my best-paying campaign",
];

/** "[2]" in an answer becomes a link to source 2. */
function linkCitations(text: string, sources: { url: string }[]) {
  return text.replace(/\[(\d{1,2})\](?!\()/g, (m, n) => {
    const s = sources[Number(n) - 1];
    return s ? `[[${n}]](${s.url})` : m;
  });
}

function host(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
}

const Markdown = lazy(() => import("./Markdown"));

function Answer({ message }: { message: ResearchMessage }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="prose-answer text-sm leading-relaxed">
        <Suspense fallback={<p className="whitespace-pre-wrap">{message.content}</p>}>
          <Markdown>{linkCitations(message.content, message.sources)}</Markdown>
        </Suspense>
      </div>
      {message.sources.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {message.sources.map((s, i) => (
            <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer" title={s.title}
               className="flex max-w-52 items-center gap-1.5 rounded-full border border-line px-2.5 py-0.5 text-xs text-muted hover:border-line-strong hover:text-fg">
              <b>{i + 1}</b><span className="truncate">{host(s.url)}</span>
            </a>
          ))}
        </div>
      )}
      <Button size="sm" variant="ghost" className="w-fit" onClick={() => void copyText(message.content, "Answer")}>
        <Copy className="size-3.5" /> Copy
      </Button>
    </div>
  );
}

function WebSetup() {
  const save = useSetKeys();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  return (
    <div className="rounded-md border border-dashed border-line p-3 text-xs text-muted">
      {!open ? (
        <button className="text-left hover:text-fg" onClick={() => setOpen(true)}>
          Answers use your clips, stats and the AI's knowledge. <span className="font-medium text-accent">Set up web search</span> for
          what's happening right now, with sources (free).
        </button>
      ) : (
        <div className="flex flex-col gap-2">
          <p>Sign up at <a className="text-accent hover:underline" href="https://app.tavily.com" target="_blank" rel="noopener noreferrer">app.tavily.com</a> (free,
            no card, 1,000 searches a month) and paste your API key:</p>
          <div className="flex gap-2">
            <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="tvly-…"
                   aria-label="Tavily key" className="h-8 flex-1 rounded-sm border border-line bg-surface-1 px-2 text-sm text-fg focus:border-accent focus:outline-none" />
            <Button size="sm" variant="primary" disabled={key.trim().length < 10}
                    onClick={() => save.mutate({ TAVILY_API_KEY: key }, {
                      onSuccess: () => { setKey(""); setOpen(false); void qc.invalidateQueries({ queryKey: ["research"] }); toast.success("Web search is on"); },
                      onError: (e) => toast.error((e as Error).message),
                    })}>Save</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function Conversation({ threadId, onThread }: { threadId?: number; onThread: (id: number) => void }) {
  const qc = useQueryClient();
  const { data: thread } = useThread(threadId);
  const { data: status } = useResearchStatus();
  const progress = useQuery<{ thread_id?: number; step: string } | null>({
    queryKey: ["research", "progress"], queryFn: () => null, staleTime: Infinity, gcTime: Infinity,
  }).data;
  const [text, setText] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [thread?.messages.length, pending]);

  const send = async (question = text) => {
    const q = question.trim();
    if (!q || pending) return;
    setPending(q);
    setText("");
    try {
      const result = await askResearch(q, threadId) as ThreadDetail;
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
  return (
    <>
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {messages.length === 0 && !pending ? (
          <div className="flex flex-col gap-4 py-6">
            <p className="text-sm text-muted">Quick questions about your clips, campaigns and what's working. It only answers; it never changes anything.</p>
            <div className="flex flex-col gap-2">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => void send(s)}
                        className="rounded-md border border-line p-2.5 text-left text-sm text-muted transition-colors hover:border-line-strong hover:bg-surface-2 hover:text-fg">{s}</button>
              ))}
            </div>
            {status && !status.web && <WebSetup />}
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            {messages.map((m) => m.role === "user"
              ? <div key={m.id} className="ml-auto max-w-[85%] rounded-lg bg-surface-2 px-3.5 py-2 text-sm whitespace-pre-wrap">{m.content}</div>
              : <Answer key={m.id} message={m} />)}
            {pending && (
              <>
                <div className="ml-auto max-w-[85%] rounded-lg bg-surface-2 px-3.5 py-2 text-sm whitespace-pre-wrap">{pending}</div>
                <div className="flex items-center gap-2 text-sm text-muted">
                  <Loader2 className="size-4 animate-spin text-accent" />
                  {(progress && (!threadId || progress.thread_id === threadId) && progress.step) || "Thinking"}…
                  <Waited after={5} />
                </div>
              </>
            )}
            <div ref={end} />
          </div>
        )}
      </div>
      <div className="border-t border-line p-3">
        <div className="flex items-end gap-2 rounded-lg border border-line bg-surface-1 p-1.5 focus-within:border-accent">
          <textarea value={text} rows={2} placeholder="Ask about your clips, campaigns, trends…" aria-label="Your question"
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }}
                    className="flex-1 resize-none bg-transparent px-2 py-1 text-sm outline-none placeholder:text-subtle" />
          <Button size="icon" variant="primary" disabled={!text.trim() || pending !== null} onClick={() => void send()} aria-label="Send">
            {pending ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
          </Button>
        </div>
        <p className="mt-1 text-center text-[11px] text-subtle">AI answers can be wrong; check sources before acting on them.</p>
      </div>
    </>
  );
}

export function AskPanel() {
  const open = useUI((s) => s.askOpen);
  const setOpen = useUI((s) => s.setAsk);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data: status } = useResearchStatus();
  const { data: threads = [] } = useThreads();
  const [threadId, setThreadId] = useState<number | undefined>(undefined);

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="fade-in fixed inset-0 z-40 bg-black/40" />
        <Dialog.Content aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[560px] flex-col border-l border-line bg-bg shadow-3 outline-none data-[state=open]:animate-[sheet-in_var(--dur-panel)_var(--ease-decelerate)]">
          <div className="flex items-center gap-2 border-b border-line px-5 py-3">
            <Sparkles className="size-4 text-accent" />
            <Dialog.Title className="flex-1 text-md font-semibold">Ask Clipper</Dialog.Title>
            {status?.can_research && status.ai && (
              <>
                {threads.length > 0 && (
                  <select value={threadId ?? ""} aria-label="Earlier chats"
                          onChange={(e) => setThreadId(e.target.value ? Number(e.target.value) : undefined)}
                          className="h-8 max-w-48 rounded-sm border border-line bg-surface-2 px-2 text-xs">
                    <option value="">Earlier chats…</option>
                    {threads.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
                  </select>
                )}
                {threadId !== undefined && (
                  <Tip label="Delete this chat">
                    <Button size="icon" variant="ghost" aria-label="Delete this chat" className="hover:text-danger"
                            onClick={() => void deleteThread(threadId).then(() => {
                              setThreadId(undefined);
                              void qc.invalidateQueries({ queryKey: ["research", "threads"] });
                            })}>
                      <Trash2 className="size-4" />
                    </Button>
                  </Tip>
                )}
                <Tip label="New chat">
                  <Button size="icon" variant="ghost" aria-label="New chat" onClick={() => setThreadId(undefined)}>
                    <MessageSquarePlus className="size-4" />
                  </Button>
                </Tip>
              </>
            )}
            <Dialog.Close asChild>
              <Button size="icon" variant="ghost" aria-label="Close"><X className="size-4" /></Button>
            </Dialog.Close>
          </div>
          {!status ? null : !status.can_research ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
              <Lock className="size-5 text-muted" />
              <p className="text-sm text-muted">Ask is part of the Research plan.</p>
              <Button variant="primary" onClick={() => { setOpen(false); void navigate({ to: "/settings" }); }}>See plans</Button>
            </div>
          ) : !status.ai ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8 text-center">
              <KeyRound className="size-5 text-muted" />
              <p className="text-sm text-muted">Ask needs your free AI key.</p>
              <Button variant="primary" onClick={() => { setOpen(false); void navigate({ to: "/settings" }); }}>Add it in Settings</Button>
            </div>
          ) : (
            <Conversation key={threadId ?? "new"} threadId={threadId} onThread={setThreadId} />
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
