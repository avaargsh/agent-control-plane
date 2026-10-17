// SPDX-License-Identifier: Apache-2.0
// Bundle the already-tested source modules; do not fork their semantics.
import { build } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const root=new URL("../",import.meta.url);
await mkdir(new URL("dist/",root),{recursive:true});
await build({
  entryPoints:[fileURLToPath(new URL("src/index.mjs",root))],
  outfile:fileURLToPath(new URL("dist/index.mjs",root)),
  bundle:true,
  platform:"node",
  format:"esm",
  target:"node22",
  external:["viem","viem/*"],
  legalComments:"inline",
  logLevel:"warning",
});
await copyFile(new URL("src/index.d.mts",root),new URL("dist/index.d.mts",root));
console.log("Built private typed read-only SDK with stable v1 contract; no payment signing or execution exports");
