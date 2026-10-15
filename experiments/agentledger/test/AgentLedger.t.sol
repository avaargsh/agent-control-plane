// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

import "../src/AgentLedger.sol";

interface Vm {
    function prank(address who) external;
}

contract AgentLedgerTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    AgentLedger ledger;
    bytes32 constant TASK = keccak256("task-1");
    bytes32 constant DESCRIPTION = keccak256("description");
    bytes32 constant REF = keccak256("settlement-1");
    bytes32 constant PROOF = bytes32(uint256(42));
    address constant ALICE = address(0xA11CE);

    function setUp() public {
        ledger = new AgentLedger(address(this));
    }

    function testHappyPath() public {
        ledger.createTask(TASK, DESCRIPTION, 100);
        AgentLedger.Task memory created = ledger.getTask(TASK);
        require(created.status == AgentLedger.Status.Created, "not created");
        require(created.maxBudget == 100, "budget");

        ledger.recordPayment(TASK, ALICE, 20, REF);
        AgentLedger.Task memory paid = ledger.getTask(TASK);
        require(paid.status == AgentLedger.Status.Paid, "not paid");
        require(paid.payer == ALICE && paid.amount == 20, "payment");

        ledger.submitEvidence(TASK, PROOF);
        AgentLedger.Task memory proven = ledger.getTask(TASK);
        require(proven.status == AgentLedger.Status.Proven, "not proven");
        require(proven.evidenceHash == PROOF, "wrong evidence");
    }

    function testNoEvidenceBeforePayment() public {
        ledger.createTask(TASK, DESCRIPTION);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSelector(ledger.submitEvidence.selector, TASK, PROOF)
        );
        require(!ok, "unpaid task accepted evidence");
    }

    function testOnlyRecorderCanAttestPayment() public {
        ledger.createTask(TASK, DESCRIPTION);
        vm.prank(ALICE);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSelector(ledger.recordPayment.selector, TASK, ALICE, 1, REF)
        );
        require(!ok, "unauthorized payment");
    }

    function testBudgetCap() public {
        ledger.createTask(TASK, DESCRIPTION, 10);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSelector(ledger.recordPayment.selector, TASK, ALICE, 11, REF)
        );
        require(!ok, "exceeded cap");
        require(!ledger.usedSettlementRefs(REF), "ref consumed after revert");
    }

    function testSettlementRefCannotReplayAcrossTasks() public {
        ledger.createTask(TASK, DESCRIPTION);
        bytes32 other = keccak256("task-2");
        ledger.createTask(other, DESCRIPTION);
        ledger.recordPayment(TASK, ALICE, 1, REF);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSelector(ledger.recordPayment.selector, other, ALICE, 1, REF)
        );
        require(!ok, "duplicate settlement ref");
    }

    function testEvidenceCannotBeChangedAfterProven() public {
        ledger.createTask(TASK, DESCRIPTION);
        ledger.recordPayment(TASK, ALICE, 1, REF);
        ledger.submitEvidence(TASK, PROOF);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSelector(ledger.submitEvidence.selector, TASK, bytes32(uint256(123)))
        );
        require(!ok, "evidence mutated");
    }

    function testOtherActorCannotSubmitEvidence() public {
        ledger.createTask(TASK, DESCRIPTION);
        ledger.recordPayment(TASK, ALICE, 1, REF);
        vm.prank(ALICE);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSelector(ledger.submitEvidence.selector, TASK, PROOF)
        );
        require(!ok, "unapproved actor submitted evidence");
    }

    function testCannotCreateSameTaskTwice() public {
        ledger.createTask(TASK, DESCRIPTION);
        (bool ok,) = address(ledger).call(
            abi.encodeWithSignature("createTask(bytes32,bytes32)", TASK, DESCRIPTION)
        );
        require(!ok, "duplicate task");
    }
}
