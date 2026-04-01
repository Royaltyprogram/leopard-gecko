# Dispatch Failure Rollback

> Format: Checklist-driven implementation plan
> Goal: Ensure `worker.submit()` failures do not corrupt session state.

## Problem Summary

- Currently, the `submit()` and queue promotion paths change the session state to `running` first, then attempt worker dispatch.
- If `_dispatch_task()` then throws an exception, the session can remain in a `busy` state with `current_task_id` set.
- However, if `active_run_id` and `active_pid` are empty, subsequent `poll_runs()` cannot track this task.

## Completion Criteria for This Patch

- [ ] After dispatch failure, the session does not remain in an “untrackable running state”.
- [ ] Failed tasks return to a retryable queue state.
- [ ] If the failure occurred in a newly created session, clean up the session or revert it to idle state.
- [ ] The failure is explicitly recorded in `tasks.jsonl`.
- [ ] All three paths -- direct submit, session queue promotion, and global queue promotion -- use the same rollback rules.

## Adopted Rules

- On dispatch failure, revert the task to `queued_globally`.
- On dispatch failure, clean up the target session to `idle` or a removable state.
- `active_run_*` fields must be cleared.
- Failed tasks leave a `task_dispatch_failed` event.

Reasons for choosing these rules:

- Restoring to the front of the session queue as well increases the number of state combinations.
- In the current structure, reverting to the global queue is the simplest approach with the clearest recovery path.
- It naturally connects with the next patch, global queue auto promotion.

## Implementation Checklist

### 1. Wrap dispatch call sites to be exception-safe

- [ ] Wrap the `_dispatch_task()` call in `submit()` with `try/except`.
- [ ] Wrap `transition.next_dispatch` and the dispatch inside `_promote_next_global_task()` in `poll_runs()` the same way.
- [ ] Do not duplicate rollback logic; consolidate it in a helper like `_rollback_failed_dispatch(...)`.

### 2. Define the rollback helper's inputs clearly

- [ ] Inputs must include `task_id`, `session_id`, `created_session`, `promoted_from_queue`, `original_queue_source`.
- [ ] The helper modifies session state only within `sessions_repo.update(...)`.
- [ ] Connect a task queue status change hook so the task snapshot is also updated.

### 3. Fix per-session rollback rules

- [ ] Dispatch failure after creating a new session:
  Remove the session if it holds nothing other than that task.
- [ ] Dispatch failure on an existing idle session:
  Clear `current_task_id` and revert to `status=idle`.
- [ ] Failure after promoting the next task from session queue:
  Leave the session as `idle` and send the failed task to the front of the global queue.
- [ ] Failure after global queue promotion:
  Restore that task to the front of the global queue.

### 4. Add logs and events

- [ ] Add `task_dispatch_failed` event.
- [ ] The payload must include at least the following fields:
  - `session_id`
  - `task_id`
  - `source`
  - `created_session`
  - `error`
- [ ] If needed, do not create a separate `session_rollback` event; start with just `task_dispatch_failed`.

### 5. Define the error propagation policy

- [ ] On dispatch failure in direct submit, return the error to the user as-is.
- [ ] However, re-raise the exception only after the rollback is complete.
- [ ] Inside the poll loop, do not swallow exceptions; decide whether to record a failure count or log only for that iteration after rollback.

Recommendation:

- Submit path: re-propagate the exception
- Poll path: after rollback, only record the event and continue the loop

## Files to Change

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/task.py`
- `tests/test_pipeline.py`
- If needed, `tests/test_worker_loop.py`

## Test Scenarios

- [ ] If worker submit fails right after creating a new session, the session (if it remains) is idle with no active run information.
- [ ] After direct submit failure, the task remains as `queued_globally`.
- [ ] When the next task dispatch from session queue fails, cleanup of existing completed tasks is preserved.
- [ ] When global queue promotion dispatch fails, the queue order is not broken.
- [ ] After rollback, the next poll can promote the same task again.

## Implementation Order

1. Add failure event schema
2. Add `_rollback_failed_dispatch(...)` helper
3. Apply to the direct submit path
4. Apply to the poll completion / promotion path
5. Add rollback tests

## Out of Scope for This Patch

- A new complex retry scheduler
- Backoff policy
- Storing dispatch retry counts
- Per-session retry budget
