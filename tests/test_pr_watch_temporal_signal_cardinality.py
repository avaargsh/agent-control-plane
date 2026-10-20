"""Operator History check: presence is not exactly one wake.

No fake success for a duplicate Signal, wrong Workflow ID, missing History
payloads or undecodable wake. Reconciliation's positive-existence semantics
remain intentionally distinct from release evidence.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from pr_watch_webhook import TemporalWebhookWake  # noqa: E402

SIGNAL = "pr-watch-webhook:" + "a" * 64
OTHER = "pr-watch-webhook:" + "b" * 64


def _event(name, payload=None):
    return SimpleNamespace(
        workflow_execution_signaled_event_attributes=SimpleNamespace(
            signal_name=name,
            input=SimpleNamespace(payloads=[] if payload is None else [payload]),
        ),
    )


def _witness(events, *, broken=False):
    class Converter:
        async def decode(self, payloads):
            if broken:
                raise ValueError("invalid Temporal payload")
            return payloads

    class Handle:
        async def fetch_history(self):
            return SimpleNamespace(events=events)

    class Client:
        data_converter = Converter()

        def get_workflow_handle(self, name):
            assert name == witness.workflow
            return Handle()

    async def factory():
        return Client()

    witness = TemporalWebhookWake(
        "isolated-temporal:7233", "testing", "operator-user", "goal-1",
        client_factory=factory,
    )
    return witness


@pytest.mark.parametrize(("events", "expected"), [
    ([], False),
    ([_event("wake", SIGNAL)], True),
    ([_event("wake", OTHER)], False),
    ([_event("wake", SIGNAL), _event("wake", SIGNAL)], False),
    ([_event("wake", SIGNAL), _event("wake", OTHER)], True),
    ([_event("other-signal", SIGNAL), _event("wake", SIGNAL)], True),
])
def test_exact_history_wake_cardinality(events, expected):
    witness = _witness(events)
    assert witness.prove_exactly_once(SIGNAL) is expected
    # ACK ambiguity reconciliation needs only a positive History witness.
    assert witness.prove(SIGNAL) is any(
        e.workflow_execution_signaled_event_attributes.signal_name == "wake"
        and e.workflow_execution_signaled_event_attributes.input.payloads == [SIGNAL]
        for e in events
    )


def test_duplicate_history_cannot_be_accepted_as_exactly_once():
    witness = _witness([_event("wake", SIGNAL), _event("wake", SIGNAL)])
    assert witness.prove(SIGNAL) is True
    assert witness.prove_exactly_once(SIGNAL) is False


def test_unreadable_wake_history_cannot_support_exact_cardinality():
    witness = _witness([_event("wake", SIGNAL)], broken=True)
    with pytest.raises(ValueError, match="invalid Temporal payload"):
        witness.prove_exactly_once(SIGNAL)
    witness = _witness([_event("wake", None)])
    with pytest.raises(ValueError, match="payload missing"):
        witness.prove_exactly_once(SIGNAL)


@pytest.mark.parametrize("invalid", [
    "", "invalid", 3, None, "pr-watch-webhook:" + "a" * 63,
])
def test_unscoped_signal_ids_never_count_as_proven(invalid):
    assert _witness([_event("wake", SIGNAL)]).prove_exactly_once(invalid) is False
