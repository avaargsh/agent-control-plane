// SPDX-License-Identifier: Apache-2.0
// Local-only document picker. Assets are never uploaded. Strict cap prevents
// unbounded memory work on mobile while hashing raw binary input/output.
import {strictUtf8} from "./utf8";
export type DocumentKind="evidence"|"input"|"output";
export type PickedLocalFile={kind:DocumentKind;name:string;bytes:Uint8Array};
export const FILE_LIMITS={evidence:2*1024*1024,input:10*1024*1024,output:10*1024*1024} as const;
type PickerAsset={name:string;size?:number;uri:string;file?:{arrayBuffer():Promise<ArrayBuffer>}};
type PickerResult={canceled:boolean;assets:PickerAsset[]|null};
type Picker=()=>Promise<PickerResult>;
type ReadFile=(asset:PickerAsset)=>Promise<Uint8Array>;

export async function readPickedDocument(kind:DocumentKind,pick:Picker,read:ReadFile):Promise<PickedLocalFile|null>{
  const result=await pick();
  if(result.canceled)return null;
  const asset=result.assets?.[0];
  if(!asset||typeof asset.name!=="string")throw Error("FILE_PICKER_MISSING_ASSET");
  const max=FILE_LIMITS[kind];
  if(typeof asset.size==="number"&&asset.size>max)throw Error("FILE_TOO_LARGE_"+kind.toUpperCase());
  // The reader only accepts a local copied asset, never an untrusted URL
  // as a network download. Limits are rechecked after read.
  const bytes=await read(asset);
  if(!(bytes instanceof Uint8Array))throw Error("FILE_READ_INVALID");
  if(bytes.byteLength>max)throw Error("FILE_TOO_LARGE_"+kind.toUpperCase());
  return {kind,name:asset.name,bytes};
}

export function parseEvidenceBytes(bytes:Uint8Array):unknown{
 if(bytes.byteLength>FILE_LIMITS.evidence)throw Error("FILE_TOO_LARGE_EVIDENCE");
 return JSON.parse(strictUtf8(bytes));
}
