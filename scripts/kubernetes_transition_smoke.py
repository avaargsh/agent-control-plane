#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from agent_control_plane.cli_runtime_transports import KubectlDeploymentApi
from agent_control_plane.execution_fencing import (
    KubernetesDeploymentFenceProjector,
    SQLiteExecutionLeaseStore,
    acquire_fenced_execution_lease,
)
from agent_control_plane.execution_journal import (
    ReconcileStatus,
    SQLiteExecutionJournal,
    reconcile_deployment_attempt,
)
from agent_control_plane.transition_ledger import (
    SQLiteTransitionLedger,
    TransitionPhase,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
    VerificationStatus,
    verify_outcome,
)
from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    PolicyEffect,
    TransitionPolicyInput,
    evaluate_policy,
)
from agent_control_plane.transition_approval import (
    ApprovalDecision,
    ApprovalSigningKey,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    TransitionApproval,
    authorize_transition_from_approval,
)
from agent_control_plane.state_transition_protocol import (
    ActionIntent,
    EvidenceBundle,
    EvidenceItem,
    ExecutionFence,
    OutcomeCondition,
    OutcomeContract,
    Principal,
    ResourceIdentity,
    StateTransition,
)


NAMESPACE = "agent-transition-smoke"
DEPLOYMENT = "state-transition-smoke"
BEFORE_REPLICAS = 20
DESIRED_REPLICAS = 30


def run(command: list[str], *, input_text: str | None = None) -> str:
    completed = subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed.stdout


def bootstrap(context: str) -> None:
    namespace = run(\n        [
            "kubectl",
            "--context",
            context,
            "create",
            "namespace",
            NAMESPACE,
            "--dry-run=client",
            "-o",
            "yaml",
        ]
    )
    run(
        ["kubectl", "--context", context, "apply", "-f", "-"],
        input_text=namespace,
    )

    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": DEPLOYMENT,
            "namespace": NAMESPACE,
        },
        "spec": {
            "replicas": BEFORE_REPLICAS,
            "selector": {
                "matchLabels": {
                    "app": DEPLOYMENT,
                },
            },
            "template": {
                "metadata": {
                    "labels": {
                        "app": DEPLOYMENT,
                    },
                },
                "spec": {
                    "containers": [
                        {
                            "name": "pause",
                            "image": "registry.k8s.io/pause:3.10",
                            "resources": {
                                "requests": {
                                    "cpu": "1m",
                                    "memory": "1Mi",
                                },
                            },
                        },
                    ],
                },
            },
        },
    }
    run(
        [
            "kubectl",
            "--context",
            context,
            "-n",
            NAMESPACE,
            "apply",
            "-f",
            "-",
        ],
        input_text=json.dumps(deployment),
    )
    run(
        [
            "kubectl",
            "--context",
            context,
            "-n",
            NAMESPACE,
            "rollout",
            "status",
            f"deployment/{DEPLOYMENT}",
            "--timeout=180s",
        ]
    )


