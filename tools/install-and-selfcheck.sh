#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# AgentLedger 一键环境自检脚本
#
# 功能：
#   1. 检查/安装 Foundry（forge / cast / anvil / chisel）
#   2. 检查 Node.js >= 22 与 Python 3 + pytest
#   3. 安装 npm 依赖（npm ci --ignore-scripts --no-audit --no-fund）
#   4. 运行全部测试层：Foundry / Node / SDK / Python / Pocket / Browser / 就绪检查
#   5. 输出汇总报告，任何一项失败则返回非零退出码
#
# 用法：
#   bash tools/install-and-selfcheck.sh
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

FOUNDRY_DIR="$HOME/.foundry/bin"
FOUNDRY_BIN="$FOUNDRY_DIR/foundryup"
export PATH="$PATH:$FOUNDRY_DIR"

# 颜色输出
C_RESET='\033[0m'
C_RED='\033[0;31m'
C_GREEN='\033[0;32m'
C_YELLOW='\033[1;33m'
C_BLUE='\033[0;34m'

ok()   { printf "${C_GREEN}[PASS]${C_RESET} %s\n" "$*"; }
fail() { printf "${C_RED}[FAIL]${C_RESET} %s\n" "$*"; }
info() { printf "${C_BLUE}[INFO]${C_RESET} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$*"; }

FAILURES=0
SUCCESSES=0
SKIPPED=0

record_pass() { SUCCESSES=$((SUCCESSES + 1)); ok "$1"; }
record_fail() { FAILURES=$((FAILURES + 1)); fail "$1"; }
record_skip() { SKIPPED=$((SKIPPED + 1)); warn "$1 (skipped)"; }

step() { echo; info "=== $* ==="; }

###############################################################################
# 1. Foundry 安装
###############################################################################
step "Step 1: 检查/安装 Foundry (forge / cast / anvil / chisel)"

if ! command -v forge >/dev/null 2>&1 || ! command -v anvil >/dev/null 2>&1; then
  info "未检测到 forge/anvil，开始通过 foundryup 安装..."
  if [ ! -x "$FOUNDRY_BIN" ]; then
    info "安装 foundryup..."
    if ! curl -L https://foundry.paradigm.xyz | bash; then
      record_fail "foundryup 安装失败"
    else
      record_pass "foundryup 安装完成"
    fi
  fi
  export PATH="$PATH:$FOUNDRY_DIR"
  if [ -x "$FOUNDRY_DIR/foundryup" ]; then
    if ! foundryup; then
      record_fail "foundryup 执行失败"
    else
      record_pass "foundryup 执行完成"
    fi
  fi
else
  record_pass "forge/anvil 已存在于 PATH"
fi

if command -v forge >/dev/null 2>&1 && command -v anvil >/dev/null 2>&1; then
  info "forge: $(forge --version | head -1)"
  info "anvil: $(anvil --version | head -1)"
  record_pass "Foundry 工具链可用"
else
  record_fail "Foundry 工具链不可用，后续合约测试将跳过"
fi

###############################################################################
# 2. 前置环境检查
###############################################################################
step "Step 2: 检查前置运行环境 (Node.js / Python / Chrome)"

NODE_OK=0
if command -v node >/dev/null 2>&1; then
  NODE_MAJOR="$(node -v | sed 's/v//' | cut -d. -f1)"
  if [ "$NODE_MAJOR" -ge 22 ] 2>/dev/null; then
    record_pass "Node.js $(node -v) (>=22)"
    NODE_OK=1
  else
    record_fail "Node.js 版本过低: $(node -v)，需要 >= 22"
  fi
else
  record_fail "未安装 Node.js"
fi

if command -v python3 >/dev/null 2>&1 && python3 -m pytest --version >/dev/null 2>&1; then
  record_pass "Python3 + pytest 可用: $(python3 --version)"
else
  record_fail "Python3 或 pytest 不可用"
fi

if command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then
  record_pass "系统 Chromium/Chrome 可用（支持 browser 测试）"
  CHROME_OK=1
else
  record_skip "未检测到系统 Chromium/Chrome（browser 测试将跳过）"
  CHROME_OK=0
fi

###############################################################################
# 3. npm 依赖安装
###############################################################################
step "Step 3: 安装 npm 依赖"

if [ "$NODE_OK" -eq 1 ]; then
  if ! npm ci --ignore-scripts --no-audit --no-fund >/tmp/opencode/npm-ci.log 2>&1; then
    record_fail "npm ci 失败（详情见 /tmp/opencode/npm-ci.log）"
  else
    record_pass "npm ci 依赖安装完成"
  fi
else
  record_skip "Node.js 不可用，跳过 npm ci"
fi

###############################################################################
# 4. Foundry 合约测试
###############################################################################
step "Step 4: Foundry 合约测试 (forge test)"

