// SPDX-License-Identifier: Apache-2.0
// Local read-only verifier service. No wallet, secret, signing or mutation API.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { makeClient, readTask } from "./lib/task-reader.mjs";

const STATIC = new Map([
  ["/", ["./public/index.html", "text/html; charset=utf-8"]],
  ["/app.mjs", ["./public/app.mjs", "text/javascript; charset=utf-8"]],
  ["/demo-scenarios.mjs", ["./public/demo-scenarios.mjs", "text/javascript; charset=utf-8"]],
  ["/summary.mjs", ["./public/summary.mjs", "text/javascript; charset=utf-8"]],
  ["/style.css", ["./public/style.css", "text/css; charset=utf-8"]],
  ["/evidence-browser.mjs", ["./public/evidence-browser.mjs", "text/javascript; charset=utf-8"]],
]);
const fileRoot = new URL("./", import.meta.url);
function send(res, status, body, type = "application/json; charset=utf-8") {
  res.writeHead(status, {
    "content-type": type, "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self';",
  });
  res.end(body);
}
export function createVerifierServer({ lookup } = {}) {
  const getTask = lookup || ((contract, id) => readTask(makeClient(process.env.MONAD_RPC_URL), contract, id));
  return createServer(async (req, res) => {
    try {
      // Optional exact-origin browser development integration for Expo Web.
      // Never wildcard CORS. GET-only semantics and no-write policy remain.
      const requested = req.headers.origin;
      const configured = process.env.AGENTLEDGER_CORS_ORIGIN;
      if (configured && requested === configured) {
        let allowed = false;
        try {
          const parsed = new URL(configured);
          const local = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
          allowed = parsed.origin === configured && !parsed.username && !parsed.password &&
            (parsed.protocol === "https:" || (parsed.protocol === "http:" && local));
        } catch { /* invalid development origin never enables CORS */ }
        if (allowed) {
          res.setHeader("access-control-allow-origin", configured);
          res.setHeader("vary", "Origin");
        }
      }
      if (req.method !== "GET") return send(res, 405, JSON.stringify({ error: "read-only endpoint" }));
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname === "/api/healthz") {
        return send(res, 200, JSON.stringify({ ok: true, chainId: 10143, mode: "read-only" }));
      }
      if (url.pathname === "/api/task") {
        if (url.searchParams.size !== 2 || !url.searchParams.has("contract") || !url.searchParams.has("taskId")) {
          return send(res, 400, JSON.stringify({ error: "contract and taskId are required" }));
        }
        const contract = url.searchParams.get("contract");
        const taskId = url.searchParams.get("taskId");
        if (!/^0x[0-9a-fA-F]{40}$/.test(contract) || !/^0x[0-9a-fA-F]{64}$/.test(taskId)) {
          return send(res, 400, JSON.stringify({ error: "Invalid contract address or taskId" }));
        }
        try {
          const result = await getTask(contract, taskId);
          return send(res, 200, JSON.stringify(result));
        } catch (error) {
          // Don't reflect arbitrary RPC errors or potentially sensitive URLs.
          console.error("Chain lookup failed:", error.message);
          return send(res, 502, JSON.stringify({ error: "Chain lookup failed: confirm network, contract and task ID" }));
        }
      }
      const entry = STATIC.get(url.pathname);
      if (!entry) return send(res, 404, JSON.stringify({ error: "Not found" }));
      const [path, type] = entry;
      const content = await readFile(new URL(path, fileRoot));
      return send(res, 200, content, type);
    } catch (error) {
      console.error("Verifier error:", error.message);
      return send(res, 500, JSON.stringify({ error: "Internal error" }));
    }
  });
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT || 4321);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("PORT must be 1..65535");
  const server = createVerifierServer();
  server.listen(port, "127.0.0.1", () => console.log("AgentLedger local verifier: http://127.0.0.1:" + port));
}