def main() -> int:
    context = os.environ.get("KUBE_CONTEXT", "kind-agent-transition")
    api = KubectlDeploymentApi(context=context)

    bootstrap(context)

    live = api.get_deployment(
        namespace=NAMESPACE,
        name=DEPLOYMENT,
    )
    if live is None:
        raise RuntimeError("bootstrap Deployment disappeared")

    metadata = live["metadata"]
    generation = int(metadata["generation"])
    resource = ResourceIdentity(
        provider="kubernetes",
        resource_uid=str(metadata["uid"]),
        namespace=NAMESPACE,
        kind="Deployment",
        name=DEPLOYMENT,
    )
    collector = Principal(
        type="service_account",
        subject="ci/kind-observer",
    )
    started_at = datetime.now(timezone.utc)

    before_evidence = EvidenceItem.capture(
        evidence_id=(
            f"k8s-deployment:{resource.resource_uid}:"
            f"{metadata['resourceVersion']}"
        ),
        evidence_type="kubernetes_deployment",
        source=(
            f"kubernetes://{NAMESPACE}/deployment/{DEPLOYMENT}"
        ),
        collected_at=started_at,
        collector_name="kind-live-smoke",
        collector_version="v1",
        principal=collector,
        query=f"get deployment/{DEPLOYMENT}",
        payload=live,
    )
    evidence = EvidenceBundle.seal(
        resource=resource,
        generation=generation,
        created_at=started_at,
        items=(before_evidence,),
    )
    outcome = OutcomeContract.seal(
        desired_conditions=(
            OutcomeCondition(
                source="kubernetes",
                expression="deployment.status.readyReplicas",
                comparator="eq",
                expected=DESIRED_REPLICAS,
            ),
        ),
        safety_conditions=(
            OutcomeCondition(
                source="prometheus",
                expression="error_rate",
                comparator="lt",
                expected=0.01,
            ),
        ),
        stabilization_seconds=0,
        deadline_seconds=180,
    )
    transition = StateTransition.seal(
        transition_id="kind-live-scale-20-30",
        subject=resource,
        expected_generation=generation,
        before={"replicas": BEFORE_REPLICAS},
        desired={"replicas": DESIRED_REPLICAS},
        evidence_hash=evidence.manifest_hash,
        outcome_contract_hash=outcome.contract_hash,
        created_at=started_at,
    )
    action = ActionIntent.seal(
        action_id="kind-live-action-scale-20-30",
        transition_hash=transition.transition_hash,
        provider="kubernetes",
        operation="scale_deployment",
        parameters={"replicas": DESIRED_REPLICAS},
    )

    agent = Principal(
        type="agent",
        subject="ci/kind-autoscaler",
    )
    controller = Principal(
        type="controller",
        subject="ci/kind-controller",
    )
    ledger_db = os.environ.get(
        "TRANSITION_LEDGER_DB",
        ".artifacts/kubernetes-transition/transition-ledger.db",
    )
    os.makedirs(os.path.dirname(ledger_db) or ".", exist_ok=True)
    ledger = SQLiteTransitionLedger(ledger_db)
    ledger_record = ledger.register(
        transition=transition,
        action=action,
        actor=agent,
        occurred_at=started_at,
    )

    policy_version = "kind-live-scale-policy/v1"
    policy_input = TransitionPolicyInput.seal(
        policy_version=policy_version,
        evidence=evidence,
        transition=transition,
        action=action,
        principal=agent,
        context={
            "operation": "scale_deployment",
            "namespace": NAMESPACE,
            "replicas": DESIRED_REPLICAS,
        },
    )
    policy_decision = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version=policy_version,
            max_replicas=DESIRED_REPLICAS,
            allowed_namespaces=(NAMESPACE,),
        ),
    )
    ledger_record = ledger.record_policy(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        policy_input=policy_input,
        decision=policy_decision,
        actor=Principal(
            type="service_account",
            subject="ci/policy-engine",
        ),
        occurred_at=datetime.now(timezone.utc),
    )
    ledger_record = ledger.route_policy_result(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        actor=Principal(
            type="service_account",
            subject="ci/policy-engine",
        ),
        occurred_at=datetime.now(timezone.utc),
    )
    if policy_decision.effect is not PolicyEffect.PERMIT:
        raise RuntimeError(
            f"live scale denied: {policy_decision.reasons}"
        )
    if ledger_record.phase is not TransitionPhase.AWAITING_APPROVAL:
        raise RuntimeError(
            f"unexpected ledger phase after policy: {ledger_record.phase.value}"
        )

    approver = Principal(
        type="human",
        subject="ci/kind-approver",
    )
    approval_key = ApprovalSigningKey(
        key_id="kind-live-approval-key-v1",
        approver=approver,
        secret=b"kind-live-test-only-approval-secret",
    )
    approval = TransitionApproval.seal(
        approval_id="kind-live-approval-scale-20-30",
        approver=approver,
        decision=ApprovalDecision.APPROVE,
        transition=transition,
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
        issued_at=started_at,
        expires_at=started_at + timedelta(minutes=5),
        reason="kind live state transition proof",
    )
    signed_approval = SignedTransitionApproval.sign(
        approval,
        key=approval_key,
    )
    authorization = authorize_transition_from_approval(
        transition=transition,
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
        signed_approval=signed_approval,
        approval_verifier=HMACApprovalVerifier(
            keys={approval_key.key_id: approval_key},
        ),
        authorization_expires_at=started_at + timedelta(minutes=4),
        now=started_at,
    )
    ledger_record = ledger.record_authorization(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        approval=approval,
        authorization=authorization,
        actor=approver,
        occurred_at=datetime.now(timezone.utc),
    )
    holder = controller
    lease_db = os.environ.get(
        "EXECUTION_LEASE_DB",
        ".artifacts/kubernetes-transition/execution-leases.db",
    )
    os.makedirs(os.path.dirname(lease_db) or ".", exist_ok=True)
    lease_authority = SQLiteExecutionLeaseStore(lease_db)
    lease = acquire_fenced_execution_lease(
        authority=lease_authority,
        projector=KubernetesDeploymentFenceProjector(api),
        resource_uid=resource.resource_uid,
        namespace=NAMESPACE,
        name=DEPLOYMENT,
        holder=holder,
        now=started_at,
        ttl_seconds=300,
    )
    fence = ExecutionFence.bind(
        transition=transition,
        action=action,
        authorization=authorization,
        lease=lease,
    )
    ledger_record = ledger.record_lease(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        lease=lease,
        fence=fence,
        actor=holder,
        occurred_at=datetime.now(timezone.utc),
    )

    journal_db = os.environ.get(
        "EXECUTION_JOURNAL_DB",
        ".artifacts/kubernetes-transition/execution-journal.db",
    )
    os.makedirs(os.path.dirname(journal_db) or ".", exist_ok=True)
    execution_journal = SQLiteExecutionJournal(journal_db)
    attempt = execution_journal.prepare(
        transition=transition,
        action=action,
        authorization=authorization,
        fence=fence,
        prepared_at=datetime.now(timezone.utc),
    )
    ledger_record = ledger.record_execution_prepared(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        attempt=attempt,
        actor=holder,
        occurred_at=datetime.now(timezone.utc),
    )

    receipt = KubernetesDeploymentScaleProvider(
        api,
        lease_authority=lease_authority,
    ).execute(
        transition=transition,
        evidence=evidence,
        outcome_contract=outcome,
        action=action,
        authorization=authorization,
        fence=fence,
        active_lease=lease,
        caller=holder,
        now=datetime.now(timezone.utc),
        operation_id=attempt.operation_id,
    )

    # Deliberately skip the normal COMMITTED write to exercise the process-
    # crash boundary. Reopen the durable journal and reconstruct the exact
    # attempt from live Kubernetes state.
    restarted_journal = SQLiteExecutionJournal(journal_db)
    ledger_record = ledger.start_reconcile(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        actor=holder,
        occurred_at=datetime.now(timezone.utc),
    )
    reconciled = reconcile_deployment_attempt(
        api=api,
        journal=restarted_journal,
        attempt=restarted_journal.get(attempt.attempt_id),
        transition=transition,
        action=action,
        namespace=NAMESPACE,
        name=DEPLOYMENT,
        reconciled_at=datetime.now(timezone.utc),
    )
    if reconciled.status is not ReconcileStatus.APPLIED:
        raise RuntimeError(
            "live execution attempt did not reconcile as APPLIED: "
            f"{reconciled.status.value}: {reconciled.reason}"
        )
    committed_attempt = restarted_journal.get(attempt.attempt_id)
    if committed_attempt is None or committed_attempt.result_hash is None:
        raise RuntimeError(
            "reconciled execution attempt was not durably committed"
        )
    ledger_record = ledger.record_execution_result(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        attempt=committed_attempt,
        actor=holder,
        occurred_at=datetime.now(timezone.utc),
    )
    ledger_record = ledger.start_verification(
        transition_id=ledger_record.transition_id,
        expected_version=ledger_record.state_version,
        actor=holder,
        occurred_at=datetime.now(timezone.utc),
    )

    observer = KubernetesDeploymentObserver(
        api,
        collector_name="kind-live-smoke",
        collector_version="v1",
    )
    deadline = time.monotonic() + 180
    last = None
    while time.monotonic() < deadline:
        checked_at = datetime.now(timezone.utc)
        observation = observer.observe(
            resource=resource,
            principal=collector,
            observed_at=checked_at,
        )
        last = verify_outcome(
            transition=transition,
            outcome_contract=outcome,
            observation=observation,
            safety_metrics={"error_rate": 0.0},
            checked_at=checked_at,
        )
        if last.status is VerificationStatus.SUCCEEDED:
            ledger_record = ledger.record_verification(
                transition_id=ledger_record.transition_id,
                expected_version=ledger_record.state_version,
                result=last,
                actor=holder,
                occurred_at=checked_at,
            )
            ledger_record = ledger.release(
                transition_id=ledger_record.transition_id,
                expected_version=ledger_record.state_version,
                actor=holder,
                occurred_at=datetime.now(timezone.utc),
            )
            ledger.verify_history(ledger_record.transition_id)
            summary = {
                "transition_id": transition.transition_id,
                "transition_hash": transition.transition_hash,
                "action_hash": action.action_hash,
                "policy_input_hash": policy_input.input_hash,
                "policy_decision_hash": policy_decision.decision_hash,
                "authorization_hash": authorization.authorization_hash,
                "approval_id": approval.approval_id,
                "approval_hash": approval.approval_hash,
                "approval_key_id": signed_approval.key_id,
                "approval_decision": approval.decision.value,
                "lease_id": lease.lease_id,
                "lease_epoch": lease.epoch,
                "execution_attempt_id": attempt.attempt_id,
                "operation_id": attempt.operation_id,
                "reconcile_status": reconciled.status.value,
                "reconciled_result_hash": committed_attempt.result_hash,
                "ledger_phase": ledger_record.phase.value,
                "ledger_state_version": ledger_record.state_version,
                "ledger_last_event_hash": ledger_record.last_event_hash,
                "ledger_event_count": len(
                    ledger.events(ledger_record.transition_id)
                ),
                "evidence_before": evidence.manifest_hash,
                "evidence_after": (
                    observation.evidence_bundle.manifest_hash
                ),
                "resource_uid": resource.resource_uid,
                "generation_before": generation,
                "generation_after": receipt.after_generation,
                "resource_version_before": (
                    receipt.before_resource_version
                ),
                "resource_version_after": (
                    receipt.after_resource_version
                ),
                "replicas_before": BEFORE_REPLICAS,
                "replicas_desired": DESIRED_REPLICAS,
                "ready_replicas": observation.deployment.get(
                    "status", {}
                ).get("readyReplicas"),
                "verification_status": last.status.value,
                "receipt": {
                    "changed": receipt.changed,
                    "replayed": receipt.replayed,
                    "operation_id": receipt.operation_id,
                    "verified_after_uncertain_mutation": (
                        receipt.verified_after_uncertain_mutation
                    ),
                },
            }
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        if last.status in {
            VerificationStatus.INVARIANT_VIOLATION,
            VerificationStatus.TIMED_OUT,
            VerificationStatus.UNKNOWN,
        }:
            ledger_record = ledger.record_verification(
                transition_id=ledger_record.transition_id,
                expected_version=ledger_record.state_version,
                result=last,
                actor=holder,
                occurred_at=checked_at,
            )
            break
        time.sleep(1)

    raise RuntimeError(
        "live transition did not verify successfully: "
        + (
            json.dumps(
                {
                    "status": last.status.value,
                    "reasons": last.reasons,
                }
            )
            if last is not None
            else "no observation"
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
