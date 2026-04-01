# Global Queue Session Promotion Plan

> Goal: Allow tasks queued in the global queue to be executed by creating a new session when no idle sessions exist but capacity remains.

## Problem

Currently, global queue promotion only supports reusing idle sessions.
When there are no sessions at all or all sessions are dead, the global queue may remain waiting indefinitely even though `max_terminal_num` still has room.

## Target State

- Tasks in the global queue are promoted with the following priority:
  1. Reuse an idle session
  2. Create a new session if capacity remains
  3. Keep in queue if neither is possible

- The submit path and poll path share the same session creation rules.

## Implementation Approach

### Approach 1. Organize global queue promotion logic into a route-like policy

Currently, `_reserve_global_dispatch()` only looks for idle sessions.
Generalize this logic to handle all of the following in one pass:

- Select an idle session
- Determine whether a new session can be created
- Return a dispatch request

### Approach 2. Extract a shared session creation helper

Introduce a common helper so that session creation during submit and session creation during global queue promotion do not diverge.

- `_start_task_in_new_session(...)`
- `_start_task_in_existing_idle_session(...)`

Separating boundaries this way reduces duplication and makes testing easier.

## Files to Change

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/session.py`
- `tests/test_pipeline.py`

## Detailed Implementation Plan

### 1. Introduce a live session count function

- Add a helper that computes the number of non-dead sessions
- Align submit validation and global promotion to use the same criteria

### 2. Extend the global queue promotion procedure

Change `_reserve_global_dispatch()` to the following order:

1. Check if the global queue is empty
2. If an idle session exists, attach the task to that session
3. If not, compare the live session count to `max_terminal_num`
4. If capacity remains, create a new session and attach the task
5. Otherwise, return `None`

### 3. Differentiate promotion events

Keep the `source` field of `task_promoted_from_queue`, but add extra metadata when a new session is created.

- `source=global`
- `created_session=true`

This makes subsequent debugging easier.

### 4. Account for dead sessions

Exclude dead sessions from capacity calculations.
If needed, add a separate dead session cleanup routine later.

## Test Plan

- If an idle session exists, the task should attach to that session as before
- If no idle session exists and capacity remains, a new session should be created
- If capacity is full, the task should remain in the global queue
- Even when only dead sessions exist, creating a new session should be possible

## Implementation Order

1. Extract session creation helper
2. Extend `_reserve_global_dispatch()`
3. Enrich event payload
4. Add promotion-related tests

## Non-Goals

- Re-evaluating the global queue through the router
- Priority queues
- Starvation prevention scheduler

At this stage, the focus is solely on "naturally unblocking a stuck global queue".
