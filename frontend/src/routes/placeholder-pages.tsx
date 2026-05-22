import type { ReactElement } from "react";

import { RoutePlaceholder } from "../components/placeholder/RoutePlaceholder";

export function DashboardPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="Dashboard"
      description="33-asset grid with live confluence scores and quick regime readouts. Real data lands in FE-2."
      skeleton="dashboard-33"
    />
  );
}



export function SignalDetailPlaceholderPage(props: { asset: string }): ReactElement {
  const { asset } = props;
  return (
    <RoutePlaceholder
      title={`Signal detail — ${asset}`}
      description="Per-asset deep dive: stacked agent contributions, historical score path, and narrative context."
      skeleton="signal-detail"
    />
  );
}

export function AssetsPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="Asset Universe"
      description="93-asset rotation grid with coverage states, staleness, and macro bucket tags."
      skeleton="assets-93"
    />
  );
}

export function AgentsPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="Agent Intelligence"
      description="Eleven specialist agents plus MARL deliberation lane, verdict history, and weight transparency."
      skeleton="agents-panel"
    />
  );
}

export function ProvidersPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="Provider Health"
      description="Hydra ingest, REST providers, circuit breakers, and latency envelopes with degradation reasons."
      skeleton="providers"
    />
  );
}

export function GnnPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="GNN Intelligence"
      description="Shadow GraphSAGE metrics, directional OBTI summaries from PROMETHEUS, and cross-asset lead–lag panels."
      skeleton="gnn"
    />
  );
}

export function OmniboxPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="OmniBox"
      description="Natural-language surface over POLARIS intelligence with grounded citations and trace hooks."
      skeleton="omnibox"
    />
  );
}

export function MonitoringPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="Monitoring"
      description="Startup dependency matrix, Prometheus targets, and alert routing for the intelligence plane."
      skeleton="monitoring"
    />
  );
}

export function SettingsPlaceholderPage(): ReactElement {
  return (
    <RoutePlaceholder
      title="Settings"
      description="Display density, theme tokens, notification routing, and operator preferences (display-only)."
      skeleton="settings"
    />
  );
}
