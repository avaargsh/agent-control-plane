// SPDX-License-Identifier: Apache-2.0
// Decode strict UTF-8 even on Expo SDK 57 Hermes where TextDecoder
// is not guaranteed. Reject overlong, surrogate and malformed sequences.
export function strictUtf8(bytes:Uint8Array):string{
  const chunks:string[]=[];let buffer:number[]=[];
  for(let i=0;i<bytes.length;){
    const b=bytes[i++];let code=0,extra=0,min=0;
    if(b<=0x7f)code=b;
    else if(b>=0xc2&&b<=0xdf){code=b&31;extra=1;min=128;}
    else if(b>=0xe0&&b<=0xef){code=b&15;extra=2;min=2048;}
    else if(b>=0xf0&&b<=0xf4){code=b&7;extra=3;min=65536;}
    else throw Error("INVALID_UTF8");
    if(extra){
      if(i+extra>bytes.length)throw Error("INVALID_UTF8");
      for(let j=0;j<extra;j++){const part=bytes[i++];if((part&0xc0)!==0x80)throw Error("INVALID_UTF8");code=(code<<6)|(part&63);}
      if(code<min||code>0x10ffff||(code>=0xd800&&code<=0xdfff))throw Error("INVALID_UTF8");
    }
    if(code<=0xffff)buffer.push(code);
    else{code-=0x10000;buffer.push(0xd800+(code>>10),0xdc00+(code&0x3ff));}
    if(buffer.length>4096){chunks.push(String.fromCharCode(...buffer));buffer=[];}
  }
  if(buffer.length)chunks.push(String.fromCharCode(...buffer));
  const text=chunks.join("");
  return text.charCodeAt(0)===0xfeff?text.slice(1):text;
}
