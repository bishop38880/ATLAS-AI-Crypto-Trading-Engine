/** MIME type for HTML5 drag payload from asset cards (or legacy pin chips) → ladder slots. */
export const DASHBOARD_PIN_DRAG_MIME_TYPE = "application/x-atlas-dashboard-pin";

export function serialize_dashboard_pin_for_drag(pair: string): string {
  return pair.trim();
}

export function read_dashboard_pin_drop(data_transfer: DataTransfer): string | null {
  try {
    const primary_trimmed = data_transfer.getData(DASHBOARD_PIN_DRAG_MIME_TYPE).trim();
    if (primary_trimmed.length > 0) {
      return primary_trimmed;
    }
  } catch {
    /* Clipboard access can fail in some browsers — fall through */
  }

  try {
    const fallback_trimmed = data_transfer.getData("text/plain").trim();
    return fallback_trimmed.length > 0 ? fallback_trimmed : null;
  } catch {
    return null;
  }
}
