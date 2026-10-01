#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone

from agent_control_plane.cli_runtime_transports import KubectlDeploymentApi
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
from agent_control_plane.state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    EvidenceBundle,
    EvidenceItem,
    ExecutionFence,
    ExecutionLease,
    OutcomeCondition,
    OutcomeContract,
    Principal,
    ResourceIdentity,
    StateTransition,
    canonical_digest,
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
    if policy_decision.effect is not PolicyEffect.PERMIT:
        raise RuntimeError(
            f"live scale denied: {policy_decision.reasons}"
        )

    approval_hash = canonical_digest(
        {
            "principal": "ci/kind-approver",
            "transition_hash": transition.transition_hash,
            "action_hash": action.action_hash,
            "evidence_hash": evidence.manifest_hash,
            "policy_version": policy_version,
            "decision_hash": policy_decision.decision_hash,
        }
    )
    authorization = AuthorizationBinding.seal(
        transition=transition,
        action=action,
        policy_version=policy_version,
        policy_decision_hash=policy_decision.decision_hash,
        approval_hash=approval_hash,
        principal=agent,
        expires_at=started_at + timedelta(minutes=5),
    )
    holder = Principal(
        type="controller",
        subject="ci/kind-controller",
    )
    lease = ExecutionLease(
        lease_id="kind-live-lease-001",
        resource_uid=resource.resource_uid,
        holder=holder,
        epoch=1,
        acquired_at=started_at,
        expires_at=started_at + timedelta(minutes=5),
    )
    fence = ExecutionFence.bind(
        transition=transition,
        action=action,
        authorization=authorization,
        lease=lease,
    )

    receipt = KubernetesDeploymentScaleProvider(api).execute(
        transition=transition,
        evidence=evidence,
        outcome_contract=outcome,
        action=action,
        authorization=authorization,
        fence=fence,
        active_lease=lease,
        caller=holder,
        now=datetime.now(timezone.utc),
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
            summary = {
                "transition_id": transition.transition_id,
                "transition_hash": transition.transition_hash,
                "action_hash": action.action_hash,
                "policy_input_hash": policy_input.input_hash,
                "policy_decision_hash": policy_decision.decision_hash,
                "authorization_hash": authorization.authorization_hash,
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
