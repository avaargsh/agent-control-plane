// SPDX-License-Identifier: Apache-2.0
import test from "node:test";
import assert from "node:assert/strict";
import {nextJourneyAction,journeyPosition,GUIDED_LABELS,JOURNEY_ACTIONS} from "./demo-flow";
test("guided demo is a deliberate and finite task→recorder claim→evidence→verify→audit→unknown journey",()=>{
 const path=Array.from({length:7},(_,i)=>nextJourneyAction(i as 0|1|2|3|4|5|6));
 assert.deepEqual(path.map(x=>x.action),["start","show-payment","show-evidence","verify","show-summary","show-unknown","restart"]);
 assert.deepEqual(path.map(x=>x.next),[1,2,3,4,5,6,1]);
 assert.equal(GUIDED_LABELS.length,7);
 assert.equal(JOURNEY_ACTIONS.length,7);
 for(let i=0;i<=6;i++)assert.equal(journeyPosition(i as 0|1|2|3|4|5|6).total,4);
});
test("payment is never a success claim on the guided journey",()=>{
 assert.match(journeyPosition(2).title,/RECORDER CLAIM/);
 assert.match(journeyPosition(6).title,/LOST ACK/);
 assert.equal(journeyPosition(3).current,3);
 assert.equal(journeyPosition(4).current,4);
});
