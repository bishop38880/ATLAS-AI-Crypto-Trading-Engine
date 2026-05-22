/**
 * Patterns that must not appear anywhere under frontend/src outside exempt paths.
 */
import { execSync } from "node:child_process";
import { dirname, join } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const FRONTEND_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

const BANNED: { readonly pattern: string; readonly reason: string }[] = [
  { pattern: "'ws://", reason: "Use wsUrl() from src/lib/url.ts" },
  { pattern: '"ws://', reason: "Use wsUrl() from src/lib/url.ts" },
  { pattern: "'wss://", reason: "Use wsUrl() from src/lib/url.ts" },
  { pattern: '"wss://', reason: "Use wsUrl() from src/lib/url.ts" },
  { pattern: "localhost:", reason: "Resolve via VITE_API_BASE_URL / VITE_WS_BASE_URL" },
  { pattern: "NEXT_PUBLIC_", reason: "This is Vite. Use VITE_ prefix." },
  { pattern: "coinglass", reason: "CoinGlass removed. Use HYDRA + Coinalyze." },
  { pattern: "coinank", reason: "Disallowed stale source naming in POLARIS UI." },
];

const BANNED_REGEX: { readonly pattern: string; readonly reason: string }[] = [
  {
    pattern: String.raw`\blocalhost\b`,
    reason: "Resolve via VITE_* env bases — never spell the loopback host in source",
  },
];

const EXEMPT_FILES = ["src/lib/url.ts"];

function is_exempt_line(line: string): boolean {
  const path_part = line.split(":")[0] ?? "";
  const normalized = path_part.replaceAll("\\", "/");
  return EXEMPT_FILES.some((ex) => normalized === ex || normalized.endsWith(`/${ex}`));
}

let failed = false;

for (const { pattern, reason } of BANNED) {
  try {
    const grep_flags = pattern === "coinglass" || pattern === "coinank" ? "rniE" : "rnE";
    const out = execSync(
      `grep -${grep_flags} --include='*.ts' --include='*.tsx' ${JSON.stringify(pattern)} src/ || true`,
      { encoding: "utf8", cwd: FRONTEND_ROOT },
    );
    const hits = out
      .split("\n")
      .map((line) => line.trimEnd())
      .filter(Boolean)
      .filter((line) => !is_exempt_line(line));
    if (hits.length > 0) {
      failed = true;
      process.stderr.write(`\n✗ Banned pattern "${pattern}" found (${reason}):\n`);
      for (const hit of hits) {
        process.stderr.write(`  ${hit}\n`);
      }
    }
  } catch {
    // grep returns 1 when no matches; fine
  }
}

for (const { pattern, reason } of BANNED_REGEX) {
  try {
    const out = execSync(
      `grep -rnE --include='*.ts' --include='*.tsx' ${JSON.stringify(pattern)} src/ || true`,
      { encoding: "utf8", cwd: FRONTEND_ROOT },
    );
    const hits = out
      .split("\n")
      .map((line) => line.trimEnd())
      .filter(Boolean)
      .filter((line) => !is_exempt_line(line));
    if (hits.length > 0) {
      failed = true;
      process.stderr.write(`\n✗ Banned regex "${pattern}" found (${reason}):\n`);
      for (const hit of hits) {
        process.stderr.write(`  ${hit}\n`);
      }
    }
  } catch {
    // ignore
  }
}

if (failed) {
  process.exit(1);
}

process.stdout.write("✓ No hardcoded URLs found\n");
