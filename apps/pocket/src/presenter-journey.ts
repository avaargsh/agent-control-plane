// SPDX-License-Identifier: Apache-2.0
// Deterministic, entirely local presenter flow. No wallet, transport,
// execution or payment action can be triggered by stage navigation.
import type {Stage} from "./facts";
export const PRESENTER_STAGES=["created","paid","evidence","unknown"] as const satisfies readonly Stage[];
export const PRESENTER_LABELS={
 created:{en:"01 · Created",zh:"01 · 创建"},
 paid:{en:"02 · Recorder Paid claim",zh:"02 · 付款声明"},
 evidence:{en:"03 · Evidence / Verify",zh:"03 · 证据核验"},
 unknown:{en:"04 · Lost ACK / No recharge",zh:"04 · 丢 ACK / 禁止重扣"},
} as const;
export function presenterIndex(stage:Stage):number{return PRESENTER_STAGES.indexOf(stage);}
export function presenterNext(stage:Stage):Stage{
 const index=presenterIndex(stage);
 return PRESENTER_STAGES[Math.min(index+1,PRESENTER_STAGES.length-1)];
}
export function presenterPrevious(stage:Stage):Stage{
 const index=presenterIndex(stage);
 return PRESENTER_STAGES[Math.max(index-1,0)];
}
export function presenterSafety(stage:Stage){
 return Object.freeze({
   mode:"LOCAL_SIMULATION",stage,publicAcceptance:false,
   paymentStatus:"PaymentUnknown",chargeAgain:false,
   walletAction:false,executionAuthority:"ACP",
 });
}
