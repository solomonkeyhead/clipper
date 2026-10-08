import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute, createRoute, createRouter, lazyRouteComponent, Link, redirect, RouterProvider,
} from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import { AppShell } from "./components/AppShell";
import { MapPinOff } from "lucide-react";
import { EmptyState, Skeleton } from "./components/ui";
import { useUI } from "./lib/store";
import { CampaignPage, CampaignsPage } from "./pages/CampaignPages";
import { DashboardPage } from "./pages/HomePage";
import { NewClipsPage } from "./pages/NewClipsPage";
import { PostQueuePage } from "./pages/PostQueue";
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

// A link to a page that isn't there (an old bookmark): say so, with the way home (D161).
const root = createRootRoute({
  component: AppShell,
  notFoundComponent: () => (
    <EmptyState icon={<MapPinOff />} title="No page here"
                body="This address isn't a page in Clipper. It may be an old link."
                action={<Link to="/" className="text-sm font-medium text-accent hover:underline">Go to the Dashboard</Link>} />
  ),
});
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
    validateSearch: (s: Record<string, unknown>): { status?: ClipFilter; campaign?: string; rate?: boolean } => ({
      status: typeof s.status === "string" ? (s.status as ClipFilter) : undefined,
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
      rate: s.rate === true || s.rate === "true" || s.rate === 1 || s.rate === "1" ? true : undefined,
    }),
  }),
  createRoute({
    getParentRoute: () => root, path: "/new", component: NewClipsPage,
    validateSearch: (s: Record<string, unknown>): { campaign?: string; source?: string } => ({
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
      source: typeof s.source === "string" ? s.source : undefined,
    }),
  }),
  // One clip at a time: post it, then submit its link (D149).
  createRoute({
    getParentRoute: () => root, path: "/post", component: PostQueuePage,
    validateSearch: (s: Record<string, unknown>): { step?: "post" | "submit"; campaign?: string } => ({
      step: s.step === "post" || s.step === "submit" ? s.step : undefined,
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
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
  // A page still loading shows its outline, not an empty screen for a second or more (D154).
  defaultPendingMs: 100,
  defaultPendingComponent: () => (
    <div className="flex flex-col gap-4"><Skeleton className="h-12 w-80" /><Skeleton className="h-96" /></div>
  ),
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
          // An open window (Radix) turns clicks off for the rest of the page; Undo has to stay clickable.
          style={{ pointerEvents: "auto" }}
          toastOptions={{ className: "!bg-surface-3 !border-line-strong !text-fg" }}
        />
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
