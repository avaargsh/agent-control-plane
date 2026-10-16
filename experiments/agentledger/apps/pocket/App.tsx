// SPDX-License-Identifier: Apache-2.0
// Original mobile-first interface inspired by Pocket's iOS + Web product
// structure, not a port of its trading/wallet implementation.
import React, {useMemo,useRef,useState} from "react";
import {ActivityIndicator,Platform,Pressable,ScrollView,Share,
  StyleSheet,Text,TextInput,useWindowDimensions,View} from "react-native";
import {SafeAreaProvider,SafeAreaView} from "react-native-safe-area-context";
import {StatusBar} from "expo-status-bar";
import * as Crypto from "expo-crypto";
import {demoTask,SCENARIOS,publicObservation,summaryJson,
  demoIdentityRejections,RPC_ID,CHAIN_ID} from "./src/domain.mjs";
import {observeTask} from "./src/client.mjs";
import {verifyDemoEvidence} from "./src/evidence.mjs";

const C={
  background:"#0B101A",panel:"#151C2A",panel2:"#1B2434",
  line:"#2B3546",white:"#F5F7FF",muted:"#9AA9BB",
  quiet:"#6E7D91",blue:"#A3BFFF",mint:"#A6EDC7",
  amber:"#F4C989",red:"#FF9A9F",accent:"#D9F875",
};
type Tab="Home"|"Tasks"|"Proof"|"Journal";
type Mode="SIMULATION"|"LIVE";
const TABS:Tab[]=["Home","Tasks","Proof","Journal"];
function cut(input?:string,n=9){if(!input)return "—";return input.length>n*2?input.slice(0,n)+"…"+input.slice(-6):input;}
function Txt({children,color=C.white,size=14,weight="400",style}:{
  children:React.ReactNode;color?:string,size?:number,weight?:"400"|"500"|"600"|"700"|"800",style?:any
}){return <Text style={[{color,fontSize:size,fontWeight:weight},style]}>{children}</Text>;}
function Button({label,onPress,secondary=false,disabled=false}:{
  label:string,onPress:()=>void,secondary?:boolean,disabled?:boolean
}){
 return <Pressable onPress={onPress} disabled={disabled}
    accessibilityRole="button" accessibilityLabel={label}
    style={[S.button,secondary?S.buttonSecondary:S.buttonPrimary,disabled&&{opacity:.45}]}>
    <Txt size={14} weight="700" color={secondary?C.white:C.background}>{label}</Txt>
    <Txt size={17} weight="700" color={secondary?C.white:C.background}>↗</Txt>
  </Pressable>;
}
function Tag({label,kind="neutral"}:{label:string,kind?:"green"|"yellow"|"red"|"neutral"}){
 const bg=kind==="green"?"#1F3B35":kind==="yellow"?"#483925":kind==="red"?"#42272F":"#273148";
 const fg=kind==="green"?C.mint:kind==="yellow"?C.amber:kind==="red"?C.red:C.blue;
 return <View style={[S.tag,{backgroundColor:bg}]}><Txt size={10} color={fg} weight="800">{label}</Txt></View>;
}
function Card({children,style}:{children:React.ReactNode,style?:any}){
 return <View style={[S.card,style]}>{children}</View>;
}
function Row({children,style}:{children:React.ReactNode,style?:any}){
 return <View style={[{flexDirection:"row",alignItems:"center",justifyContent:"space-between"},style]}>{children}</View>;
}
function Detail({label,value,mono=true}:{
  label:string,value:string,mono?:boolean
}){
 return <View style={S.detail}><Txt color={C.muted} size={11}>{label}</Txt>
 <Txt size={12} weight="600" style={mono?S.mono:{}}>{value}</Txt></View>;
}
export default function App(){
 const {width}=useWindowDimensions();
 const wide=width>=1040;
 const [entered,setEntered]=useState(false);
 const [tab,setTab]=useState<Tab>("Home");
 const [mode,setMode]=useState<Mode>("SIMULATION");
 const [stage,setStage]=useState("created");
 const [liveTask,setLiveTask]=useState<any>(null);
 const [contract,setContract]=useState("");
 const [taskId,setTaskId]=useState("");
 const [busy,setBusy]=useState(false);
 const [error,setError]=useState("");
 const [proofMatched,setProofMatched]=useState(false);
 const [events,setEvents]=useState<string[]>(["Verification Studio · local demo initialized"]);
 const seq=useRef(0);
 const task=mode==="SIMULATION"?demoTask(stage):liveTask;
 const fact=task?publicObservation(task,{simulation:mode==="SIMULATION",evidenceChecked:proofMatched}):null;
 const rejects=useMemo(()=>demoIdentityRejections() as Array<{kind:string;result:string}>,[]);
 const journal=(description:string)=>setEvents(e=>[description,...e].slice(0,12));
 const stageChange=(key:string)=>{
  seq.current++;setStage(key);setMode("SIMULATION");setProofMatched(false);setError("");
  journal("LOCAL SIMULATION · "+key+" · payment still PaymentUnknown");
 };
 const selectMode=(newMode:Mode)=>{
  seq.current++;setMode(newMode);setError("");setProofMatched(false);setLiveTask(null);
  journal(newMode==="LIVE"?"Entered read-only lookup; no wallet":"Returned to local simulation");
 };
 const lookup=async()=>{
  const current=++seq.current;setError("");setBusy(true);setLiveTask(null);
  try{
   const base=process.env.EXPO_PUBLIC_VERIFIER_API_URL;
   if(!base)throw Error("请先设置 EXPO_PUBLIC_VERIFIER_API_URL (只读 API 地址)");
   const result=await observeTask(base,contract.trim(),taskId.trim());
   if(current!==seq.current)return;
   setLiveTask(result);
   journal("TESTNET TASK ONLY · observed "+cut(result.taskId)+" · payment unverified");
  }catch(e){
   if(current===seq.current)setError(e instanceof Error?e.message:"Read-only lookup failed");
  }finally{if(current===seq.current)setBusy(false);}
 };
 const verify=async()=>{
  if(mode!=="SIMULATION"||stage!=="proven"){setError("当前版本只能核验本地模拟 Evidence；真实数据请使用独立只读 verifier。");return;}
  setBusy(true);setError("");
  try{
   const result=await verifyDemoEvidence((input:string)=>Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256,input));
   setProofMatched(result.match);
   journal("LOCAL EVIDENCE · SHA-256 matched · payment unknown");
  }catch(e){setProofMatched(false);setError(e instanceof Error?e.message:"Evidence mismatch");}
  finally{setBusy(false);}
 };
 const shareSummary=async()=>{
  if(!task)return;
  const content=summaryJson(task,{simulation:mode==="SIMULATION",evidenceChecked:proofMatched});
  if(Platform.OS==="web"){
    const data=new Blob([content+"\n"],{type:"application/json"});
    const url=URL.createObjectURL(data);
    const link=document.createElement("a");
    link.href=url;link.download="summary.json";document.body.appendChild(link);link.click();link.remove();
    URL.revokeObjectURL(url);
  }else await Share.share({message:content,title:"AgentLedger Pocket · read-only summary.json"});
 };
 const welcome=(
 <View style={S.welcome}>
  <View style={S.eyebrowWrap}><View style={S.dot}/><Txt color={C.accent} size={12} weight="700">AGENTLEDGER / POCKET</Txt></View>
  <View style={S.welcomeOrb}><View style={S.orbit}><Txt color={C.accent} weight="800" size={64}>◎</Txt></View><Txt color={C.muted} size={10} style={{marginTop:16,letterSpacing:3}}>PROOF OVER PROMISES</Txt></View>
  <Txt size={39} weight="800" style={{letterSpacing:-1.4,lineHeight:47,marginTop:28}}>把 Agent 任务，{"\n"}装进口袋。</Txt>
  <Txt color={C.muted} size={15} style={{lineHeight:25,marginTop:18}}>
    查看执行任务、支付观察和证据哈希。你看到的不只是状态，而是每一个可以核对的事实。
  </Txt>
  <View style={{marginTop:30,gap:9}}>
   <Button label="进入模拟体验" onPress={()=>{setEntered(true);setTab("Tasks");}}/>
   <Button label="连接只读任务查询" secondary onPress={()=>{setEntered(true);selectMode("LIVE");setTab("Tasks");}}/>
  </View>
  <View style={S.welcomeFoot}><Tag label="NO WALLET / NO REAL CHARGE" kind="yellow"/>
  <Txt color={C.quiet} size={11} style={{marginTop:12,lineHeight:18}}>Monad Testnet · Chain 10143 · 当前不支持真实支付签名</Txt></View>
 </View>);
 const header=(
 <Row style={{paddingHorizontal:22,paddingTop:21,paddingBottom:18}}>
  <View><Txt color={C.blue} size={11} weight="800" style={{letterSpacing:1.7}}>AGENTLEDGER</Txt>
       <Txt color={C.white} size={20} weight="800" style={{letterSpacing:-.5}}>Pocket <Txt color={C.accent} size={14}>◈</Txt></Txt></View>
  <Pressable onPress={()=>setTab("Journal")} accessibilityRole="button" accessibilityLabel="打开记录">
   <View style={S.headerIcon}><Txt size={20}>☷</Txt></View>
  </Pressable>
 </Row>);
 const home=(
  <View>
   <View style={S.sectionSpace}><Tag label="BUILT FOR MONAD · PHASE 0" kind="green"/>
    <Txt size={33} weight="800" style={{letterSpacing:-1.3,lineHeight:39,marginTop:19}}>
     Every action.{"\n"}Every proof.
    </Txt>
    <Txt color={C.muted} size={13} style={{lineHeight:22,marginTop:9}}>从任务创建到链上存证，随手查证 Agent 做过什么。</Txt>
   </View>
   <Card style={{backgroundColor:"#222B41",padding:23}}>
    <Row><Txt color={C.blue} weight="800" size={11} style={{letterSpacing:1.4}}>VERIFICATION FIRST</Txt><Txt size={27} color={C.accent}>◈</Txt></Row>
    <Txt size={23} weight="800" style={{marginTop:16,lineHeight:30}}>不相信一个 Paid，{"\n"}只相信证据。</Txt>
    <Txt color={C.muted} size={12} style={{lineHeight:21,marginTop:13}}>Recorder 的付款声明 ≠ USDC 转账验证。Evidence 哈希匹配 ≠ Agent 业务正确。</Txt>
    <View style={{marginTop:22}}><Button label="开始任务旅程" onPress={()=>setTab("Tasks")}/></View>
   </Card>
   <Txt color={C.muted} weight="700" size={12} style={S.sectionTitle}>三步完成验证</Txt>
   {[
    ["01","Task","查看任务身份与 descriptionHash"],
    ["02","Settlement","核对付款回执、Nonce 和 Recorder 声明"],
    ["03","Evidence","重算 SHA-256，并回看证据"],
   ].map(([n,title,description])=><View style={S.stepRow} key={n}>
    <View style={S.stepNumber}><Txt color={C.accent} weight="800">{n}</Txt></View>
    <View style={{flex:1}}><Txt weight="700" size={15}>{title}</Txt><Txt color={C.muted} size={12} style={{marginTop:4,lineHeight:18}}>{description}</Txt></View>
    <Txt color={C.quiet} size={21}>↗</Txt>
   </View>)}
  </View>
 );
 const tasks=(
 <View>
  <Txt color={C.muted} weight="700" size={11} style={{letterSpacing:1.5}}>TASK EXPLORER</Txt>
  <Txt size={29} weight="800" style={{marginTop:8}}>我的任务</Txt>
  <View style={S.toggle}>
   {(["SIMULATION","LIVE"] as Mode[]).map(m=><Pressable accessibilityRole="button" key={m}
      onPress={()=>selectMode(m)} style={[S.toggleItem,mode===m&&S.toggleActive]}>
      <Txt color={mode===m?C.white:C.muted} weight="700" size={12}>{m==="SIMULATION"?"模拟旅程":"只读 Testnet"}</Txt>
   </Pressable>)}
  </View>
  {mode==="SIMULATION"?<>
   <Txt color={C.quiet} size={11} style={{lineHeight:18,marginBottom:12}}>所有情境都是合成数据，不会签名、支付或提交交易。</Txt>
   <View style={{gap:9}}>{SCENARIOS.map((scenario,index)=><Pressable
    key={scenario.key} accessibilityRole="button" accessibilityState={{selected:stage===scenario.key}}
    onPress={()=>stageChange(scenario.key)}
    style={[S.scenario,stage===scenario.key&&S.scenarioActive]}>
    <View style={S.scenarioIndex}><Txt color={C.accent} weight="800" size={12}>{String(index+1).padStart(2,"0")}</Txt></View>
    <View style={{flex:1}}><Txt weight="700" size={14}>{scenario.summary}</Txt>
      <Txt color={C.muted} size={11} style={{marginTop:5}}>{scenario.hint}</Txt></View>
    <Txt color={stage===scenario.key?C.accent:C.quiet} size={17}>→</Txt>
   </Pressable>)}</View>
  </>:<Card><Txt weight="700" size={15}>只读查询真实任务</Txt>
    <Txt color={C.muted} size={12} style={{marginTop:10,lineHeight:20}}>仅 GET /api/task。不会读取私钥、调用钱包或主动支付。网页需要配置同源代理/CORS。</Txt>
    <TextInput value={contract} onChangeText={setContract} style={S.input}
      placeholder="0x... Ledger contract" placeholderTextColor={C.quiet}
      autoCapitalize="none" autoCorrect={false} maxLength={42} accessibilityLabel="Ledger contract address"/>
    <TextInput value={taskId} onChangeText={setTaskId} style={S.input}
      placeholder="0x... Task ID (bytes32)" placeholderTextColor={C.quiet}
      autoCapitalize="none" autoCorrect={false} maxLength={66} accessibilityLabel="Task identifier"/>
    <Button label={busy?"正在查询…":"查询任务状态"} onPress={lookup} disabled={busy}/>
   </Card>}
  {task&&<Card style={{marginTop:20}}>
    <Row><Txt color={C.muted} size={11} weight="700">TASK OVERVIEW</Txt><Tag label={task.status.toUpperCase()} kind={task.status==="Proven"?"green":task.status==="Paid"?"yellow":"neutral"}/></Row>
    <Txt weight="800" size={19} style={{marginTop:18}}>Task {cut(task.taskId,6)}</Txt>
    <Detail label="descriptionHash" value={cut(task.descriptionHash,13)}/>
    <Detail label="Contract" value={cut(task.contract,11)}/>
    <Detail label="金额 (atomic)" value={task.amountAtomic}/>
    <Detail label="Chain" value="Monad 10143"/>
    <View style={S.borderTop}><Row><Txt color={C.muted} size={11}>独立支付观察</Txt><Txt color={C.amber} weight="800" size={13}>PaymentUnknown</Txt></Row></View>
    <View style={{marginTop:18}}><Button label="查看 Evidence / Receipt" secondary onPress={()=>setTab("Proof")}/></View>
   </Card>}
  {!task&&mode==="LIVE"&&<Card style={{marginTop:20}}><Txt color={C.quiet}>尚未读取任何链上任务</Txt></Card>}
 </View>);
 const proof=(
 <View>
  <Txt color={C.muted} weight="700" size={11} style={{letterSpacing:1.5}}>VERIFY THE FACTS</Txt>
  <Txt size={29} weight="800" style={{marginTop:8}}>证据工作台</Txt>
  <Txt color={C.muted} size={12} style={{lineHeight:20,marginTop:10}}>先分清来源，再判断真假。即使 Recorder 声明 Paid，也不等于验证了 USDC。</Txt>
  <Card style={{marginTop:21}}>
   <Row><Txt color={C.muted} size={11}>PAYMENT OBSERVATION</Txt><Tag label="UNCONFIRMED" kind="yellow"/></Row>
   <Txt color={C.amber} size={23} weight="800" style={{marginTop:13}}>PaymentUnknown</Txt>
   <Detail label="Recorder claim" value={fact?.recorderClaim??"—"}/>
   <Detail label="Transfer event" value="NOT INDEPENDENTLY VERIFIED"/>
   <Detail label="AuthorizationUsed / Nonce" value="UNKNOWN"/>
   <View style={S.notice}><Txt size={12} color={C.amber} weight="700">↗ 绝不根据丢失 ACK 重新扣款</Txt></View>
  </Card>
  <Card style={{marginTop:13}}>
   <Row><Txt color={C.muted} size={11}>EVIDENCE ANCHOR</Txt><Tag label={proofMatched?"LOCAL MATCH":"NOT VERIFIED"} kind={proofMatched?"green":"neutral"}/></Row>
   <Txt size={12} color={C.muted} style={{marginTop:15}}>evidenceHash</Txt>
   <Txt size={13} style={[S.mono,{marginTop:7,lineHeight:21}]}>{task?.evidenceHash??"—"}</Txt>
   <Txt color={C.muted} size={12} style={{lineHeight:21,marginTop:15}}>
    {mode==="LIVE"?"真实 Evidence 请使用 Verification Studio 核验原始 JSON、输入和输出字节。":
      stage==="proven"?"可以在设备上重算固定的模拟 Evidence、输入、输出三份 SHA-256。":
      "切换到 Evidence Anchored 情境后可以执行本地哈希核验。"}
   </Txt>
   <View style={{marginTop:17}}><Button
    label={busy?"正在校验…":proofMatched?"本地 SHA-256 已匹配 ✓":"重算模拟 Evidence SHA-256"}
    onPress={verify} disabled={busy||mode!=="SIMULATION"||stage!=="proven"}
   /></View>
  </Card>
  <Card style={{marginTop:13}}>
    <Row><Txt color={C.muted} size={11}>SAFETY & AUTHORITY</Txt><Tag label="READ ONLY" kind="green"/></Row>
    {[
      ["Runtime / Resume","ACP owns execution"],
      ["Contract & chain","Monad Testnet · 10143"],
      ["Pinned RPC",RPC_ID],
      ["Reviewed deployment","NOT REVIEWED"],
      ["Mainnet/Testnet acceptance","BLOCKED"],
      ["Service correctness","NOT PROVEN"],
    ].map(([key,value])=><Detail label={key} value={value} key={key}/>)}
  </Card>
 </View>);
 const journalView=(
 <View>
  <Txt color={C.muted} weight="700" size={11} style={{letterSpacing:1.5}}>AUDIT TRAIL / THIS SESSION</Txt>
  <Txt size={29} weight="800" style={{marginTop:8}}>操作记录</Txt>
  <Txt color={C.muted} size={12} style={{marginTop:10,lineHeight:20}}>仅当前会话中的本地浏览记录。没有创建链上审计日志，也没有把私钥或签名保存在设备上。</Txt>
  <Card style={{marginTop:20}}>
    <Row><Txt weight="700">summary.json</Txt><Tag label="LOCAL ONLY" kind="neutral"/></Row>
    <Txt color={C.muted} size={12} style={{lineHeight:20,marginTop:12}}>
      包含 taskId / descriptionHash、链信息、Recorder 声明、PaymentUnknown 和 Evidence 结果，明确标识模拟或只读来源。
    </Txt>
    <View style={{marginTop:18}}><Button label={Platform.OS==="web"?"下载 summary.json":"分享只读 Summary"} onPress={()=>void shareSummary()} disabled={!task}/></View>
  </Card>
  <Txt color={C.muted} weight="700" size={12} style={S.sectionTitle}>近期操作</Txt>
  {events.map((e,i)=><View style={S.event} key={i}>
    <View style={S.eventBullet}/>
    <View style={{flex:1}}><Txt size={12}>{e}</Txt><Txt color={C.quiet} size={10} style={{marginTop:5}}>In-memory demo journal</Txt></View>
  </View>)}
 </View>);
 const judge=(
 <View style={S.judge}>
  <Txt color={C.blue} weight="800" size={11} style={{letterSpacing:2}}>VERIFICATION / JUDGE VIEW</Txt>
  <Txt weight="800" size={32} style={{marginTop:15,letterSpacing:-1}}>Every claim has a source.</Txt>
  <Txt color={C.muted} size={13} style={{marginTop:13,lineHeight:23}}>左侧是一套 Expo 代码生成的 iOS / Web 用户界面。右侧可独立检查演示来源、付款语义和证据边界。</Txt>
  <View style={{marginTop:24,gap:12}}>
   <Card><Row><Txt color={C.muted} size={12}>Current source</Txt><Tag label={mode==="SIMULATION"?"SIMULATED":"READ ONLY"} kind="yellow"/></Row>
     <Detail label="Chain ID" value={String(CHAIN_ID)}/>
     <Detail label="Contract" value={cut(task?.contract,15)}/>
     <Detail label="Pinned RPC identity" value={RPC_ID}/>
     <Detail label="RPC contacted by simulation" value={mode==="SIMULATION"?"NO":"TASK READ ONLY"}/>
   </Card>
   <Card><Txt color={C.muted} size={11} weight="800">INDEPENDENT FACTS</Txt>
     <Detail label="Ledger status" value={task?.status||"UNKNOWN"}/>
     <Detail label="Payment receipt" value="NOT INDEPENDENTLY VERIFIED"/>
     <Detail label="EIP-3009 nonce" value="UNKNOWN"/>
     <Detail label="Evidence digest" value={proofMatched?"LOCAL SHA-256 MATCH":"NOT VERIFIED"}/>
     <View style={[S.notice,{marginTop:16}]}><Txt color={C.amber} size={12} weight="700">Public Testnet acceptance BLOCKED</Txt></View>
   </Card>
   <Card><Txt color={C.muted} size={11} weight="800">NEGATIVE PROOF</Txt>
     {rejects.map(e=><Row key={e.kind} style={{marginTop:14}}>
       <Txt size={12}>{e.kind.replace("_"," ")}</Txt><Tag label={e.result} kind="green"/>
     </Row>)}
     <Txt color={C.quiet} size={11} style={{marginTop:16,lineHeight:18}}>Matched task IDs cannot redeem another Ledger's settlement, nor a different payer's authorization.</Txt>
   </Card>
   <Button label="Export local summary.json" secondary onPress={()=>void shareSummary()} disabled={!task}/>
  </View>
  <Txt color={C.quiet} size={11} style={{marginTop:26,lineHeight:19}}>Pocket-inspired UX, independently implemented. No Pocket trading/wallet code copied. No Mainnet/Testnet paid demo claimed.</Txt>
 </View>);
 const main=(
 <View style={[S.phone,wide&&S.phoneWide]}>
  {header}
  <ScrollView keyboardShouldPersistTaps="handled"
    style={{flex:1,width:"100%"}}
    contentContainerStyle={{width:"100%",paddingHorizontal:21,paddingBottom:125,flexGrow:1}}
    showsVerticalScrollIndicator={false}>
    {tab==="Home"?home:tab==="Tasks"?tasks:tab==="Proof"?proof:journalView}
    {!!error&&<View style={[S.notice,{borderColor:C.red,marginTop:18}]}><Txt color={C.red} size={12}>{error}</Txt></View>}
  </ScrollView>
  <View style={S.bottomBar}>
   {TABS.map((item,i)=><Pressable accessibilityRole="button" accessibilityLabel={item}
     accessibilityState={{selected:tab===item}}
     style={[S.tab,tab===item&&S.tabActive]} onPress={()=>setTab(item)} key={item}>
      <Txt color={tab===item?C.accent:C.quiet} size={19}>{["⌂","▤","◇","≡"][i]}</Txt>
      <Txt color={tab===item?C.white:C.quiet} size={10} weight="700">{item}</Txt>
   </Pressable>)}
  </View>
 </View>);
 return <SafeAreaProvider><SafeAreaView style={S.root}>
  <StatusBar style="light"/>
  <View style={[S.layout,wide&&{justifyContent:"center",paddingHorizontal:28}]}>
   <View style={wide?{width:438,flexShrink:0,height:"100%"}:{flex:1,width:"100%"}}>
     {entered?main:welcome}
   </View>
   {wide&&<ScrollView contentContainerStyle={{padding:38,paddingBottom:65,maxWidth:580}} style={{flex:1}}>
    {judge}
   </ScrollView>}
  </View>
 </SafeAreaView></SafeAreaProvider>;
}
const S=StyleSheet.create({
 root:{flex:1,backgroundColor:C.background},
 layout:{flex:1,flexDirection:"row",alignItems:"stretch",maxWidth:1320,alignSelf:"center",width:"100%"},
 phone:{flex:1,width:"100%",backgroundColor:C.background},
 phoneWide:{borderLeftWidth:1,borderRightWidth:1,borderColor:C.line},
 welcome:{flex:1,paddingHorizontal:28,paddingTop:34,paddingBottom:30,justifyContent:"space-between"},
 eyebrowWrap:{flexDirection:"row",alignItems:"center",gap:9},
 dot:{width:7,height:7,borderRadius:5,backgroundColor:C.accent},
 welcomeOrb:{height:210,marginTop:32,alignItems:"center",justifyContent:"center"},
 orbit:{width:180,height:180,borderRadius:90,borderWidth:1,borderColor:"#6078AA",backgroundColor:"#1F2B42",alignItems:"center",justifyContent:"center",
   shadowColor:C.blue,shadowOpacity:.13,shadowRadius:40},
 welcomeFoot:{alignItems:"center",marginTop:24},
 headerIcon:{height:37,width:37,borderRadius:12,borderWidth:1,borderColor:C.line,alignItems:"center",justifyContent:"center"},
 sectionSpace:{marginBottom:22},
 button:{minHeight:52,borderRadius:14,paddingHorizontal:18,flexDirection:"row",alignItems:"center",justifyContent:"space-between"},
 buttonPrimary:{backgroundColor:C.accent},
 buttonSecondary:{backgroundColor:C.panel2,borderWidth:1,borderColor:C.line},
 card:{backgroundColor:C.panel,borderWidth:1,borderColor:C.line,borderRadius:20,padding:19},
 tag:{paddingHorizontal:10,paddingVertical:7,borderRadius:7},
 mono:{fontFamily:Platform.select({ios:"Menlo",android:"monospace",default:"monospace"}),letterSpacing:-.2},
 detail:{flexDirection:"row",alignItems:"flex-start",justifyContent:"space-between",gap:9,borderBottomWidth:1,borderColor:"#283242",paddingVertical:13},
 sectionTitle:{marginTop:27,marginBottom:16,letterSpacing:1},
 stepRow:{flexDirection:"row",alignItems:"center",gap:15,paddingVertical:16,borderBottomWidth:1,borderBottomColor:C.line},
 stepNumber:{width:44,height:44,borderRadius:13,backgroundColor:C.panel2,alignItems:"center",justifyContent:"center"},
 toggle:{backgroundColor:"#1B2434",padding:4,borderRadius:13,flexDirection:"row",marginTop:19,marginBottom:15},
 toggleItem:{flex:1,alignItems:"center",paddingVertical:12,borderRadius:10},
 toggleActive:{backgroundColor:"#364661"},
 scenario:{flexDirection:"row",alignItems:"center",gap:12,borderWidth:1,borderColor:C.line,backgroundColor:C.panel,borderRadius:13,padding:13},
 scenarioActive:{borderColor:C.blue,backgroundColor:"#202D42"},
 scenarioIndex:{width:40,height:40,borderRadius:11,backgroundColor:"#273148",alignItems:"center",justifyContent:"center"},
 borderTop:{borderTopWidth:1,borderTopColor:C.line,paddingTop:16,marginTop:15},
 input:{color:C.white,backgroundColor:C.panel2,borderColor:C.line,borderWidth:1,borderRadius:10,padding:13,fontSize:12,marginTop:13},
 notice:{backgroundColor:"#322B25",borderColor:"#51422F",borderWidth:1,borderRadius:12,padding:14,marginTop:18},
 bottomBar:{height:72,borderTopWidth:1,borderTopColor:C.line,flexDirection:"row",paddingHorizontal:14,backgroundColor:"#111826"},
 tab:{flex:1,alignItems:"center",justifyContent:"center",gap:4,borderRadius:12,marginVertical:8},
 tabActive:{backgroundColor:"#273247"},
 event:{flexDirection:"row",gap:12,borderBottomWidth:1,borderColor:C.line,paddingVertical:16},
 eventBullet:{backgroundColor:C.blue,width:7,height:7,borderRadius:4,marginTop:5},
 judge:{paddingTop:20},
});
