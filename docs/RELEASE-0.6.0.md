# CDWAgent 0.6.0

Long database operations can now run as detached local jobs through MCP or CLI.
Submission returns immediately; agents monitor phase, heartbeat, row/byte counts,
elapsed time and advisory ETA. Results stream to private CSV/JSONL files and are
published as complete only on success. Cancellation, wall-clock and disk budgets,
worker-loss detection and explicit cleanup are supported. Credentials remain in
the connector/worker or OS keyring, outside tool arguments and job-status data.

The repository now includes a deterministic installable BRXT, lockfile, checksum,
bundled query-job skill, installation documentation, tests and release automation.
No-argument CLI invocation remains an MCP stdio server.

CDW patient lookups use an explicit identifier type to prevent ambiguous patient matches. Use id_type="PatientKey" for surrogate IDs. Preview limits are 1–1000 with truncation notices. Metadata counts are labeled estimates; cohort errors no longer trigger a misleading surrogate-key fallback. Legacy CSV export requires a new filename and publishes atomically. Schema tools now paginate and distinguish the bundled dictionary from live DB truth. Raw SQL audit logging requires explicit opt-in.

Validation includes offline lifecycle/security/domain tests and live small queries,
10,000-row real-table exports, 100,000-row generated transfers, parameterized
queries, EXPLAIN jobs, CLI monitoring after submit exits, cancellation, and the
BioRouter agent execution path with a deterministic local provider fixture.
Clinical tests selected constants from real tables, not patient details.

Jobs survive MCP/chat disconnects, not host shutdown. Interrupted exports require
explicit resubmission; arbitrary queries do not have automatic checkpoint/resume.
Network, permissions, database resource limits and disk exhaustion can still fail.
These limits are reported; this release does not promise every query can succeed.

Dependency locks were refreshed to patched releases, including cryptography 50+.
The old Intel macOS compatibility cap was removed; Intel installations without a
compatible wheel may require Rust/OpenSSL build prerequisites. pip-audit found no
known vulnerabilities in the resolved dependency set on the test platform.
