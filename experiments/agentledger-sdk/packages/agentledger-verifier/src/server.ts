// SPDX-License-Identifier: Apache-2.0
// Local read-only verifier service. No wallet, secret, signing or mutation API.
import { createServer, type RequestListener, type ServerResponse, type IncomingMessage } from "node:http";
import { pathToFileURL } from "node:url";
import { makeClient, readTask } from "./lib/task-reader.js";

// The former static verifier page is retired. The SDK compatibility
// server remains a GET-only API for existing CLI consumers. Keep the
// supported product UI in experiments/agentledger/apps/pocket and the
// security acceptance UI in experiments/agentledger/examples/verifier.

function send(
  res: ServerResponse,
  status: number,
  body: string,
  type = "application/json; charset=utf-8",
): void {
  res.writeHead(status, {
    "content-type": type,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self';",
  });
  res.end(body);
}

interface LookupFn {
  (contract: string, id: string): Promise<ReturnType<typeof readTask>>;
}

function createVerifierServer(opts: { lookup?: LookupFn } = {}) {
  const getTask = opts.lookup || ((contract: string, id: string) =>
    readTask(makeClient(process.env.MONAD_RPC_URL), contract, id));
  const listener: RequestListener = async (req: IncomingMessage, res: ServerResponse) => {
    try {
      if (req.method !== "GET") {
        return send(res, 405, JSON.stringify({ error: "read-only endpoint" }));
      }
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/api/healthz") {
        return send(res, 200, JSON.stringify({ ok: true, chainId: 10143, mode: "read-only" }));
      }
      if (url.pathname === "/api/task") {
        if (url.searchParams.size !== 2 ||
            !url.searchParams.has("contract") || !url.searchParams.has("taskId")) {
          return send(res, 400, JSON.stringify({ error: "contract and taskId are required" }));
        }
        const contract = url.searchParams.get("contract") ?? "";
        const taskId = url.searchParams.get("taskId") ?? "";
        if (!/^0x[0-9a-fA-F]{40}$/.test(contract) ||
            !/^0x[0-9a-fA-F]{64}$/.test(taskId)) {
          return send(res, 400, JSON.stringify({ error: "Invalid contract address or task ID" }));
        }
        try {
          const result = await getTask(contract, taskId);
          return send(res, 200, JSON.stringify(result));
        } catch (error) {
          console.error("Chain lookup failed:", (error as Error).message);
          return send(res, 502, JSON.stringify({ error: "Chain lookup failed: confirm network, contract and task ID" }));
        }
      }
      if (url.pathname === "/") {
        return send(res, 410, JSON.stringify({
          code: "LEGACY_VERIFIER_UI_RETIRED",
          message: "The duplicate SDK verifier page is no longer served. The GET /api/task and /api/healthz compatibility endpoints remain available.",
          pocket: "experiments/agentledger/apps/pocket",
          verificationStudio: "experiments/agentledger/examples/verifier",
          paymentStatus: "PaymentUnknown",
          publicTestnetAcceptance: "BLOCKED_NO_REVIEWED_DEPLOYMENT"
        }));
      }
      return send(res, 404, JSON.stringify({ error: "Not found" }));
    } catch (error) {
      console.error("Verifier error:", (error as Error).message);
      return send(res, 500, JSON.stringify({ error: "Internal error" }));
    }
  };
  return createServer(listener);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number(process.env.PORT || 4321);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("PORT must be 1..65535");
  }
  const server = createVerifierServer();
  server.listen(port, "127.0.0.1", () =>
    console.log("AgentLedger local verifier: http://127.0.0.1:" + port));
}

export { createVerifierServer };
