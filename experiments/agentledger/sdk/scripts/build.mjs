// SPDX-License-Identifier: Apache-2.0
// Bundle the already-tested source modules; do not fork their semantics.
import { build } from "esbuild";
import { mkdir } from "node:fs/promises";
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
console.log("Built experimental read-only SDK; no payment signing or execution exports");
