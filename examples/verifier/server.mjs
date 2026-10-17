// SPDX-License-Identifier: Apache-2.0
// Local read-only verifier service. No wallet, secret, signing or mutation API.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { makeClient, readTask } from "./lib/task-reader.mjs";
import { observeTaskCreation } from "./lib/task-creation.mjs";

const STATIC = new Map([
  ["/", ["./public/index.html", "text/html; charset=utf-8"]],
  ["/demo", ["./public/index.html", "text/html; charset=utf-8"]],
  ["/demo-guide.mjs", ["./public/demo-guide.mjs", "text/javascript; charset=utf-8"]],
  ["/text-stats.mjs", ["./public/text-stats.mjs", "text/javascript; charset=utf-8"]],
  ["/app.mjs", ["./public/app.mjs", "text/javascript; charset=utf-8"]],
  ["/locale.mjs", ["./public/locale.mjs", "text/javascript; charset=utf-8"]],
  ["/locale-copy.mjs", ["./public/locale-copy.mjs", "text/javascript; charset=utf-8"]],
  ["/demo-scenarios.mjs", ["./public/demo-scenarios.mjs", "text/javascript; charset=utf-8"]],
  ["/summary.mjs", ["./public/summary.mjs", "text/javascript; charset=utf-8"]],
  ["/style.css", ["./public/style.css", "text/css; charset=utf-8"]],
  ["/evidence-browser.mjs", ["./public/evidence-browser.mjs", "text/javascript; charset=utf-8"]],
  ["/api/showcase", ["../../phase0/demo-showcase.json", "application/json; charset=utf-8"]],
]);
const fileRoot = new URL("./", import.meta.url);
// Expo Web development uses 8081; allow only explicit loopback dev origins
// to read task-only data. This does not expose receipts, wallets or mutations.
const POCKET_WEB_ORIGINS = new Set([
  "http://localhost:8081", "http://127.0.0.1:8081",
  "http://localhost:19006", "http://127.0.0.1:19006",
]);
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
export function createVerifierHandler({ lookup, observeCreation } = {}) {
  const getTask = lookup || ((contract, id) => readTask(makeClient(process.env.MONAD_RPC_URL), contract, id));
  const getCreation = observeCreation || ((contract, id, hash) => observeTaskCreation(makeClient(process.env.MONAD_RPC_URL), contract, id, hash));
  return async (req, res) => {
    try {
      if (req.method !== "GET") return send(res, 405, JSON.stringify({ error: "read-only endpoint" }));
      const url = new URL(req.url, "http://127.0.0.1");
      if (url.pathname === "/api/healthz") {
        return send(res, 200, JSON.stringify({ ok: true, chainId: 10143, mode: "read-only" }));
      }
      if (url.pathname === "/api/task-creation") {
        const contract = url.searchParams.get("contract"), taskId = url.searchParams.get("taskId"), hash = url.searchParams.get("transactionHash");
        if (url.searchParams.size !== 3 || !/^0x[0-9a-fA-F]{40}$/.test(contract || "")
          || !/^0x[0-9a-fA-F]{64}$/.test(taskId || "") || !/^0x[0-9a-fA-F]{64}$/.test(hash || "")) {
          return send(res, 400, JSON.stringify({ error: "Valid contract, taskId and transactionHash required" }));
        }
        try {
          const result = await getCreation(contract, taskId, hash);
          return send(res, result.state === "PENDING" ? 202 : 200, JSON.stringify(result));
        } catch (error) {
          const codes = new Set(["WRONG_CHAIN", "CREATION_REVERTED", "RECEIPT_MISMATCH", "TRANSACTION_MISMATCH", "NOT_TASK_CREATION", "TASK_ID_MISMATCH", "CREATION_EVENT_MISMATCH", "TASK_READBACK_MISMATCH", "CREATION_BLOCK_CHANGED"]);
          const code = codes.has(error.code) ? error.code : "RPC_UNAVAILABLE";
          return send(res, code === "RPC_UNAVAILABLE" ? 502 : 422, JSON.stringify({ error: code }));
        }
      }
      if (url.pathname === "/api/task") {
        const origin = req.headers.origin;
        if (origin && POCKET_WEB_ORIGINS.has(origin)) {
          res.setHeader("access-control-allow-origin", origin);
          res.setHeader("vary", "Origin");
        }
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
  };
}
export function createVerifierServer(options = {}) {
  return createServer(createVerifierHandler(options));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT || 4321);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("PORT must be 1..65535");
  const server = createVerifierServer();
  server.listen(port, "127.0.0.1", () => console.log("AgentLedger local verifier: http://127.0.0.1:" + port));
}
