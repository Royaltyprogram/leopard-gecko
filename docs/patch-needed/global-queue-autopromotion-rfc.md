# RFC: Global Queue Auto Promotion

## 1. Summary

Currently, global queue promotion only happens “when some run has just finished.”
This RFC defines a plan to change `poll_runs()` so that it advances the global queue on its own even when there are no active runs.

## 2. Motivation

The following state is actually possible:

- One session is idle
- Tasks remain in `global_queue`
- Active runs: 0

In this case, the current implementation does nothing.
The system is not stopped, but it cannot restart on its own.

## 3. Non-Goals

- Introducing a priority queue
- starvation-free scheduler
- A complex scheduler that fairly interleaves multiple tasks

## 4. Proposed Behavior

`poll_runs()` always performs the following two steps:

1. Collect and reflect active run states.
2. Regardless of the result, promote as many dispatchable global queue tasks as possible.

Promotion priority is maintained:

1. Reuse idle sessions
2. Create new sessions if remaining capacity is available
3. If neither is possible, keep waiting

## 5. API / Code Shape

Introduce a new helper.

```python
def _promote_dispatchable_global_tasks(self, config: AppConfig) -> int:
    ...
```

Intent:

- Returns the number of tasks actually dispatched in a single poll
- Can process multiple tasks via a `while` loop internally
- Stops the moment no more dispatches are possible

`poll_runs()` uses it at the end like this:

```python
poll_result.dispatched += self._promote_dispatchable_global_tasks(config)
```

## 6. Dispatch Limit Rule

A limit is needed to prevent infinite looping in a single poll.

Recommended rule:

- Promote only up to the number of idle sessions + remaining capacity at the start

Advantages of this approach:

- Does not attempt to start more work than the number of sessions in the same poll.
- Implementation is simple.

## 7. Error Handling

Dispatch failure follows the rollback rules from RFC 01 as-is.

- Even if one fails mid-promotion, already successful dispatches are kept
- Failed tasks are restored to the global queue
- Whether to continue in the same poll or stop immediately: start with “stop immediately” for simplicity

## 8. Invariants

After this change, the following must always hold:

- If there is an idle session and the global queue is not empty, after the next poll at least one of the following changes:
  - A task is dispatched.
  - A reason for being unable to dispatch remains.
- The global queue can advance even when the active run count is 0.

## 9. Implementation Notes

- `_promote_next_global_task()` can be kept, but its internal implementation should be changed so the new bulk helper reuses it.
- The existing post-completion promotion branch in `poll_runs()` can be simplified.
- Where possible, it is easier to read the flow if promotions are batched at the end of the poll in one pass.

## 10. Files To Change

- `src/leopard_gecko/orchestrator/pipeline.py`
- `tests/test_pipeline.py`
- If needed, `tests/test_worker_loop.py`

## 11. Test Plan

Required tests:

- 0 active runs + 1 idle session + 1 global queue -> 1 dispatch
- 0 active runs + 0 idle sessions + remaining capacity available -> dispatch after creating new session
- 0 active runs + capacity full -> 0 dispatches
- 2 global queue + 1 idle + 1 remaining capacity -> up to 2 dispatches in one poll
- First task dispatch fails during promotion -> end the same iteration after rollback

## 12. Migration / Rollout

There are no data format changes.
Only behavior changes.
Therefore, it can be completed as a pure code patch.

## 13. Open Question

Should multiple tasks be dispatched in one poll, or only one?

Recommended answer:

- Dispatch multiple

Reason:

- Throughput improves when remaining capacity is clear.
- If a separate daemon runs on a slow cycle, pulling out tasks one at a time is unnecessarily sluggish.
