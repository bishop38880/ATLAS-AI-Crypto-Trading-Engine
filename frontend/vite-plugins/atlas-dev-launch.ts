import { spawn } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import type { Plugin } from "vite";

const LAUNCH_PATH = "/__atlas_dev__/launch-full-stack";

/**
 * Dev-only: POST spawns `scripts/start-atlas-and-dashboard.sh` (Docker + API + Vite tabs + browser).
 * Gated by process.env.ATLAS_VITE_LAUNCH_STACK === "1" and loopback clients only.
 */
export function atlas_dev_launch_stack_plugin(): Plugin {
  const atlas_repo_root = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");

  return {
    name: "atlas-dev-launch-stack",
    apply: "serve",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const raw_url = req.url ?? "";
        const path_only = raw_url.split("?", 1)[0] ?? raw_url;
        if (req.method !== "POST" || path_only !== LAUNCH_PATH) {
          next();
          return;
        }

        if (process.env.ATLAS_VITE_LAUNCH_STACK !== "1") {
          res.statusCode = 403;
          res.setHeader("Content-Type", "application/json");
          res.end(
            JSON.stringify({
              ok: false,
              error: "Set ATLAS_VITE_LAUNCH_STACK=1 when starting the Vite dev server.",
            }),
          );
          return;
        }

        const remote = req.socket.remoteAddress ?? "";
        const loopback_ok =
          remote === "127.0.0.1" || remote === "::1" || remote === "::ffff:127.0.0.1";
        if (!loopback_ok) {
          res.statusCode = 403;
          res.setHeader("Content-Type", "application/json");
          res.end(JSON.stringify({ ok: false, error: "local_only" }));
          return;
        }

        const script = resolve(atlas_repo_root, "scripts", "start-atlas-and-dashboard.sh");
        const child = spawn("bash", [script], {
          cwd: atlas_repo_root,
          detached: true,
          stdio: "ignore",
          env: {
            ...process.env,
            ATLAS_SKIP_FRONTEND_TAB: "1",
          },
        });
        child.unref();

        res.statusCode = 200;
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify({ ok: true, status: "spawned" }));
      });
    },
  };
}
