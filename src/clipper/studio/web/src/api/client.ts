import createClient from "openapi-fetch";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components, paths } from "./schema";
import { useUI } from "@/lib/store";

const api = createClient<paths>({ baseUrl: "" });

export type Clip = components["schemas"]["Clip"];
export type Post = components["schemas"]["Post"];
export type Campaign = components["schemas"]["Campaign"];
export type CampaignDetail = components["schemas"]["CampaignDetail"];
export type Brief = components["schemas"]["Brief"];
export type Home = components["schemas"]["Home"];
export type Status = components["schemas"]["Status"];
export type Account = components["schemas"]["Account"];
export type ClipStatus = "ready" | "posted" | "submitted" | "skipped";

async function unwrap<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await p;
  if (error !== undefined || data === undefined) {
    const detail = (error as { detail?: string } | undefined)?.detail;
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return data;
}

export const keys = {
  status: ["status"] as const,
  home: ["home"] as const,
  campaigns: ["campaigns"] as const,
  campaign: (name: string) => ["campaign", name] as const,
  clips: ["clips"] as const,
  posts: ["posts"] as const,
  accounts: ["accounts"] as const,
  settings: ["settings"] as const,
  setup: ["setup"] as const,
};

export const useStatus = () =>
  useQuery({ queryKey: keys.status, queryFn: () => unwrap(api.GET("/api/status")),
             refetchInterval: 60_000 });
/** The clip list on screen: its cache key carries the viewing scope. */
const clipsKey = () => [...keys.clips, useUI.getState().accountScope];

/** The "viewing" switcher's scope, sent with the lists it narrows (D89). */
const scopeParam = () => {
  const scope = useUI.getState().accountScope;
  return scope && scope !== "all" ? scope : undefined;
};
export const useHome = () => {
  const scope = useUI((s) => s.accountScope);
  return useQuery({ queryKey: [...keys.home, scope],
                    queryFn: () => unwrap(api.GET("/api/home", { params: { query: { scope: scopeParam() } } })) });
};
export const useCampaigns = () =>
  useQuery({ queryKey: keys.campaigns, queryFn: () => unwrap(api.GET("/api/campaigns")) });
export const useCampaign = (name: string) =>
  useQuery({ queryKey: keys.campaign(name),
             queryFn: () => unwrap(api.GET("/api/campaigns/{name}", { params: { path: { name } } })) });
export const useClips = () => {
  const scope = useUI((s) => s.accountScope);
  return useQuery({ queryKey: [...keys.clips, scope],
                    queryFn: () => unwrap(api.GET("/api/clips", { params: { query: { scope: scopeParam() } } })) });
};
export const usePosts = () => {
  const scope = useUI((s) => s.accountScope);
  return useQuery({ queryKey: [...keys.posts, scope],
                    queryFn: () => unwrap(api.GET("/api/posts", { params: { query: { scope: scopeParam() } } })) });
};
export const useAccounts = () =>
  useQuery({ queryKey: keys.accounts, queryFn: () => unwrap(api.GET("/api/accounts")) });
export const useSettings = () =>
  useQuery({ queryKey: keys.settings, queryFn: () => unwrap(api.GET("/api/settings")) });

/** The reasons a rating can give, grouped (one list, on the server). */
export const useReasons = () =>
  useQuery({ queryKey: ["reasons"], queryFn: () => unwrap(api.GET("/api/reasons")), staleTime: Infinity });

/** One post's views at each sync that changed them (studio/db.py snapshots). */
export const usePostHistory = (url: string) =>
  useQuery({ queryKey: ["post-history", url],
             queryFn: () => unwrap(api.GET("/api/posts/history", { params: { query: { url } } })) });

/** Everything that shows clips or posts. */
const CLIP_KEYS = [keys.home, keys.campaigns, keys.clips, keys.posts, ["campaign"], ["post-history"]];

