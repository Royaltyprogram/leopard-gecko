# Session Timeout And Lifecycle FAQ

> Format: Q&A
> Goal: Connect `session_idle_timeout_min` to actual lifecycle rules, and prevent blocked/dead sessions from permanently occupying capacity.

## Q1. What is the problem right now?

The config has `session_idle_timeout_min`, but the current code does not use this value to expire sessions.
As a result:

- Old idle sessions may continue to be counted as alive
- Blocked sessions keep consuming slots
- Dead transition rules remain limited to manual testing

## Q2. What is the core goal of this patch?

Three things:

1. Automatically transition stale sessions to `dead`.
2. On dead transition, move the queue and current task to a recoverable location.
3. Release sessions that “cannot actually be reused” from capacity calculations.

## Q3. When should a session be considered stale?

The basic rule is kept simple:

- `now - last_heartbeat > session_idle_timeout_min`

And per-status interpretation is as follows:

| Status | Action when determined stale |
|---|---|
| `idle` | Transition to `dead` |
| `blocked` | Transition to `dead` |
| `busy` + active run info exists | First check worker poll results; if progress is still unclear, candidate for `dead` transition |
| `busy` + no active run info | Immediately considered abnormal; candidate for `dead` transition |

## Q4. How are tasks handled during dead transition?

Choose the simplest rule:

- If `current_task_id` exists, move it to the front of the global queue
- Tasks that were in the session queue are also moved to the front of the global queue, preserving order
- Clear the session's `current_task_id`, `queue`, and `active_run_*`

Why global queue?

- There is no reason to keep tasks tied to a specific dead session.
- The scheduler can redistribute them to healthy sessions later.

## Q5. Should blocked sessions be converted to dead immediately?

No.

Initially, blocked status is maintained.
However, if the heartbeat has not been updated for a long time, it is demoted to dead.

This way:

- A short window for manual verification is allowed
- Indefinite slot occupation is prevented

## Q6. How should the capacity calculation function be changed?

It is safer to split into two layers:

- `live_session_count`: Maintain existing meaning or make minimal modifications
- `allocatable_session_count` or `routable_session_count`: A calculation closer to actual new task assignment possibility

Recommended direction:

- Slot availability decisions in routing and global promotion should be based on the state after stale cleanup.
- That is, clean up stale sessions first, then use the existing `live_session_count`.

This approach is the least invasive.

## Q7. Where should this logic run?

Two places are good:

1. Right after `poll_runs()` starts
2. Early in `submit()`, before the route decision

Reason:

- If the worker loop is running, automatic recovery occurs
- Even if the worker loop is slow or briefly paused, stale slots can be cleared at submit time

## Q8. What helpers should be added?

The two candidates are:

```python
def _expire_stale_sessions(self, state: SessionsState, config: AppConfig, now: datetime) -> ExpireResult:
    ...

def _requeue_dead_session_tasks(session: Session, state: SessionsState) -> list[str]:
    ...
```

`ExpireResult` only needs the following:

- `expired_session_ids`
- `requeued_task_ids`

## Q9. What events should be recorded?

Minimum events:

- `session_expired`
- `task_requeued_from_dead_session`

Payload example:

- `session_id`
- `previous_status`
- `reason=stale_timeout`
- `task_ids`

## Q10. How should tests be structured?

Required tests:

- A stale idle session is marked dead before submit, and creating a new session becomes possible
- A stale blocked session is marked dead during poll
- On dead transition, `current_task_id` and queue tasks move to the global queue
- After dead transition, session active run information is cleared
- After dead processing, capacity is recovered and global queue promotion becomes possible

## Q11. What does not need to be done in this patch?

- Adding fine-grained per-grace-period states
- Intermediate states like “suspect” or “recovering”
- Strengthening OS process-level liveness checks

For this phase, it is sufficient to focus on making the stale timeout actually work.
