// SPDX-License-Identifier: Apache-2.0
// GET-only transport to the existing Phase0 Verification Studio API.
// No wallet, signer, payment facilitator, mutation or raw RPC injection.
import { ADDRESS, HEX32, assertTask } from "./domain.mjs";
export function serviceBase(raw) {
  if(typeof raw!=="string" || !raw.trim())throw Error("VERIFIER_URL_REQUIRED");
  const url=new URL(raw);
  const loopback=["localhost","127.0.0.1","::1","[::1]"].includes(url.hostname);
  if(!(url.protocol==="https:" || (url.protocol==="http:"&&loopback)) ||
     url.username || url.password || url.search || url.hash)throw Error("UNTRUSTED_VERIFIER_URL");
  return url.origin;
}
export async function observeTask(baseUrl,contract,taskId,fetcher=fetch) {
  const base=serviceBase(baseUrl);
  if(!ADDRESS.test(contract||"") || !HEX32.test(taskId||""))throw Error("INVALID_TASK_LOOKUP");
  const path="/api/task?"+new URLSearchParams({contract,taskId}).toString();
  const response=await fetcher(base+path,{method:"GET",cache:"no-store",
    headers:{accept:"application/json"}});
  if(!response.ok)throw Error("CHAIN_READ_UNAVAILABLE");
  const value=await response.json();
  return assertTask(value,{contract,taskId});
}
