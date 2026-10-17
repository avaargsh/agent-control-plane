// Discover desktop MetaMask without assuming ownership of window.ethereum.
export function createWalletConnection(host, onChange = () => {}, {
  onRequest = () => {}, readTimeoutMs = 12_000, promptReminderMs = 8_000,
} = {}) {
  const readMethods = new Set(['eth_chainId', 'eth_accounts', 'eth_getTransactionCount',
    'eth_getTransactionReceipt', 'eth_getTransactionByHash', 'eth_getBlockByNumber',
    'eth_getCode', 'eth_call', 'eth_estimateGas', 'eth_gasPrice', 'eth_getBalance']);
  let announced = null;
  let subscribed = null;
  const changed = () => onChange();
  function getProvider() {
    const legacy = host.ethereum;
    const provider = announced || legacy?.providers?.find(item => item.isMetaMask && !item.isPhantom)
      || (legacy?.isMetaMask && !legacy.isPhantom ? legacy : null);
    if (provider && typeof provider.request === 'function') {
      if (subscribed !== provider) {
        subscribed?.removeListener?.('accountsChanged', changed);
        subscribed?.removeListener?.('chainChanged', changed);
        subscribed?.removeListener?.('disconnect', changed);
        subscribed = provider;
        provider.on?.('accountsChanged', changed);
        provider.on?.('chainChanged', changed);
        provider.on?.('disconnect', changed);
      }
      return provider;
    }
    return null;
  }
  host.addEventListener('eip6963:announceProvider', event => {
    if (event.detail?.info?.rdns !== 'io.metamask' || typeof event.detail.provider?.request !== 'function') return;
    // Keep the selected provider stable once a wallet session has started.
    if (!announced) {
      announced = event.detail.provider;
      getProvider();
      onChange();
    }
  });
  host.addEventListener('ethereum#initialized', changed);
  function discover() { host.dispatchEvent(new Event('eip6963:requestProvider')); return getProvider(); }
  async function request(method, params = []) {
    const provider = getProvider() || discover();
    if (!provider) throw Error('此浏览器未检测到 MetaMask 扩展。请查看下方连接说明；手机钱包暂不支持扫码连接。');
    onRequest({ method, status: 'started' });
    let timer;
    try {
      // Only read-only calls have a deadline. A signature may still be pending
      // inside MetaMask, so it must never be cancelled or retried by a UI timer.
      const pending = Promise.resolve(provider.request({ method, params }));
      let result;
      if (readMethods.has(method)) {
        result = await Promise.race([pending, new Promise((_, reject) => {
          timer = setTimeout(() => reject(Object.assign(
            Error(`钱包查询 ${method} 超时，尚未据此发送交易。请检查 MetaMask 网络连接后重新检查。`),
            { code: 'WALLET_RPC_TIMEOUT' })), readTimeoutMs);
        })]);
      } else {
        timer = setTimeout(() => onRequest({ method, status: 'waiting' }), promptReminderMs);
        result = await pending;
      }
      onRequest({ method, status: 'completed' });
      return result;
    } catch (error) {
      onRequest({ method, status: 'failed', message: error.message, code: error.code });
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function switchToTestnet() {
    try {
      await request('wallet_switchEthereumChain', [{ chainId: '0x279f' }]);
    } catch (error) {
      if ((error.code ?? error.data?.originalError?.code) !== 4902) throw error;
      await request('wallet_addEthereumChain', [{ chainId: '0x279f', chainName: 'Monad Testnet',
        nativeCurrency: { name: 'MON', symbol: 'MON', decimals: 18 },
        rpcUrls: ['https://testnet-rpc.monad.xyz'], blockExplorerUrls: ['https://testnet.monadscan.com'] }]);
      await request('wallet_switchEthereumChain', [{ chainId: '0x279f' }]);
    }
    if (await request('eth_chainId') !== '0x279f') throw Error('钱包尚未切换到 Monad 测试网，请检查钱包中的网络。');
  }
  return { getProvider, discover, request, switchToTestnet };
}
