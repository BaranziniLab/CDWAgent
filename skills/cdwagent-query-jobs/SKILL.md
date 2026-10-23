---
name: cdwagent-query-jobs
description: Plan, submit and monitor long read-only SQL Server queries and complete local exports through CDWAgent; use after timeouts or when result size or query cost is uncertain.
---

Use existing schema/concept/entity tools for discovery and small previews. Resolve
identifiers before joining or traversing. For full exports, unbounded scans, or a
previous timeout, call `CDW-submit_query_job`; it launches the CLI worker in
the background and returns a job ID immediately. Credentials are injected by
BioRouter. Never ask the user to paste credentials into chat or shell arguments.

1. Establish filters and projections. Prefer indexed anchors; avoid SELECT * and
   expensive counts solely for sizing. SQL Server parameters use `%(name)s`;
   Neo4j parameters use `$name`. Supply them in the parameters object.
2. If cost is uncertain, submit `mode="explain"` first and inspect the completed
   local plan. It does not execute the query. SQL Server SHOWPLAN may require
   additional permission; lack of a plan is not evidence of a cheap query.
3. Call `CDW-query_job_capacity` before admission. It reports `active_jobs`,
   `max_active_jobs` (2) and `available_slots`. Submit once with an explicit timeout
   budget (default 3600 seconds, maximum 86400) and disk budget (`max_bytes`,
   default 10 GB). Capacity is checked atomically again on submission; a
   `capacity_exceeded` rejection includes current capacity. Wait for a slot or
   cancel unwanted work, rather than retrying admission in a tight loop. Supply
   `expected_rows` only when already known or estimated, and label it provisional.
4. Call `CDW-wait_for_query_job` with the job ID and `timeout_seconds` from
   0 to 30. This is native bounded waiting; it does not require a shell sleep.
   `wait_timed_out=true` means the wait ended with the job still active, not that
   the job failed. Cancelling a wait leaves the job running. For an immediate
   snapshot, use `CDW-query_job_status`; if polling, respect its
   `recommended_poll_seconds` (5–60). Report phase, elapsed time, rows and bytes;
   unknown ETA during blocking execution is normal. Estimates are advisory.
5. Only `completed` means the export is complete. Use
   `CDW-read_query_job_result` with `job_id`, byte `offset` (initially 0) and
   `max_bytes` (4–65536). Follow `next_offset` until `eof=true`; offsets are bytes,
   not rows, and CSV records may span chunks. Do not send `result_path` to generic
   filesystem tools or request a wider filesystem permission. Keep patient-level
   exports local and return appropriate summaries. Large local analyses can use
   the CLI file; the MCP reader requires POSIX secure-open support and refuses
   unsupported platforms. Exports do not silently apply a preview row cap.
6. On `failed`, `timed_out` or `interrupted`, inspect sanitized `error_details`
   (`code`, `kind`, `message`, `phase`, `retryable`) and revise the plan before a
   new submission. A successful status lookup can report a failed job. The MCP
   `_meta['io.biorouter/operation']` envelope describes that operation outcome
   separately from lookup success. Partial files are incomplete; there is no
   automatic retry, keyset checkpoint or general-purpose resume. `retryable` is
   advisory and never triggers resubmission. Use `CDW-cancel_query_job` for
   unwanted work, then wait for a terminal state before replacing it.

CLI status needs no credentials: from the installed extension directory, run
`uv run cdwagent status JOB_ID` or `uv run cdwagent watch JOB_ID`.
A user running standalone CLI submissions can configure its separate OS-keyring
profile once with `uv run cdwagent auth`; BioRouter submissions already use its own
credential store and do not require that step. Never dump either credential store.