function useInvalidate() {
  const qc = useQueryClient();
  return () => CLIP_KEYS.forEach((queryKey) => qc.invalidateQueries({ queryKey }));
}

/** Optimistic: every cached clip list shows the new status at once, rolled back on error. */
export function useSetClipStatus() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: ClipStatus }) =>
      unwrap(api.PATCH("/api/clips/{clip_id}", { params: { path: { clip_id: id } }, body: { status } })),
    onMutate: async ({ id, status }) => {
      await qc.cancelQueries({ queryKey: keys.clips });
      const previous = qc.getQueryData<Clip[]>(clipsKey());
      qc.setQueryData<Clip[]>(clipsKey(), (old) =>
        old?.map((c) => (c.id === id ? { ...c, status, marked: status } : c)));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(clipsKey(), context.previous),
    onSettled: invalidate,
  });
}

export function useSetNote() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, notes }: { id: number; notes: string }) =>
      unwrap(api.PATCH("/api/clips/{clip_id}", { params: { path: { clip_id: id } }, body: { notes } })),
    onSettled: invalidate,
  });
}

export function useSetCampaign() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, ...changes }: {
      name: string; archived?: boolean; auto_post?: boolean | null; budget_left?: number | null;
    }) => unwrap(api.PATCH("/api/campaigns/{name}", { params: { path: { name } }, body: changes })),
    onSettled: invalidate,
  });
}

export type Payout = components["schemas"]["Payout"];

/** What campaigns actually paid, as recorded (D99). */
export const usePayouts = (campaign?: string) =>
  useQuery({ queryKey: ["payouts", campaign ?? "all"],
             queryFn: () => unwrap(api.GET("/api/payouts", { params: { query: { campaign } } })) });

export function useAddPayout() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: { campaign: string; amount: number; paid_on: string; note?: string }) =>
      unwrap(api.POST("/api/payouts", { body })),
    onSettled: () => { invalidate(); void qc.invalidateQueries({ queryKey: ["payouts"] }); },
  });
}

export function useDeletePayout() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE("/api/payouts/{payout_id}", { params: { path: { payout_id: id } } })),
    onSettled: () => { invalidate(); void qc.invalidateQueries({ queryKey: ["payouts"] }); },
  });
}

/** A brief's view-milestone task done (or undone) for a post (D98). */
export function useSetTask() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: { url: string; views: number; done: boolean }) =>
      unwrap(api.POST("/api/posts/task", { body })),
    onSettled: invalidate,
  });
}

export function useSetSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (changes: Record<string, string>) => unwrap(api.PUT("/api/settings", { body: changes })),
    onSuccess: (data) => {
      qc.setQueryData(keys.settings, data);
      qc.invalidateQueries({ queryKey: keys.status });
    },
  });
}

export function useSyncNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/sync")),
    onSettled: () => {
      CLIP_KEYS.forEach((queryKey) => qc.invalidateQueries({ queryKey }));
      qc.invalidateQueries({ queryKey: keys.status });
    },
  });
}

export async function revealClip(id: number) {
  return unwrap(api.POST("/api/clips/{clip_id}/reveal", { params: { path: { clip_id: id } } }));
}

export const downloadUrl = (id: number) => `/media/${id}?download=true`;

/* ---------- delete (to the 30-day trash) with undo ---------- */

export function useDeleteClip() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, restore }: { id: number; restore?: boolean }) => restore
      ? unwrap(api.POST("/api/clips/{clip_id}/restore", { params: { path: { clip_id: id } } }))
      : unwrap(api.DELETE("/api/clips/{clip_id}", { params: { path: { clip_id: id } } })),
    onMutate: async ({ id, restore }) => {
      if (restore) return {};
      await qc.cancelQueries({ queryKey: keys.clips });
      const previous = qc.getQueryData<Clip[]>(clipsKey());
      qc.setQueryData<Clip[]>(clipsKey(), (old) => old?.filter((c) => c.id !== id));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(clipsKey(), context.previous),
    onSettled: invalidate,
  });
}

