import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { toFunctionSelector } from 'viem';
import { createWalletConnection } from '../tools/wallet-connection.mjs';
import { createTransactionJournal, prepareTask, readTaskCreation, verifyTransaction } from '../tools/testnet-transactions.mjs';

const FROM = '0x' + '12'.repeat(20);
const source = readFileSync(new URL('../tools/deploy.mjs', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '');
const artifact = { bytecode: '0x6000', sourceSha256: 'fixture', compiler: 'test',
  createTaskSelector: toFunctionSelector('createTask(bytes32,bytes32,uint256)') };

function provider({ chain = '0x279f', request: custom } = {}) {
  const calls = [];
  const handlers = new Map();
  const instance = { isMetaMask: true, calls,
    on(name, fn) { handlers.set(name, fn); },
    removeListener(name) { handlers.delete(name); },
    emit(name) { handlers.get(name)?.(); },
    async request({ method, params }) {
      calls.push({ method, params });
      if (custom) { const result = await custom(method, params); if (result !== undefined) return result; }
      if (method === 'eth_chainId') return chain;
      if (method === 'wallet_switchEthereumChain') { chain = '0x279f'; instance.emit('chainChanged'); return null; }
      if (method === 'eth_accounts' || method === 'eth_requestAccounts') return [FROM];
      if (method === 'eth_estimateGas') return '0x100000';
      if (method === 'eth_gasPrice') return '0x174876e800';
      if (method === 'eth_getBalance') return '0xde0b6b3a7640000';
      throw Error('Unexpected RPC: ' + method);
    },
  };
  return instance;
}

async function page(wallet, { announced = false, preparedRequest = null, stored = {} } = {}) {
  const host = new EventTarget();
  host.location = { hash: preparedRequest ? '#task=prepared' : '' };
  if (announced) {
    host.addEventListener('eip6963:requestProvider', () => host.dispatchEvent(new CustomEvent('eip6963:announceProvider', {
      detail: { info: { rdns: 'io.metamask' }, provider: wallet },
    })));
  } else { host.ethereum = wallet; }
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { textContent: '', value: '', hidden: false, disabled: ['prepare', 'deploy'].includes(id),
      addEventListener() {}, setAttribute() {}, removeAttribute() {}, classList: { toggle() {} } });
    return elements.get(id);
  };
  const saved = new Map(Object.entries(stored));
  const context = { window: host, document: { getElementById: element }, Event, TextEncoder, URL, URLSearchParams, console, setTimeout,
    fetch: async path => ({ ok: true, json: async () => path === '/prepared-task.json' ? preparedRequest : artifact }),
    localStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key) }, createWalletConnection,
    createTransactionJournal, prepareTask, readTaskCreation, verifyTransaction,
    // The VM harness strips imports; localization is a DOM-only concern, not a wallet RPC action.
    initLanguage:()=>({language:"zh"}) };
  await vm.runInNewContext('(async () => {\n' + source + '\n})()', context);
  return { host, element, saved };
}

test('explicit prepared-task handoff restores the deployment and input without signing', async () => {
  const draft = await prepareTask(artifact, 'Prepared input\n', '1000');
  const wallet = provider();
  const preparedRequest = { schemaVersion: 'agentledger.prepared-testnet-task.v1', chainId: 10143,
    from: FROM, contract: '0x' + '34'.repeat(20), ...draft,
    deployment: { chainId: 10143, from: FROM, data: '0x6000', paymentRecorder: FROM, transactionHash: '0x' + '56'.repeat(32) } };
  const { element, saved } = await page(wallet, { preparedRequest });
  assert.equal(element('task-input').value, 'Prepared input\n');
  assert.match(element('task-estimate').textContent, new RegExp(draft.taskId));
  assert.equal(saved.size, 1);
  assert.equal(wallet.calls.some(x => x.method === 'eth_sendTransaction'), false);
  const bad = await page(wallet, { preparedRequest: { ...preparedRequest, input: 'tampered' } });
  assert.match(bad.element('task-status').textContent, /不一致/);
  assert.equal(bad.saved.size, 0);
});

