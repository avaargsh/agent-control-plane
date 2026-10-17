// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.24;

/// @title AgentLedger (hackathon experiment)
/// @notice Anchors task metadata, a trusted payment receipt reference, and one evidence digest.
/// @dev Payment recording is an ATTESTATION by a trusted recorder, not on-chain
///      verification of USDC settlement. No funds are held by this contract.
contract AgentLedger {
    enum Status { None, Created, Paid, Proven }

    struct Task {
        address creator;
        address payer;
        uint256 amount;
        uint256 maxBudget;
        bytes32 descriptionHash;
        bytes32 evidenceHash;
        bytes32 settlementRef;
        Status status;
        uint64 createdAt;
        uint64 paidAt;
        uint64 provenAt;
    }

    error ZeroValue();
    error TaskAlreadyExists();
    error TaskNotFound();
    error InvalidStatus();
    error RecorderOnly();
    error UnauthorizedExecutor();
    error SettlementAlreadyRecorded();
    error BudgetExceeded();

    address public immutable paymentRecorder;
    mapping(bytes32 => Task) private tasks;
    mapping(bytes32 => bool) public usedSettlementRefs;

    event TaskCreated(bytes32 indexed taskId, address indexed creator, bytes32 descriptionHash, uint256 maxBudget);
    event PaymentAttested(bytes32 indexed taskId, address indexed payer, uint256 amount, bytes32 indexed settlementRef);
    event EvidenceAnchored(bytes32 indexed taskId, bytes32 indexed evidenceHash, address indexed submitter);

    constructor(address recorder) {
        if (recorder == address(0)) revert ZeroValue();
        paymentRecorder = recorder;
    }

    function createTask(bytes32 taskId, bytes32 descriptionHash) external {
        _createTask(taskId, descriptionHash, 0);
    }

    /// @param maxBudget Upper bound for the trusted recorder's payment attestation.
    ///                  This does NOT cap the payer's wallet spending by itself.
    ///                  Zero means no on-chain amount cap.
    function createTask(bytes32 taskId, bytes32 descriptionHash, uint256 maxBudget) external {
        _createTask(taskId, descriptionHash, maxBudget);
    }

    function _createTask(bytes32 taskId, bytes32 descriptionHash, uint256 maxBudget) internal {
        if (taskId == bytes32(0) || descriptionHash == bytes32(0)) revert ZeroValue();
        if (tasks[taskId].status != Status.None) revert TaskAlreadyExists();
        Task storage task = tasks[taskId];
        task.creator = msg.sender;
        task.descriptionHash = descriptionHash;
        task.maxBudget = maxBudget;
        task.status = Status.Created;
        task.createdAt = uint64(block.timestamp);
        emit TaskCreated(taskId, msg.sender, descriptionHash, maxBudget);
    }

    /// @notice Only a trusted, off-chain settlement verifier may attest a payment.
    /// @dev Caller MUST verify network, token, transaction finality, payee, amount,
    ///      and binding to taskId BEFORE invoking. Unique settlementRef is a
    ///      replay guard but does NOT independently prove the payment happened.
    function recordPayment(
        bytes32 taskId,
        address payer,
        uint256 amount,
        bytes32 settlementRef
    ) external {
        if (msg.sender != paymentRecorder) revert RecorderOnly();
        Task storage task = tasks[taskId];
        if (task.status == Status.None) revert TaskNotFound();
        if (task.status != Status.Created) revert InvalidStatus();
        if (payer == address(0) || amount == 0 || settlementRef == bytes32(0)) revert ZeroValue();
        if (usedSettlementRefs[settlementRef]) revert SettlementAlreadyRecorded();
        if (task.maxBudget != 0 && amount > task.maxBudget) revert BudgetExceeded();
        usedSettlementRefs[settlementRef] = true;
        task.payer = payer;
        task.amount = amount;
        task.settlementRef = settlementRef;
        task.status = Status.Paid;
        task.paidAt = uint64(block.timestamp);
        emit PaymentAttested(taskId, payer, amount, settlementRef);
    }

    /// @notice Evidence is a hash anchor only, not independent execution proof.
    function submitEvidence(bytes32 taskId, bytes32 evidenceHash) external {
        Task storage task = tasks[taskId];
        if (task.status == Status.None) revert TaskNotFound();
        if (task.status != Status.Paid) revert InvalidStatus();
        if (msg.sender != task.creator && msg.sender != paymentRecorder) revert UnauthorizedExecutor();
        if (evidenceHash == bytes32(0)) revert ZeroValue();
        task.evidenceHash = evidenceHash;
        task.status = Status.Proven;
        task.provenAt = uint64(block.timestamp);
        emit EvidenceAnchored(taskId, evidenceHash, msg.sender);
    }

    function getTask(bytes32 taskId) external view returns (Task memory task) {
        task = tasks[taskId];
        if (task.status == Status.None) revert TaskNotFound();
    }
}
