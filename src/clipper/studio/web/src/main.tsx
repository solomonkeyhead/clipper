import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute, createRoute, createRouter, redirect, RouterProvider,
} from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import { AppShell } from "./components/AppShell";
import { useUI } from "./lib/store";
import { CampaignEditorPage } from "./pages/CampaignEditor";
import { CampaignPage, CampaignsPage } from "./pages/CampaignPages";
import { DashboardPage } from "./pages/HomePage";
import { LearningPage } from "./pages/LearningPage";
import { NewClipsPage } from "./pages/NewClipsPage";
import { ResearchPage } from "./pages/ResearchPage";
import { AccountsPage, SettingsPage } from "./pages/SettingsPages";
import { StatsPage } from "./pages/StatsPage";
import { ClipsPage, type ClipFilter } from "./pages/WorkPages";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: true, retry: 1 } },
});

const root = createRootRoute({ component: AppShell });
const routes = [
  createRoute({ getParentRoute: () => root, path: "/", component: DashboardPage }),
  createRoute({
    getParentRoute: () => root, path: "/research", component: ResearchPage,
    validateSearch: (s: Record<string, unknown>): { tab?: "ask" | "niches" | "saved"; thread?: number; niche?: number } => ({
      tab: s.tab === "ask" || s.tab === "niches" || s.tab === "saved" ? s.tab : undefined,
      thread: typeof s.thread === "number" ? s.thread : undefined,
      niche: typeof s.niche === "number" ? s.niche : undefined,
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/campaigns", component: CampaignsPage }),
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
    validateSearch: (s: Record<string, unknown>): { campaign?: string } => ({
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
    }),
  }),
  // Queue and Submissions became filters on Clips.
  createRoute({ getParentRoute: () => root, path: "/queue",
                beforeLoad: () => { throw redirect({ to: "/clips", search: { status: "ready" } }); } }),
  createRoute({ getParentRoute: () => root, path: "/submissions",
                beforeLoad: () => { throw redirect({ to: "/clips", search: { status: "posted" } }); } }),
  createRoute({ getParentRoute: () => root, path: "/stats", component: StatsPage }),
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
