# Task Snapshot Sync Plan

> Format: Phased execution plan
> Goal: Ensure `TaskRepository` snapshots stay in sync with the actual queue/runtime state.

## Background

Currently, task snapshots are only updated at creation time and right after submit.
However, the actual lifecycle continues to change after that.

- queued in session
- queued globally
- running
- completed
- failed
- interrupted

If snapshots are stale, they cannot be trusted as a hot path store.

## Phase 1. Fix the list of state change points

First, confirm from a code perspective “when must the task snapshot be rewritten.”

### Target Transitions

1. Route confirmed right after submit
2. Entering global queue
3. Entering session queue
4. Maintaining running after successful dispatch
5. Restoring queued_globally after dispatch failure
6. run completed
7. run failed
8. run blocked / interrupted
9. Promoting next task from session queue
10. Redistributing from dead session to global queue

### Deliverables for This Phase

- 1 transition table
- Mapping of which function each transition occurs in

## Phase 2. Consolidate task snapshot update paths into one place

The key here is “not calling `task_repo.save()` directly from various places whenever needed.”

Recommended helper:

```python
def _update_task_snapshot(
    self,
    task_id: str,
    *,
    queue_status: QueueStatus | None = None,
    routing: TaskRouting | None = None,
) -> Task:
    ...
```

Principles:

- Full object overwrite only inside the helper
- Call sites pass only the needed state

## Phase 3. Align the order of session mutation and task snapshot mutation

Recommended order:

1. Confirm session state first with `sessions_repo.update(...)`
2. Update the task snapshot based on that transition result
3. Append events last

Reason for recommending this order:

- The actual position in the queue is determined by the session state.
- It is less tangled to treat the snapshot as derived state that reflects that source of truth.

## Phase 4. Extend the Task model only as much as needed

Even with the current model, the primary goal is achieved if only `queue_status` and `routing` are kept in sync.
Minimize model extensions in this patch.

### Keep

- `queue_status`
- `routing`

### Defer

- Completion summary
- Final exit code
- Last session_id history

This information can be added later if needed, but for now just restore snapshot reliability.

## Phase 5. Specify per-lifecycle reflection rules

| Situation | Task snapshot reflection |
|---|---|
| Entered existing session queue | `queued_in_session` |
| Entered global queue | `queued_globally` |
| Dispatch success | `running` |
| Run completed | `completed` |
| Run failed | `failed` |
| Manual recovery needed | Do not create a new enum instead of `failed`; decide whether to use `failed` or maintain current behavior |
| Redistributed from dead session | `queued_globally` |

One decision is needed here.

### Should interrupted have a separate entry in the task snapshot?

Recommended answer:

- Do not do this in this patch.

Reason:

- `TaskHistoryStatus.INTERRUPTED` is a session-local detail.
- Expanding the `QueueStatus` enum has a wide impact.
- For now, it is simpler to normalize to `queued_globally` on global redistribution.

## Phase 6. Rewrite tests centered on state transitions

Required test set:

1. After submit, snapshot matches route result
2. When a queued task is promoted, snapshot changes to `running`
3. After completed, snapshot is `completed`
4. After failed, snapshot is `failed`
5. After dispatch rollback, snapshot is `queued_globally`
6. After dead session redistribution, snapshot is `queued_globally`

## Files to Change

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/task.py`
- `src/leopard_gecko/store/task_repo.py`
- `tests/test_pipeline.py`

## Final Completion Criteria

- All tasks in `TaskRepository` reflect the last known queue/runtime state.
- The result of `task_repo.load(task_id)` alone can be trusted for basic state in UI or debugging output.
- Lifecycle tests also pass based on snapshots.
