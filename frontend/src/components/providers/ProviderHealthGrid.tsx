import { memo, useEffect, useState, type ReactElement } from "react";

import type { ProviderHealth } from "../../store/index";
import { ProviderCard } from "./ProviderCard";

export interface ProviderHealthGridProps {
  providers: ProviderHealth[];
}

const MAX_SPARK = 20;

export const ProviderHealthGrid = memo(function ProviderHealthGrid({
  providers,
}: ProviderHealthGridProps): ReactElement {
  const [p99ByName, setP99ByName] = useState<Map<string, number[]>>(() => new Map());

  useEffect(() => {
    setP99ByName((prev) => {
      const next = new Map(prev);
      for (const p of providers) {
        const prevSeries = next.get(p.name) ?? [];
        const merged = [...prevSeries, p.p99Ms];
        const trimmed =
          merged.length > MAX_SPARK ? merged.slice(merged.length - MAX_SPARK, merged.length) : merged;
        next.set(p.name, trimmed);
      }
      return next;
    });
  }, [providers]);

  return (
    <div className="grid gap-4 sm:grid-cols-1 lg:grid-cols-2 xl:grid-cols-3">
      {providers.map((p) => (
        <ProviderCard key={p.name} provider={p} p99History={p99ByName.get(p.name) ?? []} />
      ))}
    </div>
  );
});
