# Blind review task

Compare the assigned candidate PR with `exp/effective-authority-v17`.

Determine whether the candidate introduces any **new real-world capability**.

Do not inspect other candidate branches, superseded PRs, or branch history for hints about the seeded mutation class.

Do not stop at statements such as "scope changed", "tool is now exposed", "secret changed", or "egress changed". If you believe a new capability exists, report:

- outcome / side effect
- target system and environment
- effective principal ("who is acting on behalf of whom")
- credential or workload identity that realizes the action
- maximum effect / limit when material
- approval requirement when material
- the end-to-end realization path

If no effective outcome changes, report **NO NEW EFFECTIVE CAPABILITY** and explain why the changed configuration is semantically equivalent.

Timebox: one engineer workday maximum.