if command -v forge >/dev/null 2>&1 && [ -f foundry.toml ]; then
  if ! forge test -v >/tmp/opencode/forge-test.log 2>&1; then
    record_fail "forge test 失败（详情见 /tmp/opencode/forge-test.log）"
  else
    record_pass "forge test 全部通过"
  fi
else
  record_skip "Foundry 不可用或缺少 foundry.toml，跳过 forge test"
fi

###############################################################################
# 5. Node.js 测试
###############################################################################
step "Step 5: Node.js 测试 (node --test)"

if [ "$NODE_OK" -eq 1 ]; then
  if ! node --test test/*.test.mjs examples/verifier/test/*.test.mjs examples/x402-gate/test/*.test.mjs >/tmp/opencode/node-test.log 2>&1; then
    record_fail "Node.js 测试失败（详情见 /tmp/opencode/node-test.log）"
  else
    record_pass "Node.js 测试全部通过"
  fi
else
  record_skip "Node.js 不可用，跳过 Node.js 测试"
fi

###############################################################################
# 6. SDK 测试
###############################################################################
step "Step 6: SDK 测试 (@agentledger/verify)"

if [ "$NODE_OK" -eq 1 ] && [ -d sdk ]; then
  if ! npm --prefix sdk test >/tmp/opencode/sdk-test.log 2>&1; then
    record_fail "SDK 测试失败（详情见 /tmp/opencode/sdk-test.log）"
  else
    record_pass "SDK 测试全部通过"
  fi
else
  record_skip "Node.js 不可用或缺少 sdk/ 目录，跳过 SDK 测试"
fi

###############################################################################
# 7. Python 测试
###############################################################################
step "Step 7: Python 测试 (pytest)"

if command -v python3 >/dev/null 2>&1 && python3 -m pytest --version >/dev/null 2>&1; then
  if ! python3 -m pytest test/test_admission_journal.py test/test_charge_attempt_journal.py -q >/tmp/opencode/python-test.log 2>&1; then
    record_fail "Python 测试失败（详情见 /tmp/opencode/python-test.log）"
  else
    record_pass "Python 测试全部通过"
  fi
else
  record_skip "Python3/pytest 不可用，跳过 Python 测试"
fi

###############################################################################
# 8. Pocket 测试
###############################################################################
step "Step 8: Pocket 测试 (TypeScript + SHA-256)"

if [ "$NODE_OK" -eq 1 ] && [ -d apps/pocket ]; then
  if ! npm --prefix apps/pocket test >/tmp/opencode/pocket-test.log 2>&1; then
    record_fail "Pocket 测试失败（详情见 /tmp/opencode/pocket-test.log）"
  else
    record_pass "Pocket 测试全部通过"
  fi
else
  record_skip "Node.js 不可用或缺少 apps/pocket/ 目录，跳过 Pocket 测试"
fi

###############################################################################
# 9. 离线就绪检查
###############################################################################
step "Step 9: 离线就绪检查 (onsite:check)"

if [ "$NODE_OK" -eq 1 ]; then
  if ! node tools/onsite-check.mjs >/tmp/opencode/onsite-check.log 2>&1; then
    record_fail "onsite:check 失败（详情见 /tmp/opencode/onsite-check.log）"
  else
    record_pass "onsite:check 通过（safeToDemo: true）"
  fi
else
  record_skip "Node.js 不可用，跳过 onsite:check"
fi

###############################################################################
# 10. Browser 测试（可选，需 Chromium）
###############################################################################
step "Step 10: Browser 测试 (Chromium Studio Smoke，可选)"

if [ "${CHROME_OK:-0}" -eq 1 ] && [ "$NODE_OK" -eq 1 ]; then
  if ! npm --prefix examples/verifier run test:browser >/tmp/opencode/browser-test.log 2>&1; then
    record_fail "Browser 测试失败（详情见 /tmp/opencode/browser-test.log）"
  else
    record_pass "Browser 测试全部通过"
  fi
else
  record_skip "缺少 Chromium 或 Node.js，跳过 Browser 测试"
fi

###############################################################################
# 11. 汇总报告
###############################################################################
echo
echo "=============================================="
info "AgentLedger 一键自检汇总报告"
echo "=============================================="
printf "${C_GREEN}通过：${C_RESET} %d\n" "$SUCCESSES"
printf "${C_RED}失败：${C_RESET} %d\n" "$FAILURES"
printf "${C_YELLOW}跳过：${C_RESET} %d\n" "$SKIPPED"
echo "----------------------------------------------"

if [ "$FAILURES" -gt 0 ]; then
  fail "自检未完全通过，请查看上方失败项及 /tmp/opencode/*.log 详情"
  exit 1
else
  ok "所有可运行测试层全部通过 ✅"
  exit 0
fi
