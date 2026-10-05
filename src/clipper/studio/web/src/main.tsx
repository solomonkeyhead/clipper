import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute, createRoute, createRouter, lazyRouteComponent, redirect, RouterProvider,
} from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import { AppShell } from "./components/AppShell";
import { useUI } from "./lib/store";
import { CampaignPage, CampaignsPage } from "./pages/CampaignPages";
import { DashboardPage } from "./pages/HomePage";
import { NewClipsPage } from "./pages/NewClipsPage";
import { ClipsPage, type ClipFilter } from "./pages/WorkPages";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: true, retry: 1 } },
});

// Pages opened now and then load when first opened, keeping the first screen quick.
const CampaignEditorPage = lazyRouteComponent(() => import("./pages/CampaignEditor"), "CampaignEditorPage");
const CreatePage = lazyRouteComponent(() => import("./pages/CreatePage"), "CreatePage");
const EditorPage = lazyRouteComponent(() => import("./pages/EditorPage"), "EditorPage");
const LearningPage = lazyRouteComponent(() => import("./pages/LearningPage"), "LearningPage");
const AccountsPage = lazyRouteComponent(() => import("./pages/SettingsPages"), "AccountsPage");
const SettingsPage = lazyRouteComponent(() => import("./pages/SettingsPages"), "SettingsPage");
const StatsPage = lazyRouteComponent(() => import("./pages/StatsPage"), "StatsPage");

const root = createRootRoute({ component: AppShell });
const routes = [
  createRoute({ getParentRoute: () => root, path: "/", component: DashboardPage }),
  // The Research page became the Ask panel (top bar) and Find campaigns (Campaigns), D63.
  createRoute({ getParentRoute: () => root, path: "/research",
                beforeLoad: () => { throw redirect({ to: "/campaigns", search: { find: "1" } }); } }),
  createRoute({
    getParentRoute: () => root, path: "/campaigns", component: CampaignsPage,
    validateSearch: (s: Record<string, unknown>): { find?: string } => ({
      find: s.find === "1" || s.find === 1 ? "1" : undefined,
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/campaigns/new", component: CampaignEditorPage }),
  createRoute({ getParentRoute: () => root, path: "/campaigns/$name", component: CampaignPage }),
  createRoute({ getParentRoute: () => root, path: "/campaigns/$name/edit", component: CampaignEditorPage }),
  createRoute({
    getParentRoute: () => root, path: "/clips", component: ClipsPage,
    validateSearch: (s: Record<string, unknown>): { status?: ClipFilter; campaign?: string } => ({
      status: typeof s.status === "string" ? (s.status as ClipFilter) : undefined,
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
    }),
  }),
  createRoute({
    getParentRoute: () => root, path: "/new", component: NewClipsPage,
    validateSearch: (s: Record<string, unknown>): { campaign?: string; source?: string } => ({
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
      source: typeof s.source === "string" ? s.source : undefined,
    }),
  }),
  // Create (D108): original Shorts for the user's own channel.
  createRoute({ getParentRoute: () => root, path: "/create", component: CreatePage }),
  // The editor (D103): a library clip, or a source video for a campaign.
  createRoute({
    getParentRoute: () => root, path: "/edit", component: EditorPage,
    validateSearch: (s: Record<string, unknown>): { clip?: number; source?: string; campaign?: string } => ({
      clip: s.clip !== undefined && s.clip !== "" && !Number.isNaN(Number(s.clip)) ? Number(s.clip) : undefined,
      source: typeof s.source === "string" ? s.source : undefined,
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
    }),
  }),
  // Queue and Submissions became filters on Clips.
  createRoute({ getParentRoute: () => root, path: "/queue",
                beforeLoad: () => { throw redirect({ to: "/clips", search: { status: "ready" } }); } }),
  createRoute({ getParentRoute: () => root, path: "/submissions",
                beforeLoad: () => { throw redirect({ to: "/clips", search: { status: "posted" } }); } }),
  createRoute({
    getParentRoute: () => root, path: "/stats", component: StatsPage,
    // Where a number on another page came from: that campaign, sorted by that column (D143).
    validateSearch: (s: Record<string, unknown>): { campaign?: string; sort?: string } => ({
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
      sort: typeof s.sort === "string" ? s.sort : undefined,
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/learning", component: LearningPage }),
  createRoute({ getParentRoute: () => root, path: "/accounts", component: AccountsPage }),
  createRoute({ getParentRoute: () => root, path: "/settings", component: SettingsPage }),
];
const router = createRouter({
  routeTree: root.addChildren(routes),
  defaultPreload: "intent",
  defaultPreloadDelay: 100,
});

declare module "@tanstack/react-router" {
  interface Register { router: typeof router }
}

function App() {
  const theme = useUI((s) => s.theme);
  return (
    <QueryClientProvider client={queryClient}>
      <Tooltip.Provider delayDuration={500}>
        <RouterProvider router={router} />
        <Toaster
          theme={theme === "light" ? "light" : "dark"}
          position="bottom-center"
          toastOptions={{ className: "!bg-surface-3 !border-line-strong !text-fg" }}
        />
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
