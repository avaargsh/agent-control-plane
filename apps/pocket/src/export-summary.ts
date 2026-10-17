// SPDX-License-Identifier: Apache-2.0
// On-device only: Web triggers a local download; native writes a temporary
// JSON file and opens OS share sheet. No uploads, signers or RPC requests.
import {Platform} from "react-native";
import {serializeSummaryJson} from "./summary-json";
export async function saveSummaryJson(report:unknown):Promise<void>{
  const body=serializeSummaryJson(report);
  if(Platform.OS==="web"){
    const url=URL.createObjectURL(new Blob([body],{type:"application/json"}));
    try{
      const link=document.createElement("a");
      link.href=url;
      link.download="summary.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
    }finally{setTimeout(()=>URL.revokeObjectURL(url),1000);}
    return;
  }
  const [{File,Paths},Sharing]=await Promise.all([
    import("expo-file-system"),import("expo-sharing")
  ]);
  if(!await Sharing.isAvailableAsync())throw Error("NATIVE_SHARING_UNAVAILABLE");
  const destination=new File(Paths.cache,"summary.json");
  destination.create({overwrite:true});
  await destination.write(body);
  await Sharing.shareAsync(destination.uri,{
    mimeType:"application/json",dialogTitle:"AgentLedger summary.json"
  });
}
