import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute, createRoute, createRouter, RouterProvider,
} from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";
import { AppShell } from "./components/AppShell";
import { useUI } from "./lib/store";
import { CampaignPage, CampaignsPage } from "./pages/CampaignPages";
import { HomePage } from "./pages/HomePage";
import { AccountsPage, SettingsPage } from "./pages/SettingsPages";
import { StatsPage } from "./pages/StatsPage";
import { ClipsPage, QueuePage, SubmissionsPage, type ClipFilter } from "./pages/WorkPages";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: true, retry: 1 } },
});

const root = createRootRoute({ component: AppShell });
const routes = [
  createRoute({ getParentRoute: () => root, path: "/", component: HomePage }),
  createRoute({ getParentRoute: () => root, path: "/campaigns", component: CampaignsPage }),
  createRoute({ getParentRoute: () => root, path: "/campaigns/$name", component: CampaignPage }),
  createRoute({
    getParentRoute: () => root, path: "/clips", component: ClipsPage,
    validateSearch: (s: Record<string, unknown>): { status?: ClipFilter; campaign?: string } => ({
      status: typeof s.status === "string" ? (s.status as ClipFilter) : undefined,
      campaign: typeof s.campaign === "string" ? s.campaign : undefined,
    }),
  }),
  createRoute({ getParentRoute: () => root, path: "/queue", component: QueuePage }),
  createRoute({ getParentRoute: () => root, path: "/submissions", component: SubmissionsPage }),
  createRoute({ getParentRoute: () => root, path: "/stats", component: StatsPage }),
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
