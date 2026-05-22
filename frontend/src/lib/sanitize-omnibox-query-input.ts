/** Strip injection-friendly invisible chars and enforce OmniBox length policy. */

const ZERO_WIDTH_AND_FORMAT_CHARS = /[\u200B-\u200D\uFEFF]/g;

export const OMNIBOX_QUERY_MAX_LENGTH = 500;

function strip_ascii_control_chars_except_newlines(raw: string): string {
  let result = "";
  for (const character of raw) {
    const code = character.codePointAt(0);
    if (code === undefined) {
      continue;
    }
    const is_disallowed_control =
      code <= 0x08 ||
      code === 0x0b ||
      code === 0x0c ||
      (code >= 0x0e && code <= 0x1f);
    if (is_disallowed_control) {
      continue;
    }
    result += character;
  }
  return result;
}

export function sanitize_omnibox_query_input(raw: string): string {
  const stripped = strip_ascii_control_chars_except_newlines(
    raw.replace(ZERO_WIDTH_AND_FORMAT_CHARS, ""),
  );
  if (stripped.length <= OMNIBOX_QUERY_MAX_LENGTH) {
    return stripped;
  }
  return stripped.slice(0, OMNIBOX_QUERY_MAX_LENGTH);
}
