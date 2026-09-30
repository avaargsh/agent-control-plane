# Measured Qwen MCP routing evidence

This directory contains a retained empirical Decision Lab artifact used to prove
the Agent Control Plane consumer boundary with real model inference.

## Source

- producer: `avaargsh/agent-decision-lab`
- model: `Qwen/Qwen3-0.6B`
- producer branch: `experiment/measured-system2-fallback-v2`
- producer commit: `fb1ba30bfb386080e101140f6630ea29bf11b004`
- GitHub Actions run: `36705402666`
- Actions artifact id: `11092303346`
- decision artifact id:
  `decision-eval:sha256:d829b647cfb0a803efeb464719610b4bf4f1de5c282a8c58c3a53b3f16d64a0b`
- calibration dataset digest:
  `sha256:84d4fbfeebc85d2f60d49851082754c5dcbe0a97f80ac1d6d1a8421a5721001c`
- test dataset digest:
  `sha256:6ae2a22b0f566508e9ea394ced032e23046c0ebf6cbb0238dad2133b0e43d65a`

## Environment

The run used a GitHub-hosted Linux x64 runner with 4 vCPU and no NVIDIA GPU.
The benchmark corpus is synthetic; the Qwen inference is real.

## Observed result

- frozen-logits accuracy: 0.3125
- System-2 structured-output accuracy: 0.375
- threshold: 0.80
- fallback cases: 16 / 16
- fallback rate: 1.0
- fallback accuracy: 0.375
- fallback p50: 5774.69 ms
- fallback p95: 5929.37 ms
- mean fallback tokens processed: 106.5625
- parse-valid rate: 1.0

## Interpretation

This artifact proves the real-model selective fallback/evidence path executes and
is replayable. It does **not** justify production promotion.

At threshold 0.80 the measured fast path has zero coverage because every case
falls back, and the measured accuracy is low. Agent Control Plane CI therefore
verifies the artifact integrity but expects the production-oriented example gate
to reject it.
