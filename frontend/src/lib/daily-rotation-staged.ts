/** Max staged assets operator can queue before submitting `POST /api/executive/rotation-override`. */
export const DAILY_ROTATION_STAGED_CAP = 4;

/**
 * Returns a new array with `asset` appended, or null if duplicate or capacity reached.
 */
export function try_append_daily_rotation_staged(
  current: readonly string[],
  asset: string,
): string[] | null {
  if (current.includes(asset)) {
    return null;
  }
  if (current.length >= DAILY_ROTATION_STAGED_CAP) {
    return null;
  }
  return [...current, asset];
}
