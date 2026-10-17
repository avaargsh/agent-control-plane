import React, {useRef,useState} from "react";
import {utf8ToBytes} from "@noble/hashes/utils.js";
import {DEMO_INPUT,DEMO_OUTPUT,makeDemoEvidence,verifyEvidenceBytes} from "./src/evidence";
import {pickLocalDocument} from "./src/local-files";
import {DocumentKind,PickedLocalFile,parseEvidenceBytes,readPickedDocument} from "./src/file-contract";
import {TaskRead,observeTaskReadOnly,taskReadSummary} from "./src/observe";
import {saveSummaryJson} from "./src/export-summary";
import {PRESENTER_LABELS,PRESENTER_STAGES,presenterIndex,presenterNext,presenterPrevious} from "./src/presenter-journey";
import {Platform, SafeAreaView, ScrollView, Pressable, StyleSheet, Text, TextInput, View, useWindowDimensions} from "react-native";
import {StatusBar} from "expo-status-bar";
import {DemoFact, FACTS, Stage, shorten, exportSummary} from "./src/facts";
import {pocketCopy} from "./src/ui-locale";

// Locale-aware copy: Linux CI's English Chromium lacks CJK fonts, while
// zh-CN browsers and native Chinese devices retain Chinese product labels.
let ZH=(()=>{
 try{return Intl.DateTimeFormat().resolvedOptions().locale.toLowerCase().startsWith("zh");}
 catch{return false;}
})();
const t=(en:string,zh:string)=>ZH?zh:en;
const l=(value:string)=>pocketCopy(value,ZH);
const LANGUAGE_KEY="agentledger.ui-language.v1";
function initialLanguage():"en"|"zh"{
 if(Platform.OS==="web")try{
  const query=new URLSearchParams(window.location.search).get("lang");
  if(query==="en"||query==="zh"){window.localStorage.setItem(LANGUAGE_KEY,query);return query;}
  const saved=window.localStorage.getItem(LANGUAGE_KEY);
  if(saved==="en"||saved==="zh")return saved;
 }catch{}
 return ZH?"zh":"en";
}
type Tab="tasks"|"payment"|"evidence"|"journal";
const C={bg:"#F5F8FC",panel:"#FFFFFF",card:"#EFF1FD",line:"#E5EAF3",ink:"#18243C",sub:"#667A91",purple:"#315FC6",mint:"#138D78",amber:"#A86327",red:"#CA475B",night:"#15375F",lavender:"#EDEBFF"};
const getLabels=():Record<Tab,string>=>({tasks:t("Tasks","任务"),payment:t("Payment","支付"),evidence:t("Evidence","证据"),journal:t("Journal","记录")});
const tabs:Tab[]=["tasks","payment","evidence","journal"];
const getSteps=():{key:Stage;label:string;note:string}[]=>[
 {key:"created",label:t("Created","已创建"),note:t("Task registered","任务已创建")},
 {key:"paid",label:t("Paid claim","支付声明"),note:t("Recorder claim · USDC not verified","Recorder 声明，不等于 USDC 已核验")},
 {key:"evidence",label:t("Evidence","证据"),note:t("Simulated Evidence awaiting hash check","模拟证据等待验证")},
 {key:"unknown",label:t("Lost ACK","响应丢失"),note:t("PaymentUnknown · never recharge","PaymentUnknown / 禁止重扣")},
];
function Chip({text,tone="purple"}:{text:string;tone?:"purple"|"amber"|"mint"}){
 return <View style={[s.chip,{backgroundColor:tone==="amber"?"#FFF1DE":tone==="mint"?"#E2F8EF":"#EBE9FF"}]}><Text style={[s.chipText,{color:tone==="amber"?C.amber:tone==="mint"?C.mint:C.purple}]}>{text}</Text></View>;
}
function Mark({size=32}:{size?:number}){
 return <View style={[s.mark,{width:size,height:size,borderRadius:Math.round(size*.3)}]}><Text style={[s.markText,{fontSize:Math.round(size*.46)}]}>✦</Text></View>;
}
function Hero({state,live}:{state:string;live:boolean}){
 return <View style={s.hero}>
    <View pointerEvents="none" style={s.heroOrbitOne}/>
    <View pointerEvents="none" style={s.heroOrbitTwo}/>
    <View style={s.heroTop}><Text style={s.heroLabel}>{l("AGENT PROOF / 01")}</Text><Text style={s.heroStamp}>↗</Text></View>
    <Text style={s.heroTitle}>{t("Your work.\nVerifiable.","每次执行，\n都有据可查。")}</Text>
    <Text style={s.heroBody}>{t("One place for the task, the payment observation and its evidence.","从任务到支付观察、再到证据核验，一目了然。")}</Text>
    <View style={s.heroBottom}><View style={[s.heroStatusDot,{backgroundColor:live?"#90B8FF":"#A7B0CB"}]}/><Text style={s.heroFoot}>{l(live?"TESTNET · TASK OBSERVED":"LOCAL DEMONSTRATION")}</Text><View style={s.heroFootRight}><Text style={s.heroFootRightText}>{state.toUpperCase()}</Text></View></View>
  </View>;
}
function InfoHeader({index,title,sub}:{index:string;title:string;sub?:string}){
 return <View style={s.sectionHeader}><View style={s.sectionIndex}><Text style={s.sectionIndexText}>{index}</Text></View><View style={{flex:1}}><Text style={s.sectionTitle}>{title}</Text>{sub?<Text style={s.sectionSubtitle}>{sub}</Text>:null}</View></View>;
}
function Row({label,value}:{label:string;value:string}){
 return <View style={s.row}><Text style={s.muted}>{label}</Text><Text style={s.value} selectable>{value}</Text></View>;
}
function Button({title,onPress,disabled=false,secondary=false}:{title:string;onPress:()=>void;disabled?:boolean;secondary?:boolean}){
 return <Pressable accessibilityRole="button" disabled={disabled} onPress={onPress}
 style={({pressed})=>[s.button,secondary&&s.buttonSecondary,(pressed||disabled)&&{opacity:.65}]}>
 <Text style={[s.buttonText,secondary&&{color:C.ink}]}>{title}</Text></Pressable>;
}
function Phone({stage,setStage,observed,onObserve,onVerified,onExport,exportNotice,onChangeLanguage}:{
 stage:Stage;setStage:(next:Stage)=>void;observed:TaskRead|null;
 onObserve:(value:TaskRead|null)=>void;onVerified:(value:string)=>void;
 onExport:()=>void;exportNotice:string;onChangeLanguage:()=>void;
}){
 const [tab,setTab]=useState<Tab>("tasks"),[verified,setVerified]=useState(false);
 const [presenting,setPresenting]=useState(false);
 const labels=getLabels(),steps=getSteps();
 const scrollerRef=useRef<ScrollView|null>(null);
 const [contract,setContract]=useState(""),[taskId,setTaskId]=useState("");
 const [lookupState,setLookupState]=useState(""),[lookupBusy,setLookupBusy]=useState(false);
 const [evidenceJson,setEvidenceJson]=useState("");
 const [inputText,setInputText]=useState(""),[outputText,setOutputText]=useState("");
 const [verificationError,setVerificationError]=useState("");
 const lookupEpoch=useRef(0);
 const fileEpoch=useRef(0);
 const webInputs=useRef<Record<DocumentKind,HTMLInputElement|null>>({evidence:null,input:null,output:null});
 const webPickerEpoch=useRef<Record<DocumentKind,number>>({evidence:0,input:0,output:0});
 const [localFiles,setLocalFiles]=useState<Partial<Record<DocumentKind,PickedLocalFile>>>({});
 const [fileDiagnostic,setFileDiagnostic]=useState("");
 const [fileReading,setFileReading]=useState(false);
 function invalidateProof(){
   setVerified(false);onVerified("NOT_VERIFIED");setVerificationError("");
 }
 function clearLocalEvidence(){
   fileEpoch.current++;setLocalFiles({});setFileDiagnostic("");setEvidenceJson("");
   setInputText("");setOutputText("");setFileReading(false);invalidateProof();
 }
 async function acceptSelected(kind:DocumentKind,selected:PickedLocalFile|null,epoch:number){
   if(epoch!==fileEpoch.current||!selected)return;
   if(kind==="evidence"){
     parseEvidenceBytes(selected.bytes);
     setEvidenceJson(""); // picked raw file is now the sole Evidence source
   }
   if(kind==="input")setInputText("");
   if(kind==="output")setOutputText("");
   setLocalFiles(prev=>({...prev,[kind]:selected}));
   setFileReading(false);invalidateProof();
   setFileDiagnostic(kind.toUpperCase()+"_FILE_LOADED_LOCAL_ONLY");
 }
 async function selectFile(kind:DocumentKind){
   // A file selection is a new provenance operation even if the dialog
   // is canceled. Never preserve a previous HASH_MATCHED claim.
   const epoch=++fileEpoch.current;
   invalidateProof();setFileDiagnostic("");
   // A failed/invalid replacement must never silently fall back to the
   // previous successfully verified byte source.
   setLocalFiles(prev=>({...prev,[kind]:undefined}));
   if(Platform.OS==="web"){
     // Bind the eventual file selection to the originating task/scenario.
     webPickerEpoch.current[kind]=epoch;
     // Direct user gesture opens stable DOM input; Expo's Web synthetic
     // click may be interpreted as cancel in headless Chromium.
     webInputs.current[kind]?.click();
     return;
   }
   setFileReading(true);
   try{
     await acceptSelected(kind,await pickLocalDocument(kind),epoch);
   }catch(error){
     if(epoch===fileEpoch.current){
       invalidateProof();
       setFileDiagnostic("FILE_NOT_VERIFIED: "+String((error as Error).message));
     }
   }finally{
     if(epoch===fileEpoch.current)setFileReading(false);
   }
 }
 async function acceptWebInput(kind:DocumentKind,file:File,epoch:number){
   if(epoch!==fileEpoch.current)return; // chooser belongs to a prior task
   invalidateProof();setFileReading(true);setFileDiagnostic("FILE_READING_LOCAL_ONLY");
   setLocalFiles(prev=>({...prev,[kind]:undefined}));
   try{
     const selected=await readPickedDocument(kind,
       async()=>({canceled:false,assets:[{
         name:file.name,size:file.size,uri:"blob:local",
         file:{arrayBuffer:()=>file.arrayBuffer()}
       }]}),
       async()=>new Uint8Array(await file.arrayBuffer()));
     await acceptSelected(kind,selected,epoch);
   }catch(error){
     if(epoch===fileEpoch.current){
       invalidateProof();
       setFileDiagnostic("FILE_NOT_VERIFIED: "+String((error as Error).message));
     }
   }finally{
     if(epoch===fileEpoch.current)setFileReading(false);
   }
 }
 function editText(kind:DocumentKind,value:string){
   // Manually entered text and a picked raw file are mutually exclusive:
   // editing text invalidates the previous file rather than silently
   // hashing a stale byte source.
   ++fileEpoch.current;setFileReading(false);setFileDiagnostic("");
   setLocalFiles(prev=>({...prev,[kind]:undefined}));
   if(kind==="evidence")setEvidenceJson(value);
   else if(kind==="input")setInputText(value);
   else setOutputText(value);
   invalidateProof();
 }
 const fact=FACTS[stage];
 const live=observed!==null;
 const state=observed?.status??fact.state;
 const hash=observed?.evidenceHash??fact.evidenceHash;
 async function lookup(){
  const epoch=++lookupEpoch.current;
  setPresenting(false); // A public query cannot keep a simulated guide.
  setLookupBusy(true);setLookupState("Reading task only; payment remains unknown");
  onObserve(null);clearLocalEvidence();
  try{
   const source=process.env.EXPO_PUBLIC_VERIFIER_URL||"http://127.0.0.1:4321";
   const result=await observeTaskReadOnly(contract.trim(),taskId.trim(),source);
   if(epoch!==lookupEpoch.current)return;
   onObserve(result.task);
   setLookupState("TASK OBSERVED · PaymentUnknown (no USDC verification)");
  }catch(error){if(epoch===lookupEpoch.current)setLookupState("NOT VERIFIED: "+String((error as Error).message));}
  finally{if(epoch===lookupEpoch.current)setLookupBusy(false);}
 }
 function verify(){
  setVerificationError("");setVerified(false);onVerified("NOT_VERIFIED");
  if(fileReading || fileDiagnostic.startsWith("FILE_NOT_VERIFIED")){
    setVerificationError("NOT VERIFIED: unresolved local file error");
    return;
  }
  try{
   const evidence=localFiles.evidence?
     parseEvidenceBytes(localFiles.evidence.bytes):
     live?JSON.parse(evidenceJson):makeDemoEvidence(fact.taskId);
   const task=observed??{taskId:fact.taskId,evidenceHash:hash,status:fact.state};
   // The native and Web paths both execute actual SHA-256 over canonical
   // JSON, not a simulated button flag or an on-chain success assertion.
   const input=localFiles.input?.bytes??
     (live?(inputText?utf8ToBytes(inputText):undefined):utf8ToBytes(DEMO_INPUT));
   const output=localFiles.output?.bytes??
     (live?(outputText?utf8ToBytes(outputText):undefined):utf8ToBytes(DEMO_OUTPUT));
   const result=verifyEvidenceBytes(evidence,task,input,output);
   setVerified(result.match);
   onVerified(result.match?
     (result.inputChecked&&result.outputChecked?"HASH_MATCHED_WITH_INPUT_OUTPUT":"HASH_MATCHED_EVIDENCE_ONLY"):
     "NOT_VERIFIED");
  }catch(error){setVerificationError("NOT VERIFIED: "+String((error as Error).message));}
 }
 const choose=(next:Stage)=>{
  lookupEpoch.current++;
  onObserve(null);setStage(next);clearLocalEvidence();
  setVerificationError("");
  setTab(next==="evidence"?"evidence":next==="unknown"?"payment":"tasks");
 };
 function gotoPresenter(next:Stage){
  choose(next);
  setTab(next==="created"?"tasks":next==="evidence"?"evidence":"payment");
  scrollerRef.current?.scrollTo({y:0,animated:false});
 }
 function startPresenter(){
  setPresenting(true);
  gotoPresenter("created"); // reset local demo only, not wallet journals
 }
 return <SafeAreaView style={s.phone}>
  <StatusBar style="dark"/>
  <View style={s.header}><View style={s.brandGroup}><Mark/><View><Text style={s.brand}>agentledger<Text style={{color:C.purple}}>.</Text></Text><Text style={s.kicker}>{l("PROOF STUDIO · POCKET")}</Text></View></View><Pressable accessibilityRole="button" accessibilityLabel={t("Switch to Chinese","切换为英文")} style={s.languageButton} onPress={onChangeLanguage}><Text style={s.languageButtonText}>{ZH?"EN":"中文"}</Text></Pressable><Chip text={l(live?"TASK READ-ONLY":"SIMULATED")} tone="amber"/></View>
  {presenting&&<View style={s.presenterStrip} accessibilityLabel="Local simulated guided demonstration">
   <View style={s.presenterTop}>
    <Text style={s.presenterEyebrow}>{l("GUIDED DEMO · LOCAL SIMULATION ONLY")}</Text>
    <Pressable accessibilityRole="button" accessibilityLabel="Exit guided demo" onPress={()=>setPresenting(false)} style={s.presenterCloseHit}><Text style={s.presenterClose}>×</Text></Pressable>
   </View>
   <View style={s.presenterStepRow}><Text style={s.presenterStepCount}>{t("STEP","步骤")} {presenterIndex(stage)+1} / {PRESENTER_STAGES.length}</Text><Text style={s.presenterCurrent}>{ZH?PRESENTER_LABELS[stage].zh:PRESENTER_LABELS[stage].en}</Text></View>
   <View style={s.presenterProgress}>{PRESENTER_STAGES.map((item,i)=>
    <View key={item} style={[s.presenterSegment,i<=presenterIndex(stage)&&s.presenterSegmentOn]}/>)}</View>
   <Text style={s.presenterHint}>{stage==="created"?t("Inspect task identity, not a payment.","先看任务身份，创建不代表付款。"):stage==="paid"?t("Recorder Paid is a claim; USDC is not verified.","Recorder Paid 只是声明，USDC 未核验。"):stage==="evidence"?t("Tap Verify SHA-256 below to check the bytes.","在下方点击 Verify SHA-256 验证字节。"):t("Lost ACK ≠ unpaid. Never recharge.","丢失 ACK ≠ 未付款，禁止自动重扣。")}</Text>
   <View style={s.presenterActions}>
    <Pressable accessibilityRole="button" accessibilityLabel="Guide back" disabled={presenterIndex(stage)===0} accessibilityState={{disabled:presenterIndex(stage)===0}} onPress={()=>gotoPresenter(presenterPrevious(stage))} style={s.presenterBackHit}>
      <Text style={[s.presenterAction,presenterIndex(stage)===0&&{opacity:.35}]}>← {t("Back","上一步")}</Text>
    </Pressable>
    {stage==="unknown"?<Pressable accessibilityRole="button" accessibilityLabel="Export guided summary.json" onPress={onExport} style={s.presenterExportHit}>
      <Text style={s.presenterExportText}>{t("Export summary.json","导出 summary.json")} ↓</Text>
    </Pressable>:<Pressable accessibilityRole="button" accessibilityLabel="Guide next" onPress={()=>gotoPresenter(presenterNext(stage))} style={s.presenterNextHit}>
      <Text style={s.presenterExportText}>{t("Next","下一步")} →</Text>
    </Pressable>}
   </View>
   <Text style={s.presenterCaution}>{l("PaymentUnknown · NO RECHARGE · NOT TESTNET PAYMENT ACCEPTANCE")}</Text>
  </View>}
  <ScrollView ref={scrollerRef} style={s.scroller} contentContainerStyle={{padding:20,paddingBottom:42}}>
   <View style={s.headline}><Text style={s.eyebrow}>{l("WORKSPACE")} / {l(tab.toUpperCase())} <Text style={{color:C.mint}}> · {l("READ ONLY")}</Text></Text><Text style={s.h1}>{l(tab==="tasks"?"Your agent tasks":tab==="payment"?"Payment clarity":tab==="evidence"?"Proof, not promises":"Your activity")}</Text>
    <Text style={s.lede}>{live?t("Read-only chain task. USDC, signed intent and deployment review remain unverified.","链上任务状态来自只读查询；USDC 转账、付款授权及部署审查尚未独立核验。"):t("Offline demo. Recorder status is not independent payment proof.","模拟场景无需网络。链上 Recorder 状态不代表独立支付事实。")}</Text></View>
   {tab==="tasks"&&<>
    <Hero state={state} live={live}/>
    {!presenting&&<Button title={t("▶ Start 3-minute guided demo","▶ 开始三分钟演示")} onPress={startPresenter}/>}
    <InfoHeader index="01" title={t("Current task","当前任务")} sub={t("A clear record of what the ledger actually claims.","只展示合约声明，不混淆链上事实。")}/>
    <View style={s.card}><View style={s.inline}><Text style={s.cardTitle}>{l(live?"Observed Task":"Agent Task 023")}</Text><Chip text={state} tone={state==="Proven"?"mint":"purple"}/></View>
     <Text style={s.muted}>{l("A task you can inspect, not a task you can spend on.")}</Text>
     <View style={s.divider}/><Row label={l("Task ID")} value={shorten(observed?.taskId??fact.taskId)}/><Row label={l("Description")} value={shorten(observed?.descriptionHash??fact.descriptionHash)}/><Row label={l("Contract")} value={shorten(observed?.contract??fact.ledger)}/><Row label={l("Network")} value={live?t("Monad Testnet · task read only","Monad 测试网 · 任务只读"):t("Monad Testnet · 10143 (simulated)","Monad 测试网 · 10143（模拟）")}/>
    </View>
    <InfoHeader index="02" title={t("Observe on Testnet","查询测试网任务")} sub={t("Inspect an address without signing or spending gas.","只读查询 · 无需签名或 Gas")}/>
    <View style={s.card}>
     <Text style={s.small}>{t("Local verifier supports GET only. Paid/Proven does not prove USDC settlement. No reviewed deployment: public acceptance blocked.","本地 verifier 代理只支持 GET。任务 Paid/Proven 不是支付证明；无 reviewed deployment 仍禁止公共验收。")}</Text>
     <TextInput accessibilityLabel={l("Ledger contract")} autoCapitalize="none"
      autoCorrect={false} value={contract} onChangeText={value=>{lookupEpoch.current++;setContract(value);onObserve(null);setLookupBusy(false);clearLocalEvidence();}}
      placeholder={l("Contract 0x…")} placeholderTextColor={C.sub} style={s.field}/>
     <TextInput accessibilityLabel="Task ID" autoCapitalize="none"
      autoCorrect={false} value={taskId} onChangeText={value=>{lookupEpoch.current++;setTaskId(value);onObserve(null);setLookupBusy(false);clearLocalEvidence();}}
      placeholder={l("Task ID bytes32 0x…")} placeholderTextColor={C.sub} style={s.field}/>
     <Button title={l(lookupBusy?"Reading…":"Observe Task · no gas")} disabled={lookupBusy} onPress={()=>{void lookup();}}/>
     {!!lookupState&&<Text style={[s.small,{color:lookupState.startsWith("NOT")?C.red:C.mint}]}>{l(lookupState)}</Text>}
    </View>
    <InfoHeader index="03" title={t("Explore the journey","任务证明旅程")} sub={t("Follow every observation, including uncertainty.","按阶段查看每一步的依据与未知项。")}/>
    {steps.map((v,i)=><Pressable key={v.key} onPress={()=>choose(v.key)} style={[s.step,stage===v.key&&s.stepActive]} accessibilityRole="button" accessibilityState={{selected:stage===v.key}}>
     <View style={[s.stepMarker,stage===v.key&&s.stepMarkerActive]}><Text style={[s.stepNumber,stage===v.key&&{color:"#fff"}]}>{String(i+1).padStart(2,"0")}</Text></View><View style={{flex:1}}><Text style={s.stepTitle}>{v.label}</Text><Text style={s.small}>{v.note}</Text></View><Text style={s.stepChevron}>›</Text>
    </Pressable>)}
   </>}
   {tab==="payment"&&<>
    <View style={s.paymentHero}><Text style={s.eyebrow}>{l("SETTLEMENT / VERIFIED FACTS")}</Text><Text style={s.paymentLead}>{t("Uncertain is\na valid state.","未知，也是一种\n重要的状态。")}</Text><View style={s.paymentStatus}><View style={s.paymentDot}/><Text style={s.paymentStatusText}>PaymentUnknown</Text></View><Text style={s.heroBodyDark}>{t("No missing acknowledgement can justify a second payment.","响应丢失不意味着未支付，也不能据此重扣。")}</Text></View>
    <InfoHeader index="01" title={t("Payment observation","支付观察")} sub={t("Claims and independently observed receipts stay separate.","Recorder 声明和真实回执分别展示。")}/>
    <View style={s.card}><Text style={s.eyebrow}>{l("INDEPENDENT SETTLEMENT FACT")}</Text><Text style={[s.bigState,{color:C.amber}]}>PaymentUnknown</Text><Text style={s.lede}>{l("Recorder claim: ")}{l(live?(state==="Created"?"NotRecorded":"Paid"):fact.recorderClaim)} · {t("Neither proof of paid nor unpaid.","既不代表付款成功，也不代表未付款。")}</Text>
     <View style={s.divider}/><Row label={l("USDC Transfer")} value={l("NOT VERIFIED")}/><Row label={l("AuthorizationUsed")} value={l("NOT VERIFIED")}/><Row label={l("EIP-3009 nonce")} value={l("NOT AVAILABLE")}/><Row label={l("Charge again?")} value={l("NO · unsafe")}/>
    </View><View style={s.notice}><Text style={{color:C.amber,fontWeight:"700"}}>{l("↗ Lost ACK protection")}</Text><Text style={s.small}>{t("Timeout, disconnection or lost response never authorizes a second charge. Reconcile USDC events, signed intent and reservation first.","超时、断链、丢失响应时禁止重扣。只有取得独立 USDC、签名意图和预留记录后才能继续对账。")}</Text></View>
    <Button title={t("Show PaymentUnknown / Lost ACK","演示 PaymentUnknown / Lost ACK")} secondary onPress={()=>choose("unknown")}/>
   </>}
   {tab==="evidence"&&<>
    <View style={s.evidenceHero}><View style={s.evidenceOrb}><Text style={s.evidenceOrbText}>◇</Text></View><View style={{flex:1}}><Text style={s.eyebrow}>{t("PROOF / SHA-256","证据 / SHA-256")}</Text><Text style={s.evidenceHeroTitle}>{t("Verify the bytes.\nKeep the context.","字节可核验，\n结论有边界。")}</Text><Text style={s.small}>{t("An anchor match does not verify a USDC payment.","摘要相同不代表付款成功。")}</Text></View></View>
    <InfoHeader index="01" title={t("Evidence anchor","证据锚点")} sub={t("Recalculate exact bytes, not an AI summary.","核验原始字节，而非依赖文字结论。")}/>
    <View style={s.card}><Text style={s.eyebrow}>{l("EVIDENCE DIGEST")}</Text><Text style={s.cardTitle}>{l("SHA-256 anchor")}</Text><Text style={[s.hash,{marginTop:15}]} selectable>{hash}</Text>
     <View style={s.divider}/><Row label={l("Verification")} value={l(verified?(live?"HASH MATCHED · PAY UNKNOWN":"SIMULATED HASH MATCH · VERIFIED BY SHA-256"):"NOT VERIFIED")}/><Row label={l("Payment")} value="PaymentUnknown"/>
    </View>
    <InfoHeader index="02" title={t("Files on this device","选择本机文件")} sub={t("Your files are hashed locally; nothing is uploaded.","文件仅在设备本地计算 SHA-256。")}/>
    <View style={s.card}>
      {Platform.OS==="web"&&(["evidence","input","output"] as DocumentKind[]).map(kind=>
        React.createElement("input",{
          key:kind,type:"file","data-kind":kind,
          accept:kind==="evidence"?"application/json":undefined,
          style:{display:"none"},
          ref:(el:HTMLInputElement|null)=>{webInputs.current[kind]=el;},
          onChange:(event:React.ChangeEvent<HTMLInputElement>)=>{
            const target=event.currentTarget;
            const file=target.files?.[0];
            if(file)void acceptWebInput(kind,file,webPickerEpoch.current[kind]);
            target.value="";
          },
        })
      )}
      <Text style={s.eyebrow}>{t("ON-DEVICE FILE VERIFICATION","本机文件校验")}</Text>
      <Text style={s.small}>{t("Choose canonical JSON (2 MB max) and optional original binary input/output (10 MB each). Files stay on your device.","选择原始 Evidence JSON（最大 2 MB）和可选的二进制输入/输出（各最大 10 MB）。不会上传文件。")}</Text>
      <Button title={l(localFiles.evidence?"Evidence: ":"Choose Evidence JSON file")+(localFiles.evidence?localFiles.evidence.name:"")}
        secondary disabled={fileReading} onPress={()=>{void selectFile("evidence");}}/>
      <Button title={l(localFiles.input?"Input: ":"Choose original input file (optional)")+(localFiles.input?localFiles.input.name:"")}
        secondary disabled={fileReading} onPress={()=>{void selectFile("input");}}/>
      <Button title={l(localFiles.output?"Output: ":"Choose original output file (optional)")+(localFiles.output?localFiles.output.name:"")}
        secondary disabled={fileReading} onPress={()=>{void selectFile("output");}}/>
      {!!fileDiagnostic&&<Text style={[s.small,{color:fileDiagnostic.startsWith("FILE_NOT")?C.red:C.mint}]}>{fileDiagnostic}</Text>}
    </View>
    {live&&<View>
      <TextInput accessibilityLabel={l("Evidence JSON")} multiline autoCapitalize="none" autoCorrect={false}
       placeholder={l("Paste canonical Evidence JSON locally")} placeholderTextColor={C.sub}
       value={evidenceJson} onChangeText={value=>editText("evidence",value)} style={[s.field,{minHeight:116,textAlignVertical:"top"}]}/>
      <TextInput accessibilityLabel={l("Input bytes text")} multiline autoCapitalize="none" autoCorrect={false}
       placeholder={l("Optional original input · UTF-8 text only")} placeholderTextColor={C.sub}
       value={inputText} onChangeText={value=>editText("input",value)} style={s.field}/>
      <TextInput accessibilityLabel={l("Output bytes text")} multiline autoCapitalize="none" autoCorrect={false}
       placeholder={l("Optional original output · UTF-8 text only")} placeholderTextColor={C.sub}
       value={outputText} onChangeText={value=>editText("output",value)} style={s.field}/>
    </View>}
    <Button title={l(verified?"✓ SHA-256 matched":"Verify SHA-256 locally")}
      disabled={fileReading||fileDiagnostic.startsWith("FILE_NOT_VERIFIED")||state!=="Proven"||(live&&!evidenceJson.trim()&&!localFiles.evidence)} onPress={verify}/>
    {!!verificationError&&<Text style={[s.small,{color:C.red}]}>{verificationError}</Text>}
    <Text style={[s.small,{marginTop:12}]}>{t("This app computes canonical Evidence SHA-256. The demo checks a pinned reference digest; real reads compare an on-chain anchor. Input/output may be original binary files or optional UTF-8 text. A hash match does not prove payment or execution correctness.","本应用实际计算 canonical JSON SHA-256。模拟路径用预先固定的独立参考哈希；真实查询使用链上锚定值。支持原始输入/输出二进制文件或 UTF-8 文本。校验摘要不证明支付或执行正确性。")}</Text>
   </>}
   {tab==="journal"&&<>
     <View style={s.journalHero}><Text style={s.eyebrow}>{l("AUDIT / OBSERVATIONS")}</Text><Text style={s.evidenceHeroTitle}>{t("A trail you\ncan explain.","可复查的\n观察记录。")}</Text><Text style={s.small}>{t("A useful history never invents blockchain finality.","记录的是事实与不确定性，不是虚构的终局。")}</Text></View>
     <InfoHeader index="01" title={t("Observation timeline","观察时间线")}/>
     <View style={s.card}><Text style={s.cardTitle}>{l("Recent observations")}</Text><Text style={s.lede}>{t("Local demonstration only. No server sync or wallet transactions.","本机演示，无服务器同步、无钱包交易。")}</Text>
       <View style={s.divider}/>{steps.map((x,i)=><View key={x.key} style={s.journalItem}><Text style={s.stepNumber}>{String(i+1).padStart(2,"0")}</Text><View><Text style={s.stepTitle}>{x.label}</Text><Text style={s.small}>{x.note}</Text></View></View>)}
     </View>
     <Button title={t("Export summary.json","导出 summary.json")} secondary onPress={onExport}/>
     {!!exportNotice&&<Text style={[s.small,{color:exportNotice.startsWith("EXPORT_FAILED")?C.red:C.mint}]}>{l(exportNotice)}</Text>}
   </>}
  </ScrollView>
  <View style={s.tabbar}>{tabs.map((item,i)=><Pressable key={item} accessibilityRole="tab" accessibilityState={{selected:tab===item}} onPress={()=>setTab(item)} style={[s.tab,tab===item&&s.tabSelected]}>
   <Text style={[s.tabIcon,tab===item&&s.tabOn]}>{["◫","⇄","◇","▤"][i]}</Text>
   <Text style={[s.tabText,tab===item&&s.tabOn]}>{labels[item]}</Text>
  </Pressable>)}</View>
 </SafeAreaView>;
}
function Inspector({fact,observed,verification,onExport,exportNotice}:{fact:DemoFact;observed:TaskRead|null;verification:string;onExport:()=>void;exportNotice:string}){
 const isLive=observed!==null;
 const chainState=observed?.status??fact.state;
 const assertions=[
  [l("Task identity"),isLive?l("RPC task identity · Chain 10143 · read-only"):l("fixture domain only"),true],
  [l("Recorder status"),(isLive?(chainState==="Created"?"NotRecorded":"Paid"):fact.recorderClaim)+l(" · NOT payment proof"),true],
  [l("USDC Transfer"),l("not independently observed"),false],
  ["EIP-3009 nonce",l("not independently observed"),false],
  [l("Evidence anchor"),verification.startsWith("HASH_MATCHED")?l("SHA-256 MATCH (payment unknown)"):chainState==="Proven"?l("hash available; bytes unchecked"):l("not established"),verification.startsWith("HASH_MATCHED")],
  [l("Reviewed deployment"),l("not registered"),false],
 ] as const;
 const report=observed?taskReadSummary(observed,verification):exportSummary(fact,verification.startsWith("HASH_MATCHED"));
 return <ScrollView style={s.inspector} contentContainerStyle={{padding:28}}>
  <View style={s.inspectorBrand}><Mark size={38}/><View><Text style={s.inspectorEyebrow}>{l("AGENTLEDGER / REVIEW DESK")}</Text><Text style={s.inspectorBrandSub}>{l("READ-ONLY OBSERVATION CONSOLE")}</Text></View></View>
  <Text style={s.eyebrow}>{l("JUDGE / EVIDENCE VIEW")}</Text><Text style={s.h2}>{l("Every claim has a boundary.")}</Text><Text style={s.inspectorIntro}>{l("Inspect the observed facts, their sources and what still needs independent verification.")}</Text>
  <View style={[s.notice,{marginTop:18}]}><Text style={{color:C.amber,fontWeight:"700"}}>{l(isLive?"TESTNET TASK ONLY · PAYMENT UNKNOWN":"LOCAL SIMULATION · NOT TESTNET ACCEPTED")}</Text><Text style={s.small}>{l(isLive?"Task fetched read-only via verifier. Payment not independently accepted.":"No RPC contacted. No funds spent. No real payment verified.")}</Text></View>
  <InfoHeader index="01" title={l("Chain context")}/><Row label={l("Chain ID")} value={l(isLive?"10143 (task read)":"10143 (simulated)")}/><Row label={l("Contract")} value={shorten(observed?.contract??fact.ledger)}/><Row label={l("RPC identity")} value={l(isLive?"local GET verifier → Monad RPC":"testnet-rpc.monad.xyz (label only)")}/><Row label={l("Task ID")} value={shorten(observed?.taskId??fact.taskId)}/><Row label={l("Description")} value={shorten(observed?.descriptionHash??fact.descriptionHash)}/>
  <InfoHeader index="02" title={l("Read-only assertions")}/>{assertions.map(([title,detail,ok])=><View key={title} style={s.assertion}><Text style={{color:ok?C.mint:C.amber,fontWeight:"800"}}>{ok?"✓":"○"}</Text><View style={{flex:1}}><Text style={s.stepTitle}>{title}</Text><Text style={s.small}>{detail}</Text></View></View>)}
  <InfoHeader index="03" title={l("summary.json preview")}/><View style={s.jsonBox}><Text style={s.json} selectable>{JSON.stringify(report,null,2)}</Text></View>
  <Button title={l("Export summary.json")} secondary onPress={onExport}/>
  {!!exportNotice&&<Text style={[s.small,{color:exportNotice.startsWith("EXPORT_FAILED")?C.red:C.mint}]}>{exportNotice}</Text>}
  <Text style={[s.small,{marginTop:20}]}>{l("Read-only evidence viewer · No signing, payment, execution or automatic recharge.")}</Text>
 </ScrollView>;
}
export default function App(){
 const {width}=useWindowDimensions();const[stage,setStage]=useState<Stage>("created");
 const [uiLang,setUiLang]=useState<"en"|"zh">(initialLanguage);
 ZH=uiLang==="zh";
 function changeLanguage(){
  const next=uiLang==="zh"?"en":"zh";
  if(Platform.OS==="web")try{window.localStorage.setItem(LANGUAGE_KEY,next);}catch{}
  setUiLang(next);
 }
 const [observed,setObserved]=useState<TaskRead|null>(null);
 const [verification,setVerification]=useState("NOT_VERIFIED");
 const [exportNotice,setExportNotice]=useState("");
 async function exportCurrent(){
   setExportNotice("");
   const report=observed?taskReadSummary(observed,verification):
     exportSummary(FACTS[stage],verification.startsWith("HASH_MATCHED"));
   try{
     await saveSummaryJson(report);
     setExportNotice("summary.json · local download/share opened");
   }catch(error){
     setExportNotice("EXPORT_FAILED: "+String((error as Error).message));
   }
 }
 const desktop=Platform.OS==="web"&&width>=1040;
 return <View style={[s.root,desktop&&s.wide]}>{desktop?<><View style={s.phoneFrame}><Phone stage={stage} setStage={setStage} observed={observed} onObserve={setObserved} onVerified={setVerification} onExport={()=>{void exportCurrent();}} exportNotice={exportNotice} onChangeLanguage={changeLanguage}/></View><View style={s.inspectorFrame}><Inspector fact={FACTS[stage]} observed={observed} verification={verification} onExport={()=>{void exportCurrent();}} exportNotice={exportNotice}/></View></>:<Phone stage={stage} setStage={setStage} observed={observed} onObserve={setObserved} onVerified={setVerification} onExport={()=>{void exportCurrent();}} exportNotice={exportNotice} onChangeLanguage={changeLanguage}/>}</View>;
}
const s=StyleSheet.create({
 presenterStrip:{paddingHorizontal:18,paddingTop:12,paddingBottom:12,backgroundColor:"#EDF4FF",borderBottomColor:"#C7D9F4",borderBottomWidth:1},
 presenterTop:{flexDirection:"row",alignItems:"center",justifyContent:"space-between"},
 presenterEyebrow:{fontSize:10,fontWeight:"800",letterSpacing:.65,color:C.purple,flexShrink:1},
  presenterStepRow:{flexDirection:"row",justifyContent:"space-between",alignItems:"center",gap:8,marginTop:7},
  presenterStepCount:{fontSize:10,fontWeight:"900",color:C.purple},
  presenterHint:{fontSize:11,lineHeight:16,color:"#506985",marginBottom:5},
  presenterCloseHit:{minWidth:40,minHeight:40,alignItems:"center",justifyContent:"center",backgroundColor:"#E0EAF9",borderRadius:10},
  presenterBackHit:{flex:1,minHeight:44,alignItems:"center",justifyContent:"center",borderRadius:10,borderWidth:1,borderColor:"#CBDAEE",backgroundColor:"#fff"},
  presenterNextHit:{flex:1.45,minHeight:44,alignItems:"center",justifyContent:"center",borderRadius:10,backgroundColor:C.purple},
  presenterExportHit:{flex:1.45,minHeight:44,alignItems:"center",justifyContent:"center",borderRadius:10,backgroundColor:C.mint},
  presenterExportText:{fontSize:11.5,fontWeight:"800",color:"#fff"},
 presenterClose:{color:"#486685",fontSize:23,fontWeight:"700",lineHeight:27},
 presenterProgress:{flexDirection:"row",gap:5,marginTop:8,marginBottom:8},
 presenterSegment:{flex:1,height:5,borderRadius:5,backgroundColor:"#CDDCEE"},
 presenterSegmentOn:{backgroundColor:C.purple},
 presenterActions:{flexDirection:"row",justifyContent:"space-between",alignItems:"center",gap:9,marginTop:7},
 presenterAction:{fontSize:12,fontWeight:"800",color:C.purple},
 presenterCurrent:{fontSize:11,fontWeight:"800",color:C.ink,flexShrink:1,textAlign:"right"},
 presenterCaution:{fontSize:9,color:C.amber,fontWeight:"800",marginTop:9,textAlign:"center"},
 root:{flex:1,backgroundColor:"#EBEEF5"},
 wide:{flexDirection:"row",justifyContent:"center",alignItems:"center",gap:34,padding:28,backgroundColor:"#ECF0F7"},
 phoneFrame:{height:"100%",maxHeight:880,width:400,borderWidth:8,borderRadius:42,borderColor:"#1D2742",overflow:"hidden",backgroundColor:C.bg,shadowColor:"#10214B",shadowOpacity:.18,shadowRadius:36,shadowOffset:{width:0,height:18}},
 inspectorFrame:{height:"100%",maxHeight:880,width:505,borderColor:"#E2E6F0",borderWidth:1,borderRadius:27,overflow:"hidden",backgroundColor:C.panel,shadowColor:"#1A2750",shadowOpacity:.06,shadowRadius:24,shadowOffset:{width:0,height:9}},
 phone:{flex:1,backgroundColor:C.bg},
 languageButton:{minWidth:42,minHeight:40,alignItems:"center",justifyContent:"center",backgroundColor:"#EDF4FF",borderRadius:10,borderWidth:1,borderColor:"#CADBF1"},
 languageButtonText:{color:C.purple,fontWeight:"900",fontSize:11},
 header:{paddingHorizontal:14,paddingTop:18,paddingBottom:17,flexDirection:"row",justifyContent:"space-between",alignItems:"center",borderBottomWidth:1,borderBottomColor:C.line,backgroundColor:"#fff"},
 brandGroup:{flexDirection:"row",alignItems:"center",gap:10},mark:{backgroundColor:C.purple,alignItems:"center",justifyContent:"center"},markText:{color:"#fff",fontWeight:"800"},
 brand:{color:C.ink,fontSize:20,fontWeight:"900",letterSpacing:-.9},kicker:{fontSize:9,fontWeight:"800",letterSpacing:1.15,color:C.sub,marginTop:3},
 chip:{paddingHorizontal:11,paddingVertical:7,borderRadius:24},chipText:{fontWeight:"800",fontSize:9,letterSpacing:.45},
 scroller:{flex:1},headline:{marginBottom:16},
 eyebrow:{fontSize:10,fontWeight:"900",letterSpacing:1.2,color:C.purple},
 h1:{fontSize:29,fontWeight:"900",color:C.ink,marginTop:10,letterSpacing:-.9},
 h2:{fontSize:28,fontWeight:"900",color:C.ink,marginTop:13,lineHeight:35,letterSpacing:-1},
 lede:{color:C.sub,fontSize:12,lineHeight:19,marginTop:9},
 hero:{backgroundColor:C.night,borderRadius:24,padding:22,marginTop:3,marginBottom:22,overflow:"hidden",minHeight:258,position:"relative"},
 heroTop:{flexDirection:"row",alignItems:"center",justifyContent:"space-between"},
 heroLabel:{fontSize:10,fontWeight:"800",letterSpacing:1.4,color:"#B8B5FF"},
 heroStamp:{fontSize:23,color:"#B7B5FF",fontWeight:"600"},
 heroOrbitOne:{position:"absolute",width:230,height:230,borderRadius:115,borderWidth:1,borderColor:"#6E74B0",right:-113,top:-74,opacity:.45},
 heroOrbitTwo:{position:"absolute",width:147,height:147,borderRadius:80,borderWidth:26,borderColor:"#424779",right:-52,top:-18,opacity:.55},
 heroTitle:{fontSize:29,fontWeight:"900",color:"#fff",lineHeight:34,marginTop:28,letterSpacing:-.9},
 heroBody:{fontSize:11.5,lineHeight:18,color:"#C7CBE3",marginTop:10,maxWidth:255},
 heroBottom:{flexDirection:"row",alignItems:"center",marginTop:28,gap:7},
 heroStatusDot:{width:7,height:7,borderRadius:5,backgroundColor:"#62DDBF"},
 heroFoot:{color:"#C6CCE5",fontSize:9,fontWeight:"800",letterSpacing:1.1,flex:1},
 heroFootRight:{backgroundColor:"#FFFFFF21",borderWidth:1,borderColor:"#FFFFFF24",paddingVertical:6,paddingHorizontal:9,borderRadius:8},
 heroFootRightText:{color:"#fff",fontWeight:"900",fontSize:9},
 sectionHeader:{flexDirection:"row",alignItems:"center",gap:12,marginTop:9,marginBottom:13},
 sectionIndex:{backgroundColor:"#E8E5FF",width:29,height:29,borderRadius:9,justifyContent:"center",alignItems:"center"},
 sectionIndexText:{fontSize:11,color:C.purple,fontWeight:"900"},
 sectionTitle:{fontSize:15,fontWeight:"900",color:C.ink},
 sectionSubtitle:{fontSize:10.5,color:C.sub,marginTop:3,lineHeight:16},
 card:{backgroundColor:"#fff",borderRadius:19,padding:19,borderWidth:1,borderColor:"#E6EAF3",marginBottom:18,shadowColor:"#22345A",shadowOpacity:.035,shadowRadius:10,shadowOffset:{width:0,height:3}},
 inline:{flexDirection:"row",justifyContent:"space-between",alignItems:"center",gap:10},cardTitle:{fontSize:17,color:C.ink,fontWeight:"900"},
 section:{fontSize:14,fontWeight:"900",color:C.ink,marginVertical:15},
 divider:{height:1,backgroundColor:"#EFF1F6",marginVertical:14},
 row:{flexDirection:"row",justifyContent:"space-between",gap:12,marginVertical:9},
 muted:{color:C.sub,fontSize:11,fontWeight:"500"},value:{color:C.ink,fontSize:11,fontWeight:"700",maxWidth:"63%",textAlign:"right"},
 step:{flexDirection:"row",alignItems:"center",gap:12,borderWidth:1,borderColor:"#E6E9F2",backgroundColor:"#fff",padding:14,borderRadius:16,marginBottom:9},
 stepActive:{borderColor:"#A9C3EF",backgroundColor:"#EAF2FF"},
 stepMarker:{height:38,width:38,borderRadius:12,backgroundColor:"#EDEFF6",justifyContent:"center",alignItems:"center"},
 stepMarkerActive:{backgroundColor:C.purple},stepNumber:{color:"#8390A8",fontWeight:"900",fontSize:12},
 stepTitle:{color:C.ink,fontWeight:"800",fontSize:12.5},small:{color:C.sub,fontSize:11,lineHeight:17,marginTop:4},
 stepChevron:{color:C.purple,fontSize:21,fontWeight:"600"},
 paymentHero:{backgroundColor:"#FFF3E4",borderColor:"#F5DDBD",borderWidth:1,borderRadius:23,padding:23,marginBottom:20},
 paymentLead:{fontSize:28,lineHeight:33,fontWeight:"900",color:"#604126",marginTop:16,letterSpacing:-.7},
 paymentStatus:{backgroundColor:"#fff",borderWidth:1,borderColor:"#F2D9B7",padding:13,borderRadius:13,flexDirection:"row",gap:9,alignItems:"center",marginTop:19},
 paymentDot:{width:9,height:9,borderRadius:5,backgroundColor:"#E39B41"},
 paymentStatusText:{fontSize:17,fontWeight:"900",color:"#9A5D22"},
 heroBodyDark:{fontSize:11.5,lineHeight:18,color:"#9B7852",marginTop:13},
 evidenceHero:{flexDirection:"row",gap:16,backgroundColor:"#EBE8FF",borderRadius:23,padding:19,marginBottom:20,borderWidth:1,borderColor:"#E1DCFF",alignItems:"center"},
 evidenceOrb:{height:55,width:55,borderRadius:19,backgroundColor:C.purple,justifyContent:"center",alignItems:"center"},
 evidenceOrbText:{fontSize:31,fontWeight:"500",color:"#fff"},
 evidenceHeroTitle:{fontSize:22,lineHeight:27,fontWeight:"900",color:C.ink,marginTop:9,marginBottom:8},
 journalHero:{backgroundColor:"#E4F5EE",borderRadius:23,padding:22,borderWidth:1,borderColor:"#CEF0E1",marginBottom:22},
 bigState:{fontSize:24,fontWeight:"900",marginTop:12},
 notice:{backgroundColor:"#FFF6E9",borderRadius:15,padding:17,marginBottom:16,borderWidth:1,borderColor:"#F3E1C8"},
 button:{backgroundColor:C.purple,borderRadius:12,padding:15,alignItems:"center",marginVertical:8,minHeight:46,justifyContent:"center"},
 buttonSecondary:{backgroundColor:"#F1F2FC",borderWidth:1,borderColor:"#DEDEFA"},
 buttonText:{color:"#fff",fontWeight:"800",fontSize:12.5},
 hash:{color:C.ink,fontSize:11.5,lineHeight:19,fontFamily:Platform.OS==="web"?"monospace":"Courier"},
 tabbar:{flexDirection:"row",borderTopWidth:1,borderTopColor:C.line,backgroundColor:"#fff",paddingTop:9,paddingBottom:Platform.OS==="web"?13:7,paddingHorizontal:10},
 tab:{flex:1,paddingVertical:7,alignItems:"center",borderRadius:11},
 tabSelected:{backgroundColor:"#E8F0FF"},
 tabIcon:{color:"#A1ABC0",fontSize:20,fontWeight:"600",marginBottom:4},
 tabText:{color:"#94A1B6",fontSize:10,fontWeight:"800"},tabOn:{color:C.purple},
 inspector:{flex:1},inspectorBrand:{flexDirection:"row",gap:12,alignItems:"center",paddingBottom:24,marginBottom:24,borderBottomWidth:1,borderBottomColor:C.line},
 inspectorEyebrow:{fontSize:11,fontWeight:"900",letterSpacing:1.2,color:C.ink},inspectorBrandSub:{fontSize:9,letterSpacing:1.05,fontWeight:"700",color:C.sub,marginTop:5},
 inspectorIntro:{fontSize:12,color:C.sub,lineHeight:20,marginTop:10,marginBottom:12},
 assertion:{flexDirection:"row",gap:14,alignItems:"flex-start",borderBottomWidth:1,borderBottomColor:"#EDF0F6",paddingVertical:11},
 jsonBox:{backgroundColor:"#19213A",borderColor:"#293657",borderWidth:1,borderRadius:15,padding:17},
 json:{color:"#ADE5CD",fontSize:10,lineHeight:17,fontFamily:Platform.OS==="web"?"monospace":"Courier"},
 field:{backgroundColor:"#F8F9FC",borderColor:"#DEE4EF",borderWidth:1,borderRadius:12,color:C.ink,padding:13,marginTop:12,fontSize:12},
 journalItem:{flexDirection:"row",gap:16,paddingVertical:13,borderBottomWidth:1,borderBottomColor:C.line},
});