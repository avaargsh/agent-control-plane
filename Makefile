# SPDX-License-Identifier: Apache-2.0
# AgentLedger Hackathon — 一键安装与自检 Makefile
#
# 用法：
#   make help            查看所有可用目标
#   make install         安装完整环境（Foundry/Anvil + npm 依赖）
#   make test            运行全部测试层（完整自检）
#   make test-foundry    仅运行 Solidity/Forge 测试
#   ...
#
# 环境变量：
#   AGENTLEDGER_CHROME_PATH  浏览器二进制路径（用于 make test-browser）
#   AGENTLEDGER_BROWSER_ARTIFACTS  Browser 测试截图输出目录

SHELL := /bin/bash
ROOT_DIR := $(shell pwd)
FOUNDRY_DIR := $(HOME)/.foundry/bin
FOUNDRY_BIN := $(FOUNDRY_DIR)/foundryup
export PATH := $(FOUNDRY_DIR):$(PATH)

# 颜色
C_RESET  := \033[0m
C_GREEN  := \033[0;32m
C_RED    := \033[0;31m
C_YELLOW := \033[1;33m
C_BLUE   := \033[0;34m

.PHONY: help install install-foundry install-deps check-env \
        test test-foundry test-node test-sdk test-python test-pocket \
        test-onsite test-browser test-anvil test-live \
        selfcheck anvil clean

###############################################################################
# 帮助
###############################################################################
help: ## 显示所有可用 make 目标及说明
	@echo "$(C_BLUE)AgentLedger Hackathon — Makefile 目标列表$(C_RESET)"
	@echo "=============================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(C_GREEN)%-18s$(C_RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "环境变量："
	@echo "  AGENTLEDGER_CHROME_PATH        浏览器二进制路径（用于 test-browser）"
	@echo "  AGENTLEDGER_BROWSER_ARTIFACTS  Browser 测试截图输出目录（默认 out/demo-journey）"

###############################################################################
# 安装目标
###############################################################################

install: install-foundry install-deps ## 安装完整环境（Foundry/Anvil + npm 依赖）

install-foundry: ## 安装 Foundry 工具链（forge / cast / anvil / chisel）
	@echo "$(C_BLUE)==> 检查/安装 Foundry$(C_RESET)"
	@if command -v forge >/dev/null 2>&1 && command -v anvil >/dev/null 2>&1; then \
		echo "$(C_GREEN)[PASS]$(C_RESET) forge/anvil 已存在于 PATH"; \
	else \
		echo "$(C_BLUE)--> 未检测到 forge/anvil，开始通过 foundryup 安装...$(C_RESET)"; \
		if [ ! -x "$(FOUNDRY_BIN)" ]; then \
			echo "$(C_BLUE)--> 安装 foundryup...$(C_RESET)"; \
			curl -L https://foundry.paradigm.xyz | bash; \
		fi; \
		export PATH="$(PATH)"; foundryup; \
	fi
	@forge --version >/dev/null 2>&1 && anvil --version >/dev/null 2>&1 && \
		echo "$(C_GREEN)[PASS]$(C_RESET) Foundry 工具链可用 (forge $$(forge --version | head -1 | cut -d' ' -f2))" || \
		(echo "$(C_RED)[FAIL]$(C_RESET) Foundry 安装失败"; exit 1)

install-deps: ## 安装 npm 依赖（npm ci --ignore-scripts --no-audit --no-fund）
	@echo "$(C_BLUE)==> 安装 npm 依赖$(C_RESET)"
	@npm ci --ignore-scripts --no-audit --no-fund
	@echo "$(C_GREEN)[PASS]$(C_RESET) npm 依赖安装完成"

check-env: ## 检查前置运行环境（Node.js / Python / Chrome）
	@echo "$(C_BLUE)==> 检查前置运行环境$(C_RESET)"
	@node -v >/dev/null 2>&1 && NODE_MAJOR=$$(node -v | sed 's/v//' | cut -d. -f1) && \
		if [ "$$NODE_MAJOR" -ge 22 ] 2>/dev/null; then \
			echo "$(C_GREEN)[PASS]$(C_RESET) Node.js $$(node -v) (>=22)"; \
		else \
			echo "$(C_RED)[FAIL]$(C_RESET) Node.js 版本过低: $$(node -v)，需要 >= 22"; exit 1; \
		fi || (echo "$(C_RED)[FAIL]$(C_RESET) 未安装 Node.js"; exit 1)
	@python3 -m pytest --version >/dev/null 2>&1 && \
		echo "$(C_GREEN)[PASS]$(C_RESET) Python3 + pytest 可用 ($$(python3 --version))" || \
		(echo "$(C_RED)[FAIL]$(C_RESET) Python3 或 pytest 不可用"; exit 1)
	@if command -v google-chrome >/dev/null 2>&1 || command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1; then \
		echo "$(C_GREEN)[PASS]$(C_RESET) 系统 Chromium/Chrome 可用（支持 browser 测试）"; \
	else \
		echo "$(C_YELLOW)[WARN]$(C_RESET) 未检测到系统 Chromium/Chrome（test-browser 将跳过）"; \
	fi

###############################################################################
# 测试目标（可单独运行）
###############################################################################

test-foundry: install-foundry ## 运行 Solidity/Forge 合约测试
	@echo "$(C_BLUE)==> Foundry 合约测试 (forge test)$(C_RESET)"
	@forge test -v
	@echo "$(C_GREEN)[PASS]$(C_RESET) forge test 全部通过"

test-node: install-deps ## 运行 Node.js 测试层
	@echo "$(C_BLUE)==> Node.js 测试 (node --test)$(C_RESET)"
	@node --test test/*.test.mjs examples/verifier/test/*.test.mjs examples/x402-gate/test/*.test.mjs
	@echo "$(C_GREEN)[PASS]$(C_RESET) Node.js 测试全部通过"

test-sdk: install-deps ## 运行 SDK 测试 (@agentledger/verify)
	@echo "$(C_BLUE)==> SDK 测试$(C_RESET)"
	@npm --prefix sdk test
	@echo "$(C_GREEN)[PASS]$(C_RESET) SDK 测试全部通过"

test-python: ## 运行 Python 测试（pytest）
	@echo "$(C_BLUE)==> Python 测试 (pytest)$(C_RESET)"
	@python3 -m pytest test/test_admission_journal.py test/test_charge_attempt_journal.py -q
	@echo "$(C_GREEN)[PASS]$(C_RESET) Python 测试全部通过"

test-pocket: install-deps ## 运行 Pocket 测试（TypeScript + SHA-256）
	@echo "$(C_BLUE)==> Pocket 测试$(C_RESET)"
	@npm --prefix apps/pocket test
	@echo "$(C_GREEN)[PASS]$(C_RESET) Pocket 测试全部通过"

test-onsite: install-deps ## 运行离线就绪检查 (onsite:check)
	@echo "$(C_BLUE)==> 离线就绪检查$(C_RESET)"
	@node tools/onsite-check.mjs
	@echo "$(C_GREEN)[PASS]$(C_RESET) onsite:check 通过（safeToDemo: true）"

test-browser: install-deps check-env ## 运行 Chromium Studio Browser 测试（可选，需 Chrome）
	@echo "$(C_BLUE)==> Browser 测试 (Chromium Studio Smoke)$(C_RESET)"
	@npm --prefix examples/verifier run test:browser
	@echo "$(C_GREEN)[PASS]$(C_RESET) Browser 测试全部通过"

test-anvil: install-foundry ## 启动本地 Anvil 并运行本地 EVM E2E 测试（需额外部署助手）
	@echo "$(C_YELLOW)[WARN]$(C_RESET) test-anvil 需要 tools/serve-testnet-deploy.mjs（当前仓库未包含）"
	@echo "$(C_YELLOW)[WARN]$(C_RESET) 且需 Anvil 监听 18545 端口、chain ID 10143"
	@echo "$(C_BLUE)==> 启动 Anvil (127.0.0.1:18545, chain 10143)...$(C_RESET)"
	@anvil --port 18545 --chain-id 10143 --block-time 1 > /tmp/opencode/anvil.log 2>&1 &
	@echo $$! > /tmp/opencode/anvil.pid
	@sleep 3
	@cat /tmp/opencode/anvil.log
	@echo "$(C_BLUE)==> 运行本地 Anvil E2E 测试$(C_RESET)"
	@AGENTLEDGER_ANVIL_RPC=http://127.0.0.1:18545 AGENTLEDGER_DEPLOY_URL=http://127.0.0.1:14422 \
		python3 examples/verifier/browser/testnet-local-e2e.py || \
		(echo "$(C_RED)[FAIL]$(C_RESET) Anvil E2E 测试失败（可能因缺少部署助手服务）"; exit 1)

test-live: ## 运行真实公共 Monad Testnet 只读测试（可选，需网络访问）
	@echo "$(C_YELLOW)[WARN]$(C_RESET) test-live 需要真实 Monad Testnet 网络访问，README 标注为 Optional"
	@npm --prefix examples/verifier run test:browser:live

###############################################################################
# 综合自检
###############################################################################

test: check-env install-foundry install-deps test-foundry test-node test-sdk test-python test-pocket test-onsite test-browser ## 运行全部测试层（完整自检，等同于 selfcheck）
	@echo ""
	@echo "$(C_GREEN)==============================================$(C_RESET)"
	@echo "$(C_GREEN)  AgentLedger 一键自检全部通过 ✅$(C_RESET)"
	@echo "$(C_GREEN)==============================================$(C_RESET)"

selfcheck: test ## 别名：运行全部测试层（完整自检）

###############################################################################
# Anvil 本地节点
###############################################################################

anvil: install-foundry ## 启动本地 Anvil 节点（默认 8545 端口）
	@echo "$(C_BLUE)==> 启动 Anvil (127.0.0.1:8545)$(C_RESET)"
	@anvil --port 8545 --block-time 1

###############################################################################
# 清理
###############################################################################

clean: ## 清理临时文件和构建产物
	@echo "$(C_BLUE)==> 清理临时文件$(C_RESET)"
	@rm -rf /tmp/opencode/anvil.log /tmp/opencode/anvil.pid 2>/dev/null || true
	@echo "$(C_GREEN)[PASS]$(C_RESET) 清理完成"
