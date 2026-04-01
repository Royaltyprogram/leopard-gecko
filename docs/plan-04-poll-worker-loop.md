# Poll Worker Loop Plan

> Goal: Remove the dependency on manual `lg poll` execution and make run completion handling and queue promotion proceed automatically.

## Problem

Currently, lifecycle transitions depend on `lg poll` invocations.
If the user or an external supervisor does not run poll periodically:

- Completion reflection
- Executing the next task in the session queue
- Global queue promotion
- Heartbeat updates

All of these stall.

## Target State

- The poll loop is automatically maintained even during normal usage.
- Even if the CLI only provides one-shot submit, a background loop continues advancing state in the same data dir.
- Manual `lg poll` remains as a debugging/operations fallback.

## Implementation Approach

### Approach 1. Add a separate `lg worker` command

The simplest and most predictable approach is to make the long-running loop an explicit command.

- `lg worker`
- Calls `orchestrator.poll_runs()` at regular intervals
- Handles multiple active runs at once
- Exits gracefully upon receiving a termination signal

This approach is simpler and easier to test than a hidden daemon.

### Approach 2. Leave automatic startup as an option for later

For now, it is better to operate as an explicit process rather than silently spawning a worker on submit.
For the MVP, observability and simplicity are more important.

## Files to Change

- `src/leopard_gecko/cli/main.py`
- `src/leopard_gecko/orchestrator/pipeline.py`
- New file: `src/leopard_gecko/orchestrator/worker_loop.py`
- `tests/test_pipeline.py`
- New test file: `tests/test_worker_loop.py`

## Detailed Implementation Plan

### 1. Add worker loop abstraction

Place a thin loop in `worker_loop.py` like the following:

- `run_worker_loop(orchestrator, interval_sec, once=False) -> int`

Responsibilities:

- `poll_runs()`
- Result aggregation
- sleep
- signal handling

### 2. Add CLI command

Add the following command to `main.py`:

- `lg worker`
- Options:
  - `--interval-sec`
  - `--once`
  - `--data-dir`

`--once` can replace or internally reuse the current `poll`.

### 3. Idle backoff policy

A simple backoff that increases the polling interval when there are no active runs and the global queue is empty can be added.
However, keeping a fixed interval for the first version is simpler.

### 4. Clean up status output

The worker loop should only leave simple, human-readable logs.

- `running`
- `completed`
- `failed`
- `dispatched`

Avoid excessive detailed output.

## Test Plan

- In `--once` mode, behavior should be identical to the current `poll`
- Verify that a completed task automatically dispatches the next queue task
- Verify that the loop exits without exceptions even in an empty state
- Minimize interrupt signal handling tests to a reasonable scope

## Implementation Order

1. Extract worker loop function
2. Add `lg worker` CLI
3. Clean up common output paths with `poll`
4. Add loop tests

## Non-Goals

- OS service registration
- Full daemon supervisor
- Multi-process clustering

At this stage, it is sufficient to achieve "the system moves forward even without manual poll".