/* ---------- new clips: footage, uploads, jobs ---------- */

export interface Source {
  name: string; path: string; size_mb: number; modified: string; folder: string;
  /** The campaign it belongs to, and how that's known: clipped | added | name | like (studio/footage.py). */
  campaign: string | null; sorted_by: string;
}
export type JobMode = "auto" | "top" | "manual";
export interface RunReport {
  mode: "auto" | "manual"; moments: number; cleared: number; bar: number | null; limit: number; made: number;
  reasons: { reason: string; count: number }[];
  near_misses: { start: number; end: number; score: number | null; why: string; text: string }[];
}
export interface Job {
  id: number; campaign: string; source: string; name: string; top: number | null;
  mode: JobMode | "prepare"; ranges: [number, number][];
  /** Clips made in the editor (D103); a "prepare" job reads a video for it. */
  edits?: unknown[];
  status: "queued" | "running" | "done" | "failed"; stage: string; pct: number;
  clips: number; message: string; created: string; finished: string;
  report?: RunReport | { source_id?: string } | Record<string, never>;
}

export const useSources = () =>
  useQuery({ queryKey: ["sources"], queryFn: async () => (await fetch("/api/sources")).json() as Promise<Source[]> });
export const useJobs = () =>
  useQuery({ queryKey: ["jobs"], queryFn: async () => (await fetch("/api/jobs")).json() as Promise<Job[]> });

/** One job per video, queued in turn. */
export async function startJob(campaign: string, sources: string | string[], mode: JobMode,
                               options: { top?: number; ranges?: [string, string][] } = {}): Promise<Job[]> {
  const res = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ campaign, sources: [sources].flat(), mode, ...options }) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data as Job[];
}

/** Upload with progress (fetch can't report upload progress; XHR can). */
export function uploadVideo(file: File, onProgress: (fraction: number) => void, campaign = ""): Promise<Source> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads/${encodeURIComponent(file.name)}${campaign ? `?campaign=${encodeURIComponent(campaign)}` : ""}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText);
        if (xhr.status >= 200 && xhr.status < 300) resolve(data as Source);
        else reject(new Error(data.detail || xhr.statusText));
      } catch {
        reject(new Error(xhr.statusText || "Upload failed"));
      }
    };
    xhr.onerror = () => reject(new Error("Upload failed: lost connection to Clipper"));
    xhr.send(file);
  });
}

/* ---------- campaigns: create, edit, read a brief ---------- */

export type CampaignForm = Required<components["schemas"]["CampaignForm"]>;

export const useCampaignForm = (name: string | undefined) =>
  useQuery({
    queryKey: ["campaign-form", name],
    enabled: Boolean(name),
    queryFn: async () => (await unwrap(api.GET("/api/campaigns/{name}/form",
      { params: { path: { name: name! } } }))) as CampaignForm,
  });

export function useSaveCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ form, name }: { form: CampaignForm; name?: string }) => name
      ? unwrap(api.PUT("/api/campaigns/{name}", { params: { path: { name } }, body: form }))
      : unwrap(api.POST("/api/campaigns", { body: form })),
    onSuccess: () => {
      CLIP_KEYS.forEach((queryKey) => qc.invalidateQueries({ queryKey }));
      qc.invalidateQueries({ queryKey: ["campaign-form"] });
    },
  });
}

export function useDeleteCampaign() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.DELETE("/api/campaigns/{name}", { params: { path: { name } } })),
    onSettled: invalidate,
  });
}

export const readBrief = async (text: string) =>
  (await unwrap(api.POST("/api/campaigns/read-brief", { body: { text } }))) as CampaignForm;

/** A campaign's display name from its id ("chad-powers-s2" -> "Chad Powers S2"). */
export function useCampaignTitle() {
  const { data: campaigns = [] } = useCampaigns();
  const titles = new Map(campaigns.map((c) => [c.name, c.title]));
  return (name: string) => titles.get(name) ?? name;
}

