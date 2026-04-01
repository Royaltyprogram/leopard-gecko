# Run Recovery Plan

> Goal: Even when the orchestrator process restarts, track in-progress worker runs as accurately as possible and avoid incorrectly marking already-completed work as `failed`.

## Problem

Currently, `CodexAdapter` stores running subprocesses in the in-memory `self.processes`.
After a process restart, this information is lost, and `poll()` only checks for PID existence or assumes `exit_code=1` for terminated processes.
As a result, runs that actually completed successfully may appear as failures at recovery time.

## Target State

- Existing run states can be restored as fully as possible even after a restart.
- Terminated runs are classified as success/failure based on output files and exit information.
- The in-memory cache is purely an optimization; the source of truth is the on-disk state.

## Implementation Approach

### Approach 1. Add run metadata files

Save run metadata as a separate JSON file for each dispatch.

- Example path: `worker_runs/<session_id>/<task_id>.meta.json`
- Stored fields:
  - `run_id`
  - `task_id`
  - `session_id`
  - `pid`
  - `started_at`
  - `worker_context_id`
  - `output_path`
  - `status`

### Approach 2. Add exit result files

Allow recording the exit code in a separate file after a subprocess finishes.

- Example path: `worker_runs/<session_id>/<task_id>.exit.json`
- Stored fields:
  - `exit_code`
  - `finished_at`

`poll()` can determine termination status from this file even when the in-memory dict is empty.

## Files to Change

- `src/leopard_gecko/adapters/codex.py`
- `src/leopard_gecko/adapters/base.py`
- `src/leopard_gecko/orchestrator/pipeline.py`
- `tests/test_workers.py`
- New test file: `tests/test_run_recovery.py`

## Detailed Implementation Plan

### 1. Write run metadata

- Create meta file immediately after successful `submit()`
- Add metadata path field to `WorkerSubmission` if needed
- Continue storing `run_id`, `pid`, `output_path` in the session as before

### 2. Add recovery path to `poll()`

Change the `CodexAdapter.poll()` order as follows:

1. Use in-memory `self.processes` first if available
2. If not available, check meta file and output file
3. If PID is alive, set `is_running=True`
4. If PID is gone and exit file exists, use that exit code
5. If PID is gone and only the last message exists, define a separate conservative `exit_code=0/unknown` policy

### 3. Determine exit code recording method

The simplest implementation is to use a subprocess wrapper shell.

- `codex exec ...`
- After termination, record `$?` to the exit file

This way, the exit code can be trusted even after a restart.

### 4. Policy for ambiguous states

When an output file exists but there is no exit information and no PID, do not immediately mark it as `failed`.

- Option 1: Introduce an `unknown_terminated` state internally
- Option 2: Use `blocked` instead of `failed` and require manual verification

For the MVP, sending to `blocked` is the safer choice.

## Test Plan

- Submit followed by poll within the same process should maintain existing behavior
- Polling an existing run after creating a new adapter instance should still restore state
- When an exit file exists, its exit code should be used as-is
- Conservative state transitions should be verified when there is no PID and no exit information

## Implementation Order

1. Define run meta/exit file format
2. Save meta in `CodexAdapter.submit()`
3. Add exit code recording wrapper
4. Add recovery path to `poll()`
5. Add restart scenario tests

## Non-Goals

- Supporting all arbitrary external worker processes
- Perfect per-OS process state tracking

At this stage, the focus is on reliably recovering a single `codex` worker.
