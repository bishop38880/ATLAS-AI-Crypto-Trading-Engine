import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { Suspense, lazy } from "react";

import { AppShell } from "./components/layout/AppShell";
import { AssetUniversePage } from "./components/assets/AssetUniversePage";
import { DecisionJournalPage } from "./components/journal/DecisionJournalPage";
import { DashboardPage } from "./components/dashboard/DashboardPage";
import { MonitoringPage } from "./components/monitoring/MonitoringPage";
import { ProviderHealthPage } from "./components/providers/ProviderHealthPage";
import { AppErrorBoundary } from "./components/ui/ErrorBoundary";
import { SignalHistoryPage } from "./components/SignalHistoryPage";
import { CorrelationMatrixPage } from "./components/correlation/CorrelationMatrixPage";
import { GnnPage } from "./components/GnnPage";
import { HydraLaunchPage } from "./components/HydraLaunchPage";
import {
  SettingsPlaceholderPage,
} from "./routes/placeholder-pages";

import { OmniBoxPage } from "./components/omnibox/OmniBoxPage";
import { FundingCommandCenterPage } from "./components/funding/FundingCommandCenterPage";
import { BacktestPage } from "./components/backtest/BacktestPage";
import { PaperTradingPage } from "./components/PaperTradingPage";
import { RegimeControlCenterPage } from "./components/regime/RegimeControlCenterPage";

import { AgentsPage } from "./components/agents/AgentsPage";
import { MemoryPage } from "./components/memory/MemoryPage";
import { RiskGovernorPage } from "./components/risk/RiskGovernorPage";
import { SignalAssetDetailPage } from "./components/signals/SignalAssetDetailPage";

const PipelineLivePage = lazy(async () => {
  const mod = await import("./components/PipelineLivePage");
  return { default: mod.PipelineLivePage };
});

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const shellLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app-shell",
  component: () => (
    <AppErrorBoundary>
      <AppShell>
        <Outlet />
      </AppShell>
    </AppErrorBoundary>
  ),
});

const pipelineRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/pipeline",
  component: () => (
    <Suspense
      fallback={
        <div className="flex min-h-[40vh] items-center justify-center px-4 text-sm text-[var(--text-secondary)]">
          Loading pipeline…
        </div>
      }
    >
      <PipelineLivePage />
    </Suspense>
  ),
});

const dashboardRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/",
  component: DashboardPage,
});

const regimeControlRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/regime",
  component: RegimeControlCenterPage,
});

const riskGovernorRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/risk",
  component: RiskGovernorPage,
});

const signalsRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/signals",
  component: SignalHistoryPage,
});

const paperTradingRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/paper-trade",
  component: PaperTradingPage,
});

const backtestRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/backtest",
  component: BacktestPage,
});

const journalRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/journal",
  component: DecisionJournalPage,
});

const signalDetailRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/signals/$asset",
  component: () => {
    const { asset } = signalDetailRoute.useParams();
    return <SignalAssetDetailPage asset={asset} />;
  },
});

const assetsRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/assets",
  component: AssetUniversePage,
});

const agentsRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/agents",
  component: AgentsPage,
});

const providersRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/providers",
  component: ProviderHealthPage,
});

const gnnRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/gnn",
  component: GnnPage,
});

const correlationRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/correlation",
  component: CorrelationMatrixPage,
});

const hydraRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/hydra",
  component: HydraLaunchPage,
});

const memoryRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/memory",
  component: MemoryPage,
});

const omniboxRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/omnibox",
  component: OmniBoxPage,
});

const monitoringRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/monitoring",
  component: MonitoringPage,
});

const fundingRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/funding",
  component: FundingCommandCenterPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => shellLayoutRoute,
  path: "/settings",
  component: SettingsPlaceholderPage,
});

const routeTree = rootRoute.addChildren([
  shellLayoutRoute.addChildren([
    dashboardRoute,
    pipelineRoute,
    regimeControlRoute,
    riskGovernorRoute,
    signalsRoute,
    paperTradingRoute,
    backtestRoute,
    journalRoute,
    signalDetailRoute,
    assetsRoute,
    agentsRoute,
    providersRoute,
    gnnRoute,
    correlationRoute,
    hydraRoute,
    memoryRoute,
    omniboxRoute,
    monitoringRoute,
    fundingRoute,
    settingsRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
