/** Relative timestamp labels for OmniBox transcript bubbles. */

export function format_omnibox_chat_timestamp(iso_utc: string): string {
  try {
    const instant = new Date(iso_utc);
    if (Number.isNaN(instant.getTime())) {
      return iso_utc;
    }
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(instant);
  } catch {
    return iso_utc;
  }
}
