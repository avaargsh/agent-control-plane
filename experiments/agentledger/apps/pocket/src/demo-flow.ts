// SPDX-License-Identifier: Apache-2.0
// Pure product journey contract. "Paid" is a RECORDER CLAIM only, never
// independent payment proof. Guides the operator without chain writes.
export type JourneyStep=0|1|2|3|4|5|6;
export type JourneyAction="start"|"show-payment"|"show-evidence"|"verify"|"show-summary"|"show-unknown"|"restart";
export const GUIDED_LABELS=Object.freeze([
  "Start guided demo","Observe Recorder Paid","Inspect Evidence",
  "Verify SHA-256 locally","Open audit summary","Show lost ACK","Restart demo",
] as const);
export const GUIDED_LABELS_ZH=Object.freeze([
  "开始引导演示","查看 Recorder Paid 声明","查看证据锚点",
  "本地验证 SHA-256","查看审计摘要","演示丢失 ACK","重新演示",
] as const);
export const JOURNEY_ACTIONS=Object.freeze([
 "start","show-payment","show-evidence","verify","show-summary",
 "show-unknown","restart",
] as const);
export function nextJourneyAction(step:JourneyStep):{action:JourneyAction;next:JourneyStep}{
  const action=JOURNEY_ACTIONS[step];
  return {action,next:step===6?1:(step+1) as JourneyStep};
}
export function journeyPosition(step:JourneyStep):{current:number;total:number;title:string}{
  const titles=[
    "READY / NOT STARTED","TASK / CREATED","SETTLEMENT / RECORDER CLAIM",
    "EVIDENCE / ANCHOR","VERIFY / SHA-256 MATCH",
    "AUDIT / SUMMARY","SAFETY / LOST ACK",
  ];
  return {current:step===0?0:Math.min(step,4),total:4,title:titles[step]};
}
