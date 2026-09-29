import createClient from "openapi-fetch";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components, paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" });

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
export const useHome = () => useQuery({ queryKey: keys.home, queryFn: () => unwrap(api.GET("/api/home")) });
export const useCampaigns = () =>
  useQuery({ queryKey: keys.campaigns, queryFn: () => unwrap(api.GET("/api/campaigns")) });
export const useCampaign = (name: string) =>
  useQuery({ queryKey: keys.campaign(name),
             queryFn: () => unwrap(api.GET("/api/campaigns/{name}", { params: { path: { name } } })) });
export const useClips = () => useQuery({ queryKey: keys.clips, queryFn: () => unwrap(api.GET("/api/clips")) });
export const usePosts = () => useQuery({ queryKey: keys.posts, queryFn: () => unwrap(api.GET("/api/posts")) });
export const useAccounts = () =>
  useQuery({ queryKey: keys.accounts, queryFn: () => unwrap(api.GET("/api/accounts")) });
export const useSettings = () =>
  useQuery({ queryKey: keys.settings, queryFn: () => unwrap(api.GET("/api/settings")) });

/** Everything that shows clips or posts. */
const CLIP_KEYS = [keys.home, keys.campaigns, keys.clips, keys.posts, ["campaign"]];

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
      const previous = qc.getQueryData<Clip[]>(keys.clips);
      qc.setQueryData<Clip[]>(keys.clips, (old) =>
        old?.map((c) => (c.id === id ? { ...c, status, marked: status } : c)));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(keys.clips, context.previous),
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

export function useSetSubmitted() {
  const qc = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ url, submitted }: { url: string; submitted: boolean }) =>
      unwrap(api.PUT("/api/posts/submitted", { body: { url, submitted } })),
    onMutate: async ({ url, submitted }) => {
      await qc.cancelQueries({ queryKey: keys.posts });
      const previous = qc.getQueryData<Post[]>(keys.posts);
      qc.setQueryData<Post[]>(keys.posts, (old) =>
        old?.map((p) => (p.url === url ? { ...p, submitted_at: submitted ? "now" : null } : p)));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(keys.posts, context.previous),
    onSettled: invalidate,
  });
}

export function useSetCampaign() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ name, ...changes }: { name: string; archived?: boolean; auto_post?: boolean | null }) =>
      unwrap(api.PATCH("/api/campaigns/{name}", { params: { path: { name } }, body: changes })),
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
      const previous = qc.getQueryData<Clip[]>(keys.clips);
      qc.setQueryData<Clip[]>(keys.clips, (old) => old?.filter((c) => c.id !== id));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(keys.clips, context.previous),
    onSettled: invalidate,
  });
}

/* ---------- new clips: footage, uploads, jobs ---------- */

export interface Source { name: string; path: string; size_mb: number; modified: string; folder: string }
export interface Job {
  id: number; campaign: string; source: string; name: string; top: number;
  status: "queued" | "running" | "done" | "failed"; stage: string; pct: number;
  clips: number; message: string; created: string; finished: string;
}

export const useSources = () =>
  useQuery({ queryKey: ["sources"], queryFn: async () => (await fetch("/api/sources")).json() as Promise<Source[]> });
export const useJobs = () =>
  useQuery({ queryKey: ["jobs"], queryFn: async () => (await fetch("/api/jobs")).json() as Promise<Job[]> });

export async function startJob(campaign: string, source: string, top: number): Promise<Job> {
  const res = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ campaign, source, top }) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data as Job;
}

/** Upload with progress (fetch can't report upload progress; XHR can). */
export function uploadVideo(file: File, onProgress: (fraction: number) => void): Promise<Source> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads/${encodeURIComponent(file.name)}`);
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
      const previous = qc.getQueryData<Clip[]>(keys.clips);
      qc.setQueryData<Clip[]>(keys.clips, (old) => old?.map((c) => c.id === id
        ? { ...c, status: submitted ? "submitted" : c.posts.length ? "posted" : "ready" } : c));
      return { previous };
    },
    onError: (_e, _v, context) => context?.previous && qc.setQueryData(keys.clips, context.previous),
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
