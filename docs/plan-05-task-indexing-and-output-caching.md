# Task Indexing And Output Caching Plan

> Goal: Prevent `poll()` and queue promotion costs from degrading linearly as the number of tasks and worker output grows.

## Problem

Currently, two areas get slower as they grow:

1. Task restoration
   Reads the entire `tasks.jsonl` and searches in reverse order to find `task_created`.

2. Worker context restoration
   Reads the entire run output jsonl to the end on every poll to find the `worker_context_id`.

This is fine initially, but as the number of tasks and output size grow, polling costs increase dramatically.

## Target State

- Task restoration is handled in near-O(1) time.
- Worker context and last message are read without rescanning the entire output.
- The append-only log is kept for audit purposes but is not read directly in the hot path.

## Implementation Approach

### Approach 1. Use task snapshots as the hot path

Use the task snapshot store added in `plan-01` as the primary lookup path.
This eliminates the need for a full `tasks.jsonl` scan during queue promotion.

### Approach 2. Add worker state sidecar files

Maintain a small state file separate from the worker output.

- Example path: `worker_runs/<session_id>/<task_id>.state.json`
- Fields:
  - `worker_context_id`
  - `last_message`
  - `updated_at`

`poll()` reads this sidecar first instead of scanning the entire jsonl.

### Approach 3. Minimize output parsing

The ideal structure would update incrementally only when new events are appended.
For the MVP, start more simply:

- Create state file on dispatch
- Read only the tail of the output file on poll, or
- Have the wrapper directly update the last message/state file on termination

## Files to Change

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/adapters/codex.py`
- `src/leopard_gecko/store/`
- `tests/test_workers.py`
- `tests/test_pipeline.py`

## Detailed Implementation Plan

### 1. Replace task lookup path

- `_load_task()` uses the snapshot store
- `tasks.jsonl` scanning is reduced to a fallback or debug-only path

### 2. Introduce worker state file

`CodexAdapter` manages the following files:

- `.state.json`
- `.last_message.txt`
- `.exit.json` if needed

`worker_context_id` and `last_message` are consolidated and stored in the state file.

### 3. Optimize `poll()` read order

`poll()` follows this priority:

1. state file
2. last message file
3. output jsonl fallback

That is, it reads the smallest files first and only accesses the large jsonl when truly necessary.

### 4. Extract output parsing helper

Extract file reading logic from `codex.py` into helpers.

- `load_run_state_files(...)`
- `parse_output_for_context_id(...)`

This separation makes testing and future replacement easier.

## Test Plan

- Verify that `_load_task()` prioritizes the snapshot path
- Verify that when a state file exists, the entire output jsonl does not need to be read
- Verify that context id and last message are properly restored from the sidecar
- Verify that existing fallback parsing works when the sidecar is missing

## Implementation Order

1. Connect task snapshot store to the hot path
2. Define run state sidecar format
3. Optimize `CodexAdapter.poll()` read order
4. Extract fallback parsing helper
5. Add performance regression prevention tests

## Non-Goals

- Precise benchmarking framework
- Large-scale log store replacement
- Full streaming parser

At this stage, it is sufficient to achieve the goal of "avoiding full file scans in the hot path".
