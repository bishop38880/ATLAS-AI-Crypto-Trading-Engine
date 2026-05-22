import { cn } from "../../lib/cn";

export type AgentCatalogRole = "CONFLUENCE" | "OVERLAY" | "VETO" | "SHADOW";

export interface AgentTypeBadgeProps {
  role: AgentCatalogRole | string;
  className?: string;
}

const roleClass: Record<string, string> = {
  CONFLUENCE: "type-badge type-badge-confluence",
  OVERLAY: "type-badge type-badge-overlay",
  VETO: "type-badge type-badge-veto",
  SHADOW: "type-badge type-badge-shadow",
};

export function AgentTypeBadge({ role, className }: AgentTypeBadgeProps) {
  const normalized = String(role).toUpperCase();
  return (
    <span className={cn(roleClass[normalized] ?? "type-badge type-badge-shadow", className)}>
      {normalized}
    </span>
  );
}