/* ---------- submitted, per clip ---------- */

export function useSetClipSubmitted() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, submitted }: { id: number; submitted: boolean }) =>
      unwrap(api.PUT("/api/clips/{clip_id}/submitted", { params: { path: { clip_id: id } }, body: { submitted } })),
    onMutate: async ({ id, submitted }) => {
      await qc.cancelQueries({ queryKey: keys.clips });
      const previous = qc.getQueryData<Clip[]>(clipsKey());
      qc.setQueryData<Clip[]>(clipsKey(), (old) => old?.map((c) => c.id === id
        ? { ...c, status: submitted ? "submitted" : c.posts.length ? "posted" : "ready" } : c));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(clipsKey(), context.previous),
    onSettled: invalidate,
  });
}

/* ---------- setup: keys, account connections, system check ---------- */

export type Setup = components["schemas"]["Setup"];
export interface CheckResult { name: string; status: "ok" | "warn" | "fail"; detail: string; fix: string }

export const useSetup = () => useQuery({ queryKey: keys.setup, queryFn: () => unwrap(api.GET("/api/setup")) });

export function useSetKeys() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, string>) => unwrap(api.PUT("/api/setup/keys", { body: values })),
    onSuccess: (data) => {
      qc.setQueryData(keys.setup, data);
      qc.invalidateQueries({ queryKey: keys.home });
    },
  });
}

export const testAI = () => unwrap(api.POST("/api/setup/test-ai")) as Promise<{ ok: boolean; detail: string }>;
export const runSystemCheck = async () => (await unwrap(api.POST("/api/setup/check"))) as unknown as CheckResult[];
export const startTikTokConnect = () =>
  unwrap(api.POST("/api/accounts/tiktok/connect")) as Promise<{ state: string; message: string; url: string }>;
export const tiktokConnectState = () =>
  unwrap(api.GET("/api/accounts/tiktok/connect")) as Promise<{ state: string; message: string; url: string }>;
export const startYouTubeConnect = () =>
  unwrap(api.POST("/api/accounts/youtube/connect")) as Promise<{ state: string; message: string; url: string }>;
export const youtubeConnectState = () =>
  unwrap(api.GET("/api/accounts/youtube/connect")) as Promise<{ state: string; message: string; url: string }>;
/** An X account by username, read with the app's Bearer Token (D83). */
export const connectX = (username: string) =>
  unwrap(api.POST("/api/accounts/x", { body: { username } })) as Promise<{ username: string }>;
export const connectInstagram = (token: string) =>
  unwrap(api.POST("/api/accounts/instagram", { body: { token } })) as Promise<{ username: string }>;

export function useDisconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ platform, id }: { platform: string; id: string }) =>
      unwrap(api.DELETE("/api/accounts/{platform}/{account}", { params: { path: { platform, account: id } } })),
    onSettled: () => [keys.accounts, keys.status, keys.home].forEach((queryKey) => qc.invalidateQueries({ queryKey })),
  });
}

export const sourceVideoUrl = (path: string) => `/api/sources/video?path=${encodeURIComponent(path)}`;

/* ---------- ratings and learning ---------- */

export type Learning = components["schemas"]["Learning"];
export const useLearning = () =>
  useQuery({ queryKey: ["learning"], queryFn: () => unwrap(api.GET("/api/learning")) });

export function useRateClip() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, rating, reasons }: { id: number; rating: number | null; reasons: string[] }) =>
      unwrap(api.PUT("/api/clips/{clip_id}/rating", { params: { path: { clip_id: id } }, body: { rating, reasons } })),
    onMutate: async ({ id, rating, reasons }) => {
      await qc.cancelQueries({ queryKey: keys.clips });
      const previous = qc.getQueryData<Clip[]>(clipsKey());
      qc.setQueryData<Clip[]>(clipsKey(), (old) => old?.map((c) => (c.id === id ? { ...c, rating, reasons } : c)));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(clipsKey(), context.previous),
    onSettled: () => { invalidate(); qc.invalidateQueries({ queryKey: ["learning"] }); },
  });
}

