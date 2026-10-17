// SPDX-License-Identifier: Apache-2.0
// Inspect *actual npm pack manifest*, not a successful-looking dry run.
import {spawnSync} from "node:child_process";
import {readFileSync} from "node:fs";
const pkg=JSON.parse(readFileSync(new URL("../package.json",import.meta.url),"utf8"));
if(pkg.private!==true||pkg.version!=="0.2.0-alpha.3"||
   pkg.exports?.["."]?.types!=="./dist/index.d.mts" ||
   pkg.exports?.["."]?.default!=="./dist/index.mjs")
  throw Error("Invalid SDK public package manifest or publication gate");
const run=spawnSync("npm",["pack","--dry-run","--json","--ignore-scripts"],{
  cwd:new URL("../",import.meta.url),encoding:"utf8",
  env:{...process.env,npm_config_update_notifier:"false"},
});
if(run.status!==0)throw Error("npm pack inspection failed: "+run.stderr.slice(0,1000));
const metadata=JSON.parse(run.stdout);
const files=new Set(metadata[0].files.map(file=>file.path));
for(const required of ["dist/index.mjs","dist/index.d.mts","README.md","package.json"]){
  if(!files.has(required))throw Error("SDK package missing "+required);
}
for(const entry of files){
  if(entry.startsWith("src/")||entry.startsWith("test/")||
     entry.includes("node_modules")||entry.includes("fixture")||
     entry.startsWith("scripts/")||entry.startsWith("phase0/"))
    throw Error("Private or test code leaked into SDK npm tarball");
}
if(metadata[0].version!==pkg.version||metadata[0].name!==pkg.name)
  throw Error("Pack metadata differs from SDK manifest");
console.log("SDK PACK CONTRACT PASS",JSON.stringify({
  name:pkg.name,version:pkg.version,publicExports:Object.keys(pkg.exports),
  files:[...files].sort(),private:true
}));
