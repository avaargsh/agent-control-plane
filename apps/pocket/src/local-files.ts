// SPDX-License-Identifier: Apache-2.0
// The picker must start synchronously with a real user interaction on Web.
import * as DocumentPicker from "expo-document-picker";
import {File} from "expo-file-system";
import {FILE_LIMITS,readPickedDocument,DocumentKind,PickedLocalFile} from "./file-contract";
export async function pickLocalDocument(kind:DocumentKind):Promise<PickedLocalFile|null>{
  const requested=DocumentPicker.getDocumentAsync({
    type:kind==="evidence"?"application/json":"*/*",
    multiple:false,copyToCacheDirectory:true,base64:false
  });
  return readPickedDocument(kind,()=>requested,async asset=>{
    if(asset.file)return new Uint8Array(await asset.file.arrayBuffer());
    // Some Expo Web picker versions expose only a local object URL.
    // A blob: URL is an in-memory object, not an external HTTP request.
    if(asset.uri.startsWith("blob:")){
      const response=await fetch(asset.uri);
      if(!response.ok)throw Error("LOCAL_BLOB_UNREADABLE");
      return new Uint8Array(await response.arrayBuffer());
    }
    if(!asset.uri.startsWith("file://")&&!asset.uri.startsWith("content://"))
      throw Error("LOCAL_FILE_URI_REQUIRED");
    const file=new File(asset.uri);
    if(file.size>FILE_LIMITS[kind])throw Error("FILE_TOO_LARGE_"+kind.toUpperCase());
    return file.bytes();
  });
}