/** The brief as pasted, kept whole for Ask (D76). */
export const useCampaignBrief = (name: string | undefined) =>
  useQuery({ queryKey: ["brief", name], enabled: !!name,
             queryFn: async () => (await fetch(`/api/campaigns/${name}/brief`)).json() as Promise<{ saved_at: string | null; chars: number }> });
export async function saveCampaignBrief(name: string, text: string) {
  const res = await fetch(`/api/campaigns/${name}/brief`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
}

/** One click on a ready clip: skip it and learn from it; `undo` restores it (D74). */
/** Not good: skip a ready clip and learn from it, with what worked and what didn't (D86). */
export async function markNotGood(id: number, undo?: { status: string }, reasons: string[] = []) {
  const res = await fetch(`/api/clips/${id}/not-good`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(undo ? { undo: true, status: undo.status } : { reasons }) });
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
}

/* ---------- Ask (the research chat) ---------- */

export type ResearchStatus = components["schemas"]["ResearchStatus"];
export type Thread = components["schemas"]["Thread"];
export type ThreadDetail = components["schemas"]["ThreadDetail"];
export type ResearchMessage = components["schemas"]["Message"];

export const useResearchStatus = () =>
  useQuery({ queryKey: ["research", "status"], queryFn: () => unwrap(api.GET("/api/research/status")) });
export const useThreads = () =>
  useQuery({ queryKey: ["research", "threads"], queryFn: () => unwrap(api.GET("/api/research/threads")) });
export const useThread = (id: number | undefined) =>
  useQuery({ queryKey: ["research", "thread", id], enabled: id !== undefined,
             queryFn: () => unwrap(api.GET("/api/research/threads/{thread_id}", { params: { path: { thread_id: id! } } })) });
export const askResearch = (text: string, threadId?: number) =>
  unwrap(api.POST("/api/research/ask", { body: { text, thread_id: threadId } }));
export const deleteThread = (id: number) =>
  unwrap(api.DELETE("/api/research/threads/{thread_id}", { params: { path: { thread_id: id } } }));

/* ---------- finding campaigns ---------- */

export type FoundCampaign = components["schemas"]["FoundCampaign"];
export type CampaignCheck = { form: CampaignForm; fit: components["schemas"]["Fit"] };

export const useFound = () => useQuery({ queryKey: ["found"], queryFn: () => unwrap(api.GET("/api/found")) });
export const checkCampaign = async (text: string) =>
  (await unwrap(api.POST("/api/campaigns/check", { body: { text } }))) as unknown as CampaignCheck;
export const checkFound = async (key: string) =>
  (await unwrap(api.POST("/api/found/{key}/check", { params: { path: { key } } }))) as unknown as CampaignCheck;
export const dismissFound = (key: string) =>
  unwrap(api.POST("/api/found/{key}/dismiss", { params: { path: { key } } }));

/* ---------- campaign alerts (Discord) ---------- */

export type Alerts = components["schemas"]["Alerts"];
export type DiscordBot = components["schemas"]["DiscordBot"];
export type AlertCheck = components["schemas"]["AlertCheck"];

export const useAlerts = () => useQuery({ queryKey: ["alerts"], queryFn: () => unwrap(api.GET("/api/alerts")) });
/** Asks Discord, so only while the setup is open. */
export const useDiscordBot = (enabled: boolean) =>
  useQuery({ queryKey: ["alerts", "discord"], enabled, retry: false, staleTime: 30_000,
             queryFn: () => unwrap(api.GET("/api/alerts/discord")) });
export const watchChannels = (ids: string[]) => unwrap(api.PUT("/api/alerts/channels", { body: { ids } }));
export const saveAlertPrefs = (profile: string, min_rate: number) =>
  unwrap(api.PUT("/api/alerts/prefs", { body: { profile, min_rate } }));
