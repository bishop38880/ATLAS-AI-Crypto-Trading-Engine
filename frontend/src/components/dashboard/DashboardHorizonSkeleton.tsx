import type { ReactElement } from "react";

import { Skeleton } from "../ui/Skeleton";

/** Loading placeholders shaped like today strip, ladder, and asset grid. */
export function DashboardHorizonSkeleton(): ReactElement {
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800/90 bg-slate-950/45 px-3 py-2.5">
        <Skeleton className="h-3 w-12" />
        <Skeleton className="h-6 w-36 rounded-md" />
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-3 w-20" />
      </div>

      <div className="command-card space-y-3 p-4">
        <Skeleton className="h-3 w-40" />
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-24 rounded-md" />
          ))}
        </div>
      </div>

      <div className="command-card space-y-3 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="h-6 w-32 rounded-md" />
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-44 rounded-lg" />
          ))}
        </div>
      </div>
    </>
  );
}
