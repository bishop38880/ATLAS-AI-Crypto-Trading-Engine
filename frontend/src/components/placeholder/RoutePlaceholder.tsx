import type { ReactElement } from "react";

import { Skeleton } from "../ui/Skeleton";

export type PlaceholderSkeleton =
  | "dashboard-33"
  | "signals-list"
  | "signal-detail"
  | "assets-93"
  | "agents-panel"
  | "providers"
  | "gnn"
  | "memory"
  | "omnibox"
  | "monitoring"
  | "settings";

export interface RoutePlaceholderProps {
  title: string;
  description: string;
  skeleton: PlaceholderSkeleton;
}

function DashboardSkeleton(): ReactElement {
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-6 lg:grid-cols-11">
      {Array.from({ length: 33 }).map((_, i) => (
        <Skeleton key={i} height={48} className="w-full" />
      ))}
    </div>
  );
}

function SignalsListSkeleton(): ReactElement {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-3">
          <Skeleton height={40} width={40} rounded />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <Skeleton height={12} width="40%" />
            <Skeleton height={10} width="70%" />
          </div>
          <Skeleton height={24} width={56} />
        </div>
      ))}
    </div>
  );
}

function SignalDetailSkeleton(): ReactElement {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-4">
        <Skeleton height={160} className="w-full" />
        <div className="grid grid-cols-2 gap-2">
          <Skeleton height={72} />
          <Skeleton height={72} />
        </div>
      </div>
      <div className="space-y-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} height={32} className="w-full" />
        ))}
      </div>
    </div>
  );
}

function Assets93Skeleton(): ReactElement {
  return (
    <div className="grid grid-cols-6 gap-1 sm:grid-cols-9 md:grid-cols-11">
      {Array.from({ length: 93 }).map((_, i) => (
        <Skeleton key={i} height={28} className="w-full" />
      ))}
    </div>
  );
}

function AgentsPanelSkeleton(): ReactElement {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 11 }).map((_, i) => (
        <div key={i} className="space-y-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-3">
          <Skeleton height={14} width="50%" />
          <Skeleton height={8} width="100%" />
          <Skeleton height={8} width="85%" />
        </div>
      ))}
    </div>
  );
}

function ProvidersSkeleton(): ReactElement {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
          <div className="flex items-center justify-between gap-2">
            <Skeleton height={14} width={120} />
            <Skeleton height={22} width={64} />
          </div>
          <Skeleton height={6} className="w-full" />
          <Skeleton height={10} width="40%" />
        </div>
      ))}
    </div>
  );
}

function GnnSkeleton(): ReactElement {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-3 lg:col-span-2">
        <Skeleton height={220} className="w-full" />
        <div className="grid grid-cols-3 gap-2">
          <Skeleton height={64} />
          <Skeleton height={64} />
          <Skeleton height={64} />
        </div>
      </div>
      <div className="space-y-3">
        <Skeleton height={120} />
        <Skeleton height={120} />
      </div>
    </div>
  );
}

function MemorySkeleton(): ReactElement {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Skeleton height={240} />
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} height={40} className="w-full" />
        ))}
      </div>
    </div>
  );
}

function OmniboxSkeleton(): ReactElement {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4">
      <Skeleton height={44} className="w-full" />
      <div className="min-h-[280px] space-y-3 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
        <Skeleton height={12} width="30%" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} height={14} className="w-full" />
        ))}
      </div>
    </div>
  );
}

function MonitoringSkeleton(): ReactElement {
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} height={88} className="w-full" />
        ))}
      </div>
      <Skeleton height={320} className="w-full" />
    </div>
  );
}

function SettingsSkeleton(): ReactElement {
  return (
    <div className="mx-auto max-w-xl space-y-4">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center justify-between gap-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-surface)] p-4">
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton height={12} width="40%" />
            <Skeleton height={10} width="80%" />
          </div>
          <Skeleton height={28} width={48} rounded />
        </div>
      ))}
    </div>
  );
}

function renderSkeleton(kind: PlaceholderSkeleton): ReactElement {
  switch (kind) {
    case "dashboard-33":
      return <DashboardSkeleton />;
    case "signals-list":
      return <SignalsListSkeleton />;
    case "signal-detail":
      return <SignalDetailSkeleton />;
    case "assets-93":
      return <Assets93Skeleton />;
    case "agents-panel":
      return <AgentsPanelSkeleton />;
    case "providers":
      return <ProvidersSkeleton />;
    case "gnn":
      return <GnnSkeleton />;
    case "memory":
      return <MemorySkeleton />;
    case "omnibox":
      return <OmniboxSkeleton />;
    case "monitoring":
      return <MonitoringSkeleton />;
    case "settings":
      return <SettingsSkeleton />;
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

/** Placeholder route shell — interactive surface: **stub** (static copy + skeleton until wired). */
export function RoutePlaceholder({ title, description, skeleton }: RoutePlaceholderProps) {
  return (
    <div className="flex min-h-0 flex-col gap-6">
      <header className="space-y-2">
        <h1 className="display text-xl font-semibold tracking-tight text-[var(--text-primary)]">
          {title}
        </h1>
        <p className="max-w-3xl text-sm leading-relaxed text-[var(--text-secondary)]">
          {description}
        </p>
      </header>
      {renderSkeleton(skeleton)}
    </div>
  );
}