export const checkAlerts = () => unwrap(api.POST("/api/alerts/check"));
export type WhopFeed = components["schemas"]["WhopFeed"];
/** Asks Whop, so only while the setup is open and signed in. */
export const useWhopFeeds = (enabled: boolean) =>
  useQuery({ queryKey: ["alerts", "whop"], enabled, retry: false, staleTime: 30_000,
             queryFn: () => unwrap(api.GET("/api/alerts/whop")) });
export const watchWhopFeeds = (ids: string[]) => unwrap(api.PUT("/api/alerts/whop/feeds", { body: { ids } }));
type ConnectState = { state: string; message: string; url: string };
export const startWhopConnect = () => unwrap(api.POST("/api/alerts/whop/connect")) as Promise<ConnectState>;
export const whopConnectState = () => unwrap(api.GET("/api/alerts/whop/connect")) as Promise<ConnectState>;
export const disconnectWhop = () => unwrap(api.POST("/api/alerts/whop/disconnect"));
export const testAlertPush = () => unwrap(api.POST("/api/alerts/test-push"));

/** Open the New campaign form already filled in (read once by CampaignEditor). */
export function prefillCampaign(value: { form?: CampaignForm; title?: string; brief?: string }) {
  try { sessionStorage.setItem("clipper.prefill", JSON.stringify(value)); } catch { /* private window */ }
}

/* ---------- posts and proof ---------- */

export const proofUrl = (id: number) => `/media/${id}/proof`;

export function useAddPostLink() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, url }: { id: number; url: string }) =>
      unwrap(api.POST("/api/clips/{clip_id}/posts", { params: { path: { clip_id: id } }, body: { url } })),
    onSettled: invalidate,
  });
}

/* ---------- footage from shared links ---------- */

export interface LinkContents { kind: string; zipped: boolean; files: { name: string; size: number | null }[] }
export interface FootageImport {
  id: number; link: string; kind: string; names: string[]; status: "queued" | "running" | "done" | "failed";
  current: string; done_bytes: number; total_bytes: number | null; files_done: number; message: string; saved: string[];
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data as T;
}

export const inspectLink = (url: string) => postJson<LinkContents>("/api/imports/inspect", { url });
export const startImport = (url: string, files: string[], campaign = "") =>
  postJson<FootageImport>("/api/imports", { url, files, campaign });
export const useImports = () =>
  useQuery({ queryKey: ["imports"], queryFn: async () => (await fetch("/api/imports")).json() as Promise<FootageImport[]> });

export type PostCopy = components["schemas"]["PostCopy"];
export type BriefProblem = components["schemas"]["BriefProblem"];
export type CaptionRule = components["schemas"]["CaptionRule"];

/** The user's own caption for a clip not yet posted (D81). */
export function useEditCaption() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, caption }: { id: number; caption: string }) =>
      unwrap(api.PUT("/api/clips/{clip_id}/caption", { params: { path: { clip_id: id } }, body: { caption } })),
    onSettled: invalidate,
  });
}

export function useRecheckRules() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST("/api/campaigns/{name}/recheck", { params: { path: { name } } })),
    onSettled: invalidate,
  });
}

export type AccountGroup = components["schemas"]["AccountGroup"];

export const useAccountGroups = () =>
  useQuery({ queryKey: ["account-groups"], queryFn: () => unwrap(api.GET("/api/account-groups")) });

/** Create a group, or change one (with its id). */
export function useSaveGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (group: AccountGroup) => unwrap(api.POST("/api/account-groups", { body: group })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["account-groups"] }); qc.invalidateQueries({ queryKey: keys.accounts }); },
  });
}

export function useDeleteGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE("/api/account-groups/{group_id}", { params: { path: { group_id: id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["account-groups"] }); qc.invalidateQueries({ queryKey: keys.accounts }); },
  });
}