test('submitted task displays its own transaction hash and explorer link on reload', async () => {
  const draft = await prepareTask(artifact, 'Saved input', '1000');
  const hash = '0x' + '56'.repeat(32);
  const record = { ...draft, chainId: 10143, from: FROM, to: '0x' + '34'.repeat(20), transactionHash: hash, status: 'SUBMITTED' };
  const { element } = await page(provider(), { stored: { 'agentledger.monad-testnet.deployment.v1.task': JSON.stringify(record) } });
  assert.equal(element('task-created-hash').value, hash);
  assert.equal(element('task-explorer').href, 'https://testnet.monadscan.com/tx/' + hash);
  assert.equal(element('task-explorer').hidden, false);
});

test('reopening prepared task resumes its matching stored transaction instead of reporting a conflict', async () => {
  const draft = await prepareTask(artifact, 'Saved input', '1000');
  const contract = '0x' + '34'.repeat(20), hash = '0x' + '56'.repeat(32);
  const deployment = { chainId: 10143, from: FROM, data: '0x6000', paymentRecorder: FROM, transactionHash: '0x' + '78'.repeat(32) };
  const record = { ...draft, chainId: 10143, from: FROM, to: contract, transactionHash: hash, status: 'SUBMITTED' };
  const preparedRequest = { schemaVersion: 'agentledger.prepared-testnet-task.v1', chainId: 10143, from: FROM, contract, ...draft, deployment };
  const { element } = await page(provider(), { preparedRequest, stored: {
    'agentledger.monad-testnet.deployment.v1': JSON.stringify(deployment),
    'agentledger.monad-testnet.deployment.v1.task': JSON.stringify(record),
  } });
  assert.match(element('task-status').textContent, /已恢复此测试任务/);
  assert.equal(element('task-created-hash').value, hash);
  assert.equal(element('create-task').disabled, true);
});

test('EIP-6963 MetaMask connects even when window.ethereum is absent', async () => {
  const wallet = provider();
  const { element } = await page(wallet, { announced: true });
  assert.equal(element('connect').disabled, false);
  await element('connect').onclick();
  assert.match(element('account').textContent, new RegExp(FROM));
  assert.equal(element('prepare').disabled, false);
  assert.equal(wallet.calls.some(x => x.method === 'eth_sendTransaction'), false);
});

test('missing extension shows actionable desktop and remote access guidance without signing', async () => {
  const { element } = await page(null);
  assert.equal(element('wallet-help').hidden, false);
  assert.equal(element('connect').disabled, true);
  assert.equal(element('deploy').disabled, true);
  const html = readFileSync(new URL('../tools/deploy.html', import.meta.url), 'utf8');
  assert.match(html, /转发端口 4322/);
  assert.match(html, /metamask.io\/download/);
});

test('legacy multi-wallet injection selects MetaMask rather than the default wallet', async () => {
  const wallet = provider();
  const other = { request() { throw Error('Wrong wallet selected'); } };
  const host = new EventTarget(); host.ethereum = { ...other, providers: [other, wallet] };
  const connection = createWalletConnection(host);
  assert.equal(connection.getProvider(), wallet);
  assert.equal(await connection.request('eth_chainId'), '0x279f');
});

test('a wallet announced after page initialization can be detected and connected', async () => {
  const { host, element } = await page(null);
  host.dispatchEvent(new CustomEvent('eip6963:announceProvider', { detail: { info: { rdns: 'io.metamask' }, provider: provider() } }));
  assert.equal(element('connect').disabled, false);
  await element('connect').onclick();
  assert.match(element('account').textContent, new RegExp(FROM));
});

test('wrong chain cannot prepare a deployment and explicit switch restores connection', async () => {
  const wallet = provider({ chain: '0x1' });
  const { element } = await page(wallet);
  await element('connect').onclick();
  assert.match(element('account').textContent, new RegExp(FROM), 'authorized address must remain visible on the wrong chain');
  assert.match(element('status').textContent, /10143/);
  assert.equal(element('prepare').disabled, true);
  await element('switch-network').onclick();
  await element('connect').onclick();
  assert.equal(element('prepare').disabled, false);
  assert.equal(wallet.calls.some(x => x.method === 'eth_sendTransaction'), false);
});

test('connection displays the address returned by authorization without re-reading eth_accounts', async () => {
  const wallet = provider({ request(method) { if (method === 'eth_accounts') return []; } });
  const { element } = await page(wallet);
  await element('connect').onclick();
  assert.match(element('account').textContent, new RegExp(FROM));
  assert.equal(element('recorder').value, FROM);
  assert.equal(wallet.calls.some(x => x.method === 'eth_accounts'), false);
  // Spending still requires a fresh account check: a revoked permission must block it.
  await element('prepare').onclick();
  assert.equal(element('deploy').disabled, true);
});

