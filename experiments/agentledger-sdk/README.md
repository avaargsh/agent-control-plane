# AgentLedger SDK — Monad Testnet Pay → Execute → Prove
#
# **Experimental monorepo; NOT ACP v0.1 protocol; NOT production-ready.**
#
# This SDK is a structured extraction of the Monad Testnet payment experiment
# formerly under `experiments/agentledger/`. It is deliberately outside
# `src/`, `release/`, and the frozen ACP v0.1 acceptance contract.
#
# Network: Monad Testnet (chainId 10143; CAIP-2 eip155:10143). Mainnet is
# forbidden. No real funds, no escrow, no refunds, no disputes.
#
# ## Packages
#
# | Package | Language | Role |
# |---------|----------|------|
# | @agentledger/core | TypeScript | Shared types, constants, EIP-712 encoding |
# | @agentledger/evidence | TypeScript | agentledger.evidence.v1 create / hash / verify |
# | @agentledger/settlement | TypeScript | Read-only USDC Transfer + EIP-3009 receipt verification |
# | @agentledger/journal | Python | SQLite admission + pre-charge journals |
# | @agentledger/x402 | TypeScript | Express x402 HTTP 402 payment middleware |
# | @agentledger/verifier | TypeScript | Read-only chain task + evidence browser verifier |
# | @agentledger/phase0 | TypeScript | Phase-0 read-only status projection |
#
# ## Install
#
#   cd experiments/agentledger-sdk
#   npm ci
#
# ## Test
#
#   npm test                    # Node packages
#   npm run build               # TypeScript compile
#   # Python journals (separate venv):
#   cd packages/agentledger-journal-py && python3 -m unittest discover -s tests -v
#
# ## Governance
#
# - Default chainId is 10143; RPC must be HTTPS or loopback only.
# - Read-only by default; writes require explicit signer and are never automatic.
# - SDK observes events only; it does not claim finality or ACP execution ownership.
#
# See docs/sdk-governance.md for the full compatibility and safety policy.
