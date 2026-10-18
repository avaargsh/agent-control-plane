# Continuation history upgrade experiment

This opt-in experiment runs **real Pydantic AI and DBOS packages**, using SQLite
and a synthetic local continuation model. It needs Python 3.11+, POSIX, and no
GPU, model API credentials, Temporal server, Kubernetes cluster, or PostgreSQL.
Normal unit CI tests only its evidence rejection rules.

## Run

From the repository root, using `uv`:

```bash
uv venv .venv-durability-old
uv pip install --python .venv-durability-old/bin/python -r integration/durability/requirements-old.txt
uv venv .venv-durability-new
uv pip install --python .venv-durability-new/bin/python -r integration/durability/requirements-new.txt
python3 integration/durability/dbos_recovery_probe.py \
  --old-python .venv-durability-old/bin/python \
  --new-python .venv-durability-new/bin/python \
  --output .artifacts/durability/run-001 --iterations 1 --jobs 1
```

Use a **new output directory** for each run. Reusing an existing directory fails;
the runner does not erase previous evidence. `--iterations 100 --jobs 4` repeats
each case 100 times with at most four concurrent workers. The first unexpected
failure cancels queued work; already-running cases finish with bounded timeouts.
No package installation occurs inside the runner. Both requirement files pin
DBOS to 3.3.0, isolating the Pydantic AI/Graph version change. Complete installed
dependency versions are retained per worker; these files are not transitive locks.

## Cases and pass conditions

The seed workflow records two suspended model results, waits between segments,
then receives SIGKILL **inside the third model request, before checkpointing it**.
A new interpreter registers the same workflow and lets DBOS recover its SQLite
history. The application version is deliberately held constant to reproduce an
unsafe unversioned deployment. No upstream source is patched or copied.

| Case | Expected observation |
| --- | --- |
| 2.56.0 → 2.56.0 | Recovery returns `done`; no durable sleep records |
| 2.57.0 → 2.57.0 | Recovery returns `done`; two completed `DBOS.sleep` records persist |
| 2.56.0 → 2.57.0 | Recovery fails with `DBOSUnexpectedStepError` at step 2 expecting `DBOS.sleep` |

For all cases, completed model requests 0 and 1 must not execute again. In the
same-version cases, uncheckpointed request 2 executes twice overall: once before
SIGKILL and once during recovery. That is an **expected retry**, not exactly-once
model execution. In the incompatible case, no new model request may execute.
The recorded step prefix must remain byte-for-byte equivalent after JSON export.
A missing package, timeout, ordinary exception, or seed process that exits
normally is a failed experiment, not evidence of history incompatibility.

`matrixStatus=PASS` means these expectations were reproduced. The separate
`upgradeDecision=BLOCK_UNVERSIONED_TAKEOVER` explicitly prevents interpreting
that PASS as an approval to migrate existing workflows.

## Evidence

Each case retains the real DBOS SQLite database, seed/recovery dependency
inventories, pre-crash checkpoint, recovered steps/error, external model request
ledger, subprocess stdout/stderr, exit codes, and a per-case verdict.
`summary.json` includes dispositions, not-run counts, worker source SHA-256s,
and hashes of retained files. Hashes establish integrity relative to this local
report, not signer identity or independent attestation.

The CLI records existing upstream results rather than simulating their expected
error. Its database files may contain upstream serialized values; keep them as
local experiment evidence and do not restore untrusted database files.

## Architecture decision

Continue **Adopt Temporal → Integrate Pydantic AI** for the existing business
workflow direction. The DBOS probe is a focused comparison, not another workflow
engine inside ACP. A Harness continuation-loop change can alter engine history;
version routing or draining must therefore cover the integration package as well
as business workflow code. This experiment does not implement or validate a
version-routing/draining rollout strategy.

ACP continues to own exact-plan authorization, CAS/fencing, UNKNOWN reconciliation,
operation ownership, and independent execution proof. Neither successful engine
replay nor a model/tool transcript grants mutation authority or proves an external
side effect occurred exactly once. No core API, schema, or v0.1 acceptance artifact
contract changes in this experiment.

Contribute an upstream reproducer if actual results differ from documented
behavior. Build no replacement durability engine on this evidence.

## Official basis and remaining work

- [Pydantic AI PR #10383](https://github.com/pydantic/pydantic-ai/pull/10383),
  merged 2026-10-15: durable DBOS continuation waits and the explicit old-history
  incompatibility warning.
- [Pydantic AI v2.57.0](https://github.com/pydantic/pydantic-ai/releases/tag/v2.57.0):
  published fix; this probe exercises the package, not an inferred implementation.
- [DBOS PR #888](https://github.com/dbos-inc/dbos-transact-py/pull/888): superseded
  execution error-path ownership protection. **Not covered by this probe.**

Temporal activity context snapshots, DBOS PostgreSQL owner-takeover races,
Restate 1.8 migration, durable human approval, cancellation, tool lost-ACK
idempotency, and controlled live-provider acceptance remain **NOT RUN** here.
The existing ACP crash/concurrency matrix covers its own contracts separately.
