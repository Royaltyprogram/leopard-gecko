# Transactional Task Persistence Plan

> Goal: Reduce inconsistencies between `sessions.json` and `tasks.jsonl` so that tasks placed in the queue can always be restored.

## Problem

Currently, `submit()` updates `sessions.json` first and then appends `task_created` to `tasks.jsonl`.
If the process terminates between these two steps, the `task_id` remains in the session queue but the task body may be missing from the log.
In this state, task restoration fails during queue promotion.

## Target State

- The task body is durably persisted before being placed into the session queue.
- Even if `submit()` is interrupted midway, retry or recovery is possible afterward.
- Queue promotion logic no longer breaks due to missing `task_created` events.

## Implementation Approach

### Approach 1. Separate task snapshot store

The simplest method is to add a per-task snapshot store such as `tasks/<task_id>.json`.

- `task_created` events are kept as audit logs
- Actual restoration is performed from snapshot files, not the append-only log
- `_load_task()` reads from the snapshot store first, falling back to the log only when absent

This approach fits well with the current architecture and keeps the recovery path simple.

### Approach 2. Reorder the submission procedure

Change the `submit()` order as follows:

1. Create task
2. Save task snapshot
3. Append `task_created` event
4. Update `sessions.json`
5. Append `task_routed` event
6. Dispatch if needed

The key point is that the task itself must be persisted before the session references it.

## Files to Change

- `src/leopard_gecko/store/`
- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/task.py`
- `tests/test_pipeline.py`
- New test file: `tests/test_task_store.py`

## Detailed Implementation Plan

### 1. Add `TaskRepository`

- Role: save/load task snapshots
- Storage format: `data_dir/tasks/<task_id>.json`
- Interface:
  - `initialize()`
  - `save(task: Task) -> None`
  - `load(task_id: str) -> Task`
  - `exists(task_id: str) -> bool`

### 2. Inject task store into `Orchestrator`

- Add `self.task_repo`
- Initialize the task store in `init_storage()`
- `_load_task()` uses `task_repo.load()` as its primary path

### 3. Clean up submission order

- Save snapshot immediately after `Task` creation
- Then record the `task_created` event
- Then perform sessions mutation
- If sessions mutation fails, the task snapshot may become orphaned, but this state is recoverable

### 4. Design recoverable logging

- `task_created` is appended only after the snapshot is saved
- `task_routed` is appended only after sessions mutation succeeds
- Additional events like `task_dispatch_failed` can be added later if needed

## Test Plan

- Even if an exception occurs after saving the task snapshot but before the sessions update, the task must still be readable
- A task placed in the queue must be properly restored from its snapshot
- `_load_task()` must be able to restore from the snapshot even if the existing `task_created` log entry is missing
- Queue promotion must work identically to the current behavior after submitting multiple tasks

## Implementation Order

1. Add `TaskRepository`
2. Connect to `Orchestrator`
3. Change `submit()` save order
4. Change `_load_task()` path
5. Add failure scenario tests

## Non-Goals

- Full multi-file transaction implementation
- Introducing SQLite
- Restructuring the entire system to event sourcing

For the MVP, the combination of snapshot + append-only log is sufficient.
