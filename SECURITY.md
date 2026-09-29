# Security

Please do not open a public issue for a suspected credential leak, private-data exposure, approval bypass, or security vulnerability.

Until a dedicated security contact/process is published, use GitHub's private vulnerability reporting feature when available.

## Scope

Security-sensitive areas include:

- approval and FrozenEvidence boundaries
- provider/executor command construction
- Kubernetes and Temporal transport invocation
- release evidence and replay integrity
- secret/budget/policy handling
- accidental inclusion of production incidents, credentials, internal endpoints or customer data

Do not commit real credentials, production ReleaseEvidence, approval payloads, customer data, internal endpoints or secrets to examples or tests.
