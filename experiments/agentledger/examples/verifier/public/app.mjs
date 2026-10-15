// SPDX-License-Identifier: Apache-2.0
import { verifyEvidence } from "./evidence-browser.mjs";

const byId = id => document.getElementById(id);
const form = byId("lookup-form");
const queryButton = byId("query-button");
const verifyButton = byId("verify-button");
const queryMessage = byId("query-message");
const verificationMessage = byId("verification-message");
let loadedTask = null;

function showMessage(element, message, tone = "") {
  element.textContent = message;
  element.className = "message" + (tone ? " " + tone : "");
}
function clearTask() {
  loadedTask = null;
  byId("task-result").hidden = true;
  verifyButton.disabled = true;
  showMessage(verificationMessage, "Query a Proven task first.");
}
function showTask(task, contract) {
  const fields = {
    "task-status": task.status,
    "task-value": task.taskId,
    "task-creator": task.creator,
    "task-payer": task.payer,
    "task-amount": task.amountAtomic,
    "task-budget": task.maxBudgetAtomic,
    "task-hash": task.evidenceHash,
    "task-settlement": task.settlementRef,
    "task-times": [task.createdAt, task.paidAt, task.provenAt].join(" / "),
  };
  for (const [id, value] of Object.entries(fields)) byId(id).textContent = value;
  byId("contract-link").href = "https://testnet.monadscan.com/address/" + encodeURIComponent(contract);
  byId("task-result").hidden = false;
  verifyButton.disabled = task.status !== "Proven";
  showMessage(verificationMessage,
    task.status === "Proven" ? "Select a local Evidence JSON and click Verify." :
    "On-chain task status is " + task.status + "; evidence cannot be verified as Proven yet.");
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  clearTask();
  const contract = byId("contract").value.trim();
  const taskId = byId("taskId").value.trim();
  if (!/^0x[0-9a-fA-F]{40}$/.test(contract) || !/^0x[0-9a-fA-F]{64}$/.test(taskId)) {
    showMessage(queryMessage, "Invalid contract address or task ID.", "error");
    return;
  }
  queryButton.disabled = true;
  showMessage(queryMessage, "Reading Monad Testnet …");
  try {
    const params = new URLSearchParams({ contract, taskId });
    const response = await fetch("/api/task?" + params, { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || "RPC query failed");
    if (result.chainId !== 10143 || result.taskId !== taskId.toLowerCase()) throw Error("Unexpected chain or task returned");
    loadedTask = result;
    showTask(result, contract);
    showMessage(queryMessage, "Task loaded from Monad Testnet.", "success");
  } catch (error) {
    clearTask();
    showMessage(queryMessage, error.message, "error");
  } finally {
    queryButton.disabled = false;
  }
});

const fileBytes = async (id, maxBytes) => {
  const file = byId(id).files?.[0];
  if (!file) return null;
  if (file.size > maxBytes) throw Error("File too large: " + file.name);
  return file.arrayBuffer();
};

verifyButton.addEventListener("click", async () => {
  if (!loadedTask || loadedTask.status !== "Proven") return;
  verifyButton.disabled = true;
  showMessage(verificationMessage, "Computing SHA-256 in your browser …");
  try {
    const evidence = await fileBytes("evidence-file", 2 * 1024 * 1024);
    if (!evidence) throw Error("Select an Evidence JSON file");
    const document = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(evidence));
    const input = await fileBytes("input-file", 10 * 1024 * 1024);
    const output = await fileBytes("output-file", 10 * 1024 * 1024);
    const result = await verifyEvidence(document, loadedTask, input, output);
    showMessage(verificationMessage,
      "MATCH: " + result.digest + " — Evidence digest matches on-chain data." +
      (result.inputChecked ? " Input file verified." : "") +
      (result.outputChecked ? " Output file verified." : "") +
      " This does not prove execution correctness.", "success");
  } catch (error) {
    showMessage(verificationMessage, "NOT VERIFIED: " + error.message, "error");
  } finally {
    verifyButton.disabled = !loadedTask || loadedTask.status !== "Proven";
  }
});

// Never retain a stale Proven status when the user edits the lookup identity.
for (const id of ["contract", "taskId"]) byId(id).addEventListener("input", clearTask);
