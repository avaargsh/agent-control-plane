#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from agent_control_plane.authority_reservation import (
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.cli_runtime_transports import KubectlDeploymentApi
from agent_control_plane.context_overlay import SQLiteContextOverlayStore
from agent_control_plane.context_transition import (
    TransitionProposalBindingV3,
    authorize_context_bound_transition,
    seal_context_bound_policy_input,
)
from agent_control_plane.execution_attestation import ExecutionAttestation
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
from agent_control_plane.execution_lifecycle import (
    KubernetesDeploymentExecutionCoordinator,
)
from agent_control_plane.independent_verifier import (
    IndependentExecutionProof,
)
from agent_control_plane.kubernetes_deployment_transition import (
    ContextBoundExecutionContext,
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
    VerificationStatus,
    build_deployment_execution_verification,
    verify_outcome,
)
from agent_control_plane.policy_replay import (
    DeploymentScalePolicy,
    PolicyEffect,
    evaluate_policy,
)
from agent_control_plane.transition_approval import (
    ApprovalDecision,
    ApprovalSigningKey,
    HMACApprovalVerifier,
    SignedTransitionApproval,
    TransitionApproval,
)
from agent_control_plane.work_context import SQLiteWorkContextStore
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
    namespace = run(
        [
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

    work_db = os.environ.get(
        "WORK_CONTEXT_DB",
        ".artifacts/kubernetes-transition/work-context.db",
    )
    os.makedirs(os.path.dirname(work_db) or ".", exist_ok=True)
    work_store = SQLiteWorkContextStore(work_db)
    work = work_store.create(
        work_id="kind-live-scale-work-20-30",
        namespace=f"kubernetes/{NAMESPACE}",
        goal=(
            f"Scale Deployment {DEPLOYMENT} from "
            f"{BEFORE_REPLICAS} to {DESIRED_REPLICAS} replicas"
        ),
        actor=Principal(
            type="human",
            subject="ci/kind-requester",
        ),
        created_at=started_at,
        state={
            "deployment": DEPLOYMENT,
            "namespace": NAMESPACE,
            "replicas_before": BEFORE_REPLICAS,
            "replicas_desired": DESIRED_REPLICAS,
            "evidence_hash": evidence.manifest_hash,
        },
    )
    work = work_store.claim(
        work_id=work.work_id,
        expected_version=work.version,
        agent=agent,
        claimed_at=started_at + timedelta(milliseconds=1),
    )
    overlay_store = SQLiteContextOverlayStore(work_db)
    overlay_store.append(
        work_id=work.work_id,
        expected_revision=0,
        actor=agent,
        entry_type="observation",
        payload={
            "source": "kind-live-smoke",
            "message": "proposal context before authorization",
        },
        created_at=started_at + timedelta(microseconds=1500),
    )
    projection = work_store.project(
        work_id=work.work_id,
        consumer=agent,
    )
    observed_overlay = overlay_store.get(
        work_id=work.work_id,
    )
    observed_authority = work_store.get_authority_head(
        work.work_id
    )
    proposal = TransitionProposalBindingV3.seal(
        proposal_id="kind-live-context-proposal-scale-20-30",
        proposer=agent,
        transition=transition,
        projection=projection,
        authority_head=observed_authority,
        context_overlay=observed_overlay,
        created_at=started_at + timedelta(milliseconds=2),
    )

    policy_version = "kind-live-scale-policy/v1"
    policy_input = seal_context_bound_policy_input(
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
        proposal=proposal,
        store=work_store,
    )
    policy_decision = evaluate_policy(
        policy_input=policy_input,
        evaluator=DeploymentScalePolicy(
            policy_version=policy_version,
            max_replicas=DESIRED_REPLICAS,
            allowed_namespaces=(NAMESPACE,),
        ),
    )
    if policy_decision.effect is not PolicyEffect.PERMIT:
        raise RuntimeError(
            f"live scale denied: {policy_decision.reasons}"
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
    approval_verifier = HMACApprovalVerifier(
        keys={approval_key.key_id: approval_key},
    )
    authorization = authorize_context_bound_transition(
        transition=transition,
        action=action,
        policy_input=policy_input,
        policy_decision=policy_decision,
        proposal=proposal,
        store=work_store,
        signed_approval=signed_approval,
        approval_verifier=approval_verifier,
        authorization_expires_at=started_at + timedelta(minutes=4),
        now=started_at + timedelta(milliseconds=3),
    )

    overlay_store.append(
        work_id=work.work_id,
        expected_revision=proposal.context_revision,
        actor=agent,
        entry_type="note",
        payload={
            "message": "context advanced after authorization",
            "must_not_invalidate_authority": True,
        },
        created_at=started_at + timedelta(milliseconds=4),
    )
    execution_overlay = overlay_store.get(
        work_id=work.work_id,
    )
    current_work = work_store.get(work.work_id)
    current_authority = work_store.get_authority_head(
        work.work_id
    )
    if (
        current_work.version != proposal.work_version
        or current_work.snapshot_hash != proposal.work_snapshot_hash
    ):
        raise RuntimeError(
            "context-only append unexpectedly changed authoritative work"
        )
    if current_authority.authority_hash != proposal.authority_hash:
        raise RuntimeError(
            "context-only append unexpectedly changed authority head"
        )
    if execution_overlay.context_revision != proposal.context_revision + 1:
        raise RuntimeError(
            "kind smoke did not advance independent context revision"
        )
    holder = Principal(
        type="controller",
        subject="ci/kind-controller",
    )
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

    journal_db = os.environ.get(
        "EXECUTION_JOURNAL_DB",
        ".artifacts/kubernetes-transition/execution-journal.db",
    )
    os.makedirs(os.path.dirname(journal_db) or ".", exist_ok=True)
    execution_journal = SQLiteExecutionJournal(journal_db)
    reservation_store = SQLiteAuthorityReservationStore(work_db)
    provider = KubernetesDeploymentScaleProvider(
        api,
        lease_authority=lease_authority,
    )
    coordinator = KubernetesDeploymentExecutionCoordinator(
        provider=provider,
        journal=execution_journal,
        reservation_store=reservation_store,
        clock=lambda: datetime.now(timezone.utc),
        lease_authority=lease_authority,
    )
    base_context = ContextBoundExecutionContext(
        policy_input=policy_input,
        proposal=proposal,
        store=work_store,
        signed_approval=signed_approval,
        approval_verifier=approval_verifier,
    )
    prepared = coordinator.prepare(
        transition=transition,
        action=action,
        authorization=authorization,
        fence=fence,
        active_lease=lease,
        context_binding=base_context,
    )
    attempt = prepared.attempt
    reservation = prepared.reservation
    context_binding = prepared.context_binding
    if reservation is None:
        raise RuntimeError(
            "proposal v3 coordinator prepare did not reserve authority"
        )
    expected_provenance_hash = attempt.context_provenance_hash
    if expected_provenance_hash is None:
        raise RuntimeError(
            "coordinator prepare did not persist context provenance"
        )

    receipt = provider.execute_context_bound(
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
        context_binding=context_binding,
    )
    if not receipt.authority_reservation_hash:
        raise RuntimeError(
            "proposal v3 live transition did not use authority reservation"
        )

    # Deliberately skip the normal COMMITTED write to exercise the process-
    # crash boundary. Reopen the durable journal and reconstruct the exact
    # attempt from live Kubernetes state.
    restarted_journal = SQLiteExecutionJournal(journal_db)
    reconciled = reconcile_deployment_attempt(
        api=api,
        journal=restarted_journal,
        attempt=restarted_journal.get(attempt.attempt_id),
        transition=transition,
        action=action,
        namespace=NAMESPACE,
        name=DEPLOYMENT,
        reconciled_at=datetime.now(timezone.utc),
        authority_reservation_store=SQLiteAuthorityReservationStore(
            work_db
        ),
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
    if (
        committed_attempt.context_provenance_hash
        != expected_provenance_hash
    ):
        raise RuntimeError(
            "reconciled execution attempt lost context provenance"
        )
    terminal_context = committed_attempt.result.get(
        "_execution_context_provenance"
    )
    terminal_reservation = committed_attempt.result.get(
        "_authority_reservation"
    )
    if (
        not isinstance(terminal_context, dict)
        or terminal_context.get("provenance_hash")
        != expected_provenance_hash
    ):
        raise RuntimeError(
            "terminal execution receipt lost context provenance"
        )
    if (
        not isinstance(terminal_reservation, dict)
        or terminal_reservation.get("reservation_hash")
        != reservation.reservation_hash
    ):
        raise RuntimeError(
            "terminal execution receipt lost authority reservation"
        )
    durable_reservation = reservation_store.get(
        reservation.reservation_id
    )
    if (
        durable_reservation is None
        or durable_reservation.state.value != "RELEASED"
    ):
        raise RuntimeError(
            "reconcile did not release terminal authority reservation"
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
            attestation = ExecutionAttestation.seal(
                attestation_id="kind-live-execution-attestation-20-30",
                attempt=committed_attempt,
                transition=transition,
                outcome_contract=outcome,
                observation_evidence=observation.evidence_bundle,
                verification=last,
            )
            attestation.verify_attempt(committed_attempt)
            attestation_path = (
                ".artifacts/kubernetes-transition/"
                "execution-attestation.json"
            )
            with open(
                attestation_path,
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    attestation.as_mapping(),
                    handle,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")

            proof_verification = build_deployment_execution_verification(
                plan=prepared.plan,
                after_observation=observation.snapshot,
                expected_operation_id=committed_attempt.operation_id,
                expected_action_hash=committed_attempt.action_hash,
                expected_transition_hash=committed_attempt.transition_hash,
                expected_authority_reservation_hash=(
                    reservation.reservation_hash
                ),
                verifier=collector,
                verified_at=checked_at,
            )
            independent_proof = IndependentExecutionProof.seal(
                proof_id="kind-live-independent-proof-20-30",
                plan=prepared.plan,
                authorization=prepared.plan_authorization,
                fence=prepared.plan_fence,
                attempt=committed_attempt,
                after_observation=observation.snapshot,
                verification=proof_verification,
            )
            proof_path = (
                ".artifacts/kubernetes-transition/"
                "independent-execution-proof.json"
            )
            proof_hash_path = proof_path + ".sha256"
            with open(proof_path, "w", encoding="utf-8") as handle:
                json.dump(
                    independent_proof.as_mapping(),
                    handle,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
            with open(
                proof_hash_path,
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(independent_proof.statement_hash + "\n")

            summary = {
                "transition_id": transition.transition_id,
                "transition_hash": transition.transition_hash,
                "action_hash": action.action_hash,
                "policy_input_hash": policy_input.input_hash,
                "policy_decision_hash": policy_decision.decision_hash,
                "authorization_hash": authorization.authorization_hash,
                "work_id": committed_attempt.context_provenance[
                    "work_id"
                ],
                "work_version": committed_attempt.context_provenance[
                    "work_version"
                ],
                "work_snapshot_hash": (
                    committed_attempt.context_provenance[
                        "work_snapshot_hash"
                    ]
                ),
                "context_projection_hash": (
                    committed_attempt.context_provenance[
                        "projection_hash"
                    ]
                ),
                "transition_proposal_hash": (
                    committed_attempt.context_provenance[
                        "proposal_hash"
                    ]
                ),
                "transition_proposal_version": proposal.proposal_version,
                "execution_context_provenance_version": (
                    committed_attempt.context_provenance[
                        "provenance_version"
                    ]
                ),
                "authority_generation": (
                    committed_attempt.context_provenance[
                        "authority_generation"
                    ]
                ),
                "authority_state_hash": (
                    committed_attempt.context_provenance[
                        "authority_state_hash"
                    ]
                ),
                "authority_hash": (
                    committed_attempt.context_provenance[
                        "authority_hash"
                    ]
                ),
                "authority_reservation_hash": (
                    receipt.authority_reservation_hash
                ),
                "terminal_authority_reservation_hash": (
                    terminal_reservation["reservation_hash"]
                ),
                "authority_reservation_state": (
                    durable_reservation.state.value
                ),
                "context_revision_observed": (
                    committed_attempt.context_provenance[
                        "context_revision"
                    ]
                ),
                "context_head_hash_observed": (
                    committed_attempt.context_provenance[
                        "context_head_hash"
                    ]
                ),
                "context_overlay_hash_observed": (
                    committed_attempt.context_provenance[
                        "context_overlay_hash"
                    ]
                ),
                "context_revision_at_execution": (
                    execution_overlay.context_revision
                ),
                "execution_context_provenance_hash": (
                    committed_attempt.context_provenance_hash
                ),
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
                "terminal_context_provenance_hash": terminal_context[
                    "provenance_hash"
                ],
                "execution_attestation_hash": (
                    attestation.attestation_hash
                ),
                "execution_attestation_path": attestation_path,
                "independent_execution_proof_hash": (
                    independent_proof.statement_hash
                ),
                "independent_execution_proof_path": proof_path,
                "independent_execution_proof_hash_path": proof_hash_path,
                "proof_verification_report_hash": (
                    proof_verification.report_hash
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
        }:
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