/* ---------- the editor (D103) ---------- */

export type EditorView = components["schemas"]["EditorView"];
export type EditRule = components["schemas"]["EditRule"];

/** A video to edit: a library clip, or a source video for a campaign. */
export const useEditor = (q: { clip?: number; source?: string; campaign?: string }) =>
  useQuery({
    queryKey: ["editor", q.clip ?? null, q.source ?? null, q.campaign ?? null],
    queryFn: () => unwrap(api.GET("/api/editor", { params: { query: q } })),
    enabled: q.clip !== undefined || Boolean(q.source && q.campaign),
    retry: false,
  });

export const usePeaks = (sourceId: string | undefined) =>
  useQuery({
    queryKey: ["editor-peaks", sourceId],
    queryFn: () => unwrap(api.GET("/api/editor/{source_id}/peaks", { params: { path: { source_id: sourceId ?? "" } } })),
    enabled: Boolean(sourceId),
    staleTime: Infinity,
  });

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data as T;
}

export const prepareEditor = (campaign: string, source: string) =>
  post<Job>("/api/editor/prepare", { campaign, source });

export type EditBody = { source_id: string; campaign: string; clip?: number | null; edit: unknown };
export const previewEdit = (body: EditBody) => post<{ url: string; length: number }>("/api/editor/preview", body);
export const saveEdit = (body: EditBody) => post<{ queued: boolean; clip?: number; job?: Job }>("/api/editor/save", body);

/* ---------- what's working (D105) ---------- */

export type WhatsWorking = components["schemas"]["WhatsWorking"];
export const useWhatsWorking = () =>
  useQuery({ queryKey: ["whats-working"], queryFn: () => unwrap(api.GET("/api/learning/compare")) });
export const tightenEdit = (body: EditBody) =>
  post<{ edit: unknown; removed: number; cuts: { start: number; end: number; why: string }[] }>("/api/editor/tighten", body);

/* ---------- Create: the user's own channel (D108) ---------- */

export type ClipFill = "auto" | "planned" | "loop" | "slow" | "hold";
export interface CreateVisual {
  kind: "stock" | "diagram"; query: string; queries?: string[]; card?: string; template: string; title: string; labels: string[];
  clip?: string; clip_start?: number | null; fill?: ClipFill;   // the user's own clip for this sentence (D119)
  manual?: boolean; idea?: string; sketch?: unknown;            // a picture the user chose (D120)
  wish?: string; notice?: string;                                // D129
  hold?: boolean;                                                // keep the drawing before on screen (D124)
  picked?: Record<string, unknown>[]; avoid?: string[]; redo?: boolean; previous?: Record<string, unknown> | null;   // D125
}
export interface MineClip { id: string; name: string; duration: number; width: number; height: number; low_res: boolean; used: number[]; missing: boolean }
export interface FootageOffer {
  beat: number; seconds: number; clips: number; searches: string[];
  candidates: { id: string; tags: string; duration: number; tall: boolean; score: number | null; query: string }[];
}
export interface Mine { auto: boolean; fill: ClipFill; clips: MineClip[] }
export interface CreateBeat { text: string; emphasis: string; visual: CreateVisual }
export interface CreateScript { title: string; beats: CreateBeat[]; description: string; hashtags: string[]; take?: number }
export interface CreateTopic { id: number; question: string; angle: string; felt: number; status: string }
export interface CreateVideo {
  id: number; topic_id: number | null; status: "draft" | "approved" | "voiced" | "building" | "built" | "failed";
  script: CreateScript; check_notes: string; voice: string; clip_id: number | null; error: string;
  created_at: string; stage: string | null; pct: number | null; mine: Mine; cancelling?: boolean;
  shots?: { beats: number[]; start: number; end: number }[]; updated_at?: string; problem?: string;
}
export interface CreateView {
  channel: { name: string; handle: string; voice: string; campaign: string; words_per_second: number };
  topics: CreateTopic[]; videos: CreateVideo[];
}

