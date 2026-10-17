// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {PRESENTER_STAGES,presenterIndex,presenterNext,presenterPrevious,presenterSafety} from "./presenter-journey";
test("3-minute presenter follows deterministic Created → claim → Evidence → Lost ACK",()=>{
 assert.deepEqual([...PRESENTER_STAGES],["created","paid","evidence","unknown"]);
 assert.equal(presenterNext("created"),"paid");
 assert.equal(presenterNext("paid"),"evidence");
 assert.equal(presenterNext("evidence"),"unknown");
 assert.equal(presenterNext("unknown"),"unknown");
 assert.equal(presenterPrevious("created"),"created");
 assert.equal(presenterPrevious("evidence"),"paid");
 assert.equal(presenterIndex("unknown"),3);
});
test("no guided step authorizes wallet, recharge, agent execution or Testnet acceptance",()=>{
 for(const stage of PRESENTER_STAGES){
  const x=presenterSafety(stage);
  assert.equal(Object.isFrozen(x),true);
  assert.equal(x.paymentStatus,"PaymentUnknown");
  assert.equal(x.chargeAgain,false);
  assert.equal(x.walletAction,false);
  assert.equal(x.publicAcceptance,false);
  assert.equal(x.executionAuthority,"ACP");
 }
});
