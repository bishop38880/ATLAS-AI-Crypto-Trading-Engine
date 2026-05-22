/** Formatting for Memory / RAG dashboard (display-only numbers). */

export function format_memory_integer(value: number): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

export function format_memory_decimal(value: number, fraction_digits = 2): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: fraction_digits,
    maximumFractionDigits: fraction_digits,
  }).format(value);
}

export function format_memory_percent(value: number, fraction_digits = 1): string {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return `${format_memory_decimal(value, fraction_digits)}%`;
}

/** Accessible description for the result badge (visual still shows payload text). */
export function format_memory_result_aria(result: string): string {
  const trimmed = result.trim();
  if (trimmed.length === 0) {
    return "Unknown result";
  }
  if (trimmed.includes("✓") || trimmed.toLowerCase().includes("stored")) {
    return trimmed.replace("✓", "").trim() || "Success";
  }
  if (trimmed.includes("✗") || trimmed.toLowerCase().includes("fail")) {
    return trimmed;
  }
  return trimmed;
}
