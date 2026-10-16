// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {createVerifierServer} from "../server.mjs";

test("Expo development origin is opt-in, exact, GET-only, never wildcard",async()=>{
  const previous=process.env.AGENTLEDGER_CORS_ORIGIN;
  process.env.AGENTLEDGER_CORS_ORIGIN="http://localhost:8081";
  const server=createVerifierServer({lookup:async()=>{throw Error("not reached")}});
  await new Promise(resolve=>server.listen(0,"127.0.0.1",resolve));
  const base="http://127.0.0.1:"+server.address().port;
  try{
    const trusted=await fetch(base+"/api/healthz",{headers:{Origin:"http://localhost:8081"}});
    assert.equal(trusted.status,200);
    assert.equal(trusted.headers.get("access-control-allow-origin"),"http://localhost:8081");
    const other=await fetch(base+"/api/healthz",{headers:{Origin:"https://malicious.example"}});
    assert.equal(other.headers.get("access-control-allow-origin"),null);
    const prefix=await fetch(base+"/api/healthz",{headers:{Origin:"http://localhost:8081.evil.example"}});
    assert.equal(prefix.headers.get("access-control-allow-origin"),null);
    const write=await fetch(base+"/api/task",{method:"POST",headers:{Origin:"http://localhost:8081"}});
    assert.equal(write.status,405);
  }finally{
    if(previous===undefined)delete process.env.AGENTLEDGER_CORS_ORIGIN;
    else process.env.AGENTLEDGER_CORS_ORIGIN=previous;
    await new Promise(resolve=>server.close(resolve));
  }
});