test('empty authorization result explains account permission rather than silently losing the address', async () => {
  const wallet = provider({ request(method) { if (method === 'eth_requestAccounts') return []; } });
  const { element } = await page(wallet);
  await element('connect').onclick();
  assert.match(element('status').textContent, /未返回可用账户/);
  assert.equal(element('prepare').disabled, true);
});

test('unknown network is added with pinned Testnet configuration before switching', async () => {
  let first = true;
  const wallet = provider({ chain: '0x1', request(method) {
    if (method === 'wallet_switchEthereumChain' && first) { first = false; throw Object.assign(Error('unknown chain'), { code: 4902 }); }
    if (method === 'wallet_addEthereumChain') return null;
  } });
  const host = new EventTarget(); host.ethereum = wallet;
  await createWalletConnection(host).switchToTestnet();
  const config = wallet.calls.find(x => x.method === 'wallet_addEthereumChain').params[0];
  assert.equal(config.chainId, '0x279f');
  assert.deepEqual(config.rpcUrls, ['https://testnet-rpc.monad.xyz']);
});

test('pending MetaMask request explains where the user should resolve it', async () => {
  const wallet = provider({ request(method) { if (method === 'eth_requestAccounts') throw Object.assign(Error('pending'), { code: -32002 }); } });
  const { element } = await page(wallet);
  await element('connect').onclick();
  assert.match(element('status').textContent, /已有等待处理的请求/);
  assert.equal(element('connect').disabled, false);
});

test('a stalled read-only wallet RPC times out with its method and never requests signing', async () => {
  const events = [];
  const host = new EventTarget();
  const wallet = provider({ request(method) { if (method === 'eth_chainId') return new Promise(() => {}); } });
  host.ethereum = wallet;
  const connection = createWalletConnection(host, () => {}, { readTimeoutMs: 10, onRequest: event => events.push(event) });
  await assert.rejects(() => connection.request('eth_chainId'), error => error.code === 'WALLET_RPC_TIMEOUT' && /eth_chainId/.test(error.message));
  assert.equal(events[0].status, 'started');
  assert.equal(events.at(-1).status, 'failed');
  assert.equal(wallet.calls.some(x => x.method === 'eth_sendTransaction'), false);
});

test('a delayed wallet signature shows a reminder without timing out or retrying', async () => {
  let resolveSignature;
  const events = [], host = new EventTarget();
  const wallet = provider({ request(method) { if (method === 'eth_sendTransaction') return new Promise(resolve => { resolveSignature = resolve; }); } });
  host.ethereum = wallet;
  const connection = createWalletConnection(host, () => {}, { readTimeoutMs: 5, promptReminderMs: 10, onRequest: event => events.push(event) });
  const pending = connection.request('eth_sendTransaction', [{}]);
  await new Promise(resolve => setTimeout(resolve, 25));
  assert(events.some(x => x.status === 'waiting' && x.method === 'eth_sendTransaction'));
  assert.equal(wallet.calls.filter(x => x.method === 'eth_sendTransaction').length, 1);
  resolveSignature('0x' + '56'.repeat(32));
  assert.equal(await pending, '0x' + '56'.repeat(32));
});

test('account change during gas estimation cannot re-enable a stale deployment', async () => {
  let release;
  const wallet = provider({ request(method) {
    if (method === 'eth_estimateGas') return new Promise(resolve => { release = resolve; });
  } });
  const { element } = await page(wallet);
  await element('connect').onclick();
  const preparing = element('prepare').onclick();
  while (!release) await new Promise(resolve => setTimeout(resolve, 0));
  wallet.emit('accountsChanged'); release('0x100000');
  await preparing;
  assert.equal(element('deploy').disabled, true);
  assert.match(element('status').textContent, /已变化/);
});

test('account change invalidates a prepared transaction and requires reconnection', async () => {
  const wallet = provider();
  const { element } = await page(wallet);
  await element('connect').onclick(); await element('prepare').onclick();
  assert.equal(element('deploy').disabled, false);
  wallet.emit('accountsChanged');
  assert.equal(element('deploy').disabled, true);
  assert.equal(element('prepare').disabled, true);
});
