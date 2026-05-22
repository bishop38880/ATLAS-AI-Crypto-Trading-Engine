/**
 * scripts/verify-logo-urls.ts
 *
 * Checks every URL in ASSET_LOGOS returns HTTP 200.
 * Run: npx tsx scripts/verify-logo-urls.ts
 *
 * Output:
 *   ✓ BTC
 *   ✗ SOMETOKEN — 404 https://coin-images.coingecko.com/...
 *   ...
 *   Summary: 91/93 OK, 2 failed
 *
 * Exit code 1 if any URL fails — use in CI with:
 *   npx tsx scripts/verify-logo-urls.ts || exit 1
 */

import { ASSET_LOGOS } from "../src/lib/asset-logos";

const CONCURRENCY = 3; // CoinGecko CDN rate-limits aggressive parallel checks

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function checkUrl(symbol: string, url: string): Promise<boolean> {
  const headers = {
    Accept: "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    Referer: "https://www.coingecko.com/",
    "User-Agent":
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 POLARIS-fe-logo-verify/1.0",
  };

  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, {
        method: "GET",
        redirect: "follow",
        headers,
      });

      if (res.ok) {
        void res.arrayBuffer().catch(() => undefined);
        console.log(`  ✓ ${symbol.padEnd(12)} ${res.status}`);
        return true;
      }

      const retryable = res.status === 429 || res.status >= 500;
      if (retryable && attempt < 2) {
        await sleep(500 * (attempt + 1));
        continue;
      }

      console.error(`  ✗ ${symbol.padEnd(12)} ${res.status}  ${url}`);
      return false;
    } catch (err) {
      if (attempt < 2) {
        await sleep(500 * (attempt + 1));
        continue;
      }
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`  ✗ ${symbol.padEnd(12)} ERROR  ${msg}`);
      return false;
    }
  }

  return false;
}

async function runInBatches<T>(items: T[], concurrency: number, fn: (item: T) => Promise<boolean>): Promise<boolean[]> {
  const results: boolean[] = [];
  for (let i = 0; i < items.length; i += concurrency) {
    const batch = items.slice(i, i + concurrency);
    const batchResults = await Promise.all(batch.map(fn));
    results.push(...batchResults);
  }
  return results;
}

async function main(): Promise<void> {
  const entries = Object.entries(ASSET_LOGOS).filter(([, url]) => url !== "");
  console.log(`Checking ${entries.length} logo URLs…\n`);

  const results = await runInBatches(entries, CONCURRENCY, ([symbol, url]) => checkUrl(symbol, url));

  const passed = results.filter(Boolean).length;
  const failed = results.length - passed;

  console.log(`\nSummary: ${passed}/${results.length} OK, ${failed} failed`);

  if (failed > 0) {
    console.error("\n⚠  Fix the failed URLs in src/lib/asset-logos.ts");
    console.error("   Find correct IDs at: https://www.coingecko.com/en/coins/{symbol}");
    process.exit(1);
  }

  console.log("\n✓ All logo URLs verified");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