export const useCreate = () =>
  useQuery({ queryKey: ["create"], queryFn: async () => {
    const res = await fetch("/api/create");
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<CreateView>;
  } });

export interface CreateAI {
  order: string[]; last_used: string; misses: Record<string, string>; claude_only: boolean; problem: string; spent_usd: number;
}
/** Which AI Create will ask, and why any didn't answer (no model call; D118). */
export const useCreateAI = () =>
  useQuery({ queryKey: ["create", "ai"], queryFn: async () => {
    const res = await fetch("/api/create/ai");
    if (!res.ok) throw new Error(res.statusText);
    return res.json() as Promise<CreateAI>;
  }, staleTime: 10_000 });

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data as T;
}

export const createApi = {
  ideas: (count = 20) => send<{ added: number }>("POST", "/api/create/ideas", { count }),
  skip: (topic: number) => send("POST", `/api/create/topics/${topic}/skip`),
  script: (topic: number) => send<{ id: number }>("POST", `/api/create/topics/${topic}/script`),
  rewrite: (video: number) => send("POST", `/api/create/videos/${video}/rewrite`),
  edit: (video: number, script: Partial<CreateScript>) => send("PUT", `/api/create/videos/${video}/script`, { script }),
  approve: (video: number) => send("POST", `/api/create/videos/${video}/approve`),
  footage: (video: number, body: { beat: number; wish: string }) => send<FootageOffer>("POST", `/api/create/videos/${video}/footage`, body),
  footageUse: (video: number, body: { beat: number; ids: string[] }) => send("POST", `/api/create/videos/${video}/footage/use`, body),
  redo: (video: number, body: { beat: number; want: "footage" | "drawing" | "undo"; note: string }) =>
    send("POST", `/api/create/videos/${video}/redo`, body),
  cancel: (video: number) => send<{ stopping: boolean }>("POST", `/api/create/videos/${video}/cancel`),
  build: (video: number) => send("POST", `/api/create/videos/${video}/build`),
  pictures: (video: number) => send("POST", `/api/create/videos/${video}/pictures`),
  remove: (video: number) => send("DELETE", `/api/create/videos/${video}`),
  own: (body: { title: string; text: string; description: string; hashtags: string; plan: boolean }) =>
    send<{ id: number }>("POST", "/api/create/videos", body),
  ready: () => fetch("/api/create/ready").then((r) => r.json() as Promise<{ name: string; title: string; about: string; words: number }[]>),
  useReady: (name: string) => send<{ id: number }>("POST", `/api/create/ready/${encodeURIComponent(name)}`),
  plan: (video: number) => send("POST", `/api/create/videos/${video}/plan`),
  check: (video: number) => send<{ notes: string }>("POST", `/api/create/videos/${video}/check`),
  clipAdd: async (video: number, file: File) => {
    const res = await fetch(`/api/create/videos/${video}/clips/${encodeURIComponent(file.name)}`, { method: "PUT", body: file });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
    return data as MineClip;
  },
  clipRemove: (video: number, clip: string) => send("DELETE", `/api/create/videos/${video}/clips/${clip}`),
  clipSettings: (video: number, body: { auto?: boolean; fill?: ClipFill }) => send("PUT", `/api/create/videos/${video}/clips-settings`, body),
  place: (video: number, how: "ai" | "order" | "clear", strict = false) => send<{ note: string }>("POST", `/api/create/videos/${video}/place`, { how, strict }),
  placement: (video: number, body: { beat: number; clip: string; start: number | null; fill: ClipFill }) =>
    send("PUT", `/api/create/videos/${video}/placement`, body),
  voice: async (video: number, file: File) => {
    const res = await fetch(`/api/create/videos/${video}/voice/${encodeURIComponent(file.name)}`, { method: "PUT", body: file });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || res.statusText);
  },
};
