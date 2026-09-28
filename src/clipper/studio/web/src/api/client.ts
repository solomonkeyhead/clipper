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
