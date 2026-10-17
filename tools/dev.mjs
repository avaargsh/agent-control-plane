#!/usr/bin/env node
// One foreground process owns only the two children it starts.
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = new URL('../', import.meta.url);
const scripts = ['examples/verifier/server.mjs', 'examples/verifier/tools/serve-testnet-deploy.mjs'];
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
}
for (const script of scripts) {
  const child = spawn(process.execPath, [fileURLToPath(new URL(script, root))], { cwd: fileURLToPath(root), stdio: 'inherit' });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) stop(code || 1); });
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
