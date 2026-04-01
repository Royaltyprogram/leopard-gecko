# Session Worktrees Plan

> Goal: Make each session work in an independent git checkout so that file changes from different sessions do not mix in a single working directory.

## Problem

Currently, worker dispatch always uses only the orchestrator's single `cwd`.

In this structure:

- Different sessions can modify the same set of files simultaneously
- Per-session branches/changes are not isolated
- It is difficult to determine "which checkout this session is working in" just by looking at the state file

To use multi-session as truly parallel work units, each session needs its own separate workspace.

## Target State

- Each session has its own dedicated git worktree when needed.
- Subsequent tasks routed to the same session reuse the same worktree.
- Workers always execute in the worktree path associated with the session.
- Even on dispatch failure or recovery, which worktree was used can be tracked.

## Design Principles

### 1. The worktree is part of the session identity

A session is no longer just a logical slot with only a `worker_context_id`.
The following two axes must be maintained together:

- agent/runtime context: `worker_context_id`
- filesystem context: `worktree_path`

In other words, "appending to the same session" means:

- Reusing the same Codex thread, and
- Reusing the same git worktree

### 2. Creation is lazy

Creating a session object does not require immediately creating a worktree.
The simplest point in time is **just before the first dispatch**.

This way:

- No need to pre-create a checkout for sessions that only sit in the global queue
- The rollback scope is clear when worker submit fails
- It naturally fits into the current `dispatch_with_rollback()` structure

### 3. No automatic cleanup in the first phase

Immediately deleting worktrees of dead/blocked sessions makes debugging and manual recovery difficult.
In the initial implementation, it is safer to keep worktrees and add a separate prune command later.

## Recommended Data Model Changes

Add the following fields to `Session`:

- `worktree_path: str | None`
- `worktree_branch: str | None`
- `worktree_base_ref: str | None`

This is sufficient:

- The actual execution path can be known
- The branch can be tracked
- The base ref from which it was derived can be restored

It is better not to add separate state enums like `workspace_mode` or `worktree_status` from the start.
If `worktree_path is None`, it can be interpreted as shared cwd mode.

## Recommended Configuration

Add only a small configuration to `AppConfig`.

```python
class WorktreeConfig(BaseModel):
    enabled: bool = False
    root_dir: str | None = None
    branch_prefix: str = "lg"
    base_ref: str | None = None
```

Description:

- `enabled`: session worktree feature on/off
- `root_dir`: root directory for creating worktrees. Recommended default is a separate directory outside the main repo
- `branch_prefix`: session branch prefix
- `base_ref`: if empty, uses the current HEAD or current branch as the base

The important point is to **not place the worktree root inside the current repo**.
In the current structure where the default data dir is `.leopard-gecko/`, creating worktrees under it results in nested checkouts that are cumbersome to manage.

Recommended default examples:

- main repo: `/repo/app`
- data dir: `/repo/app/.leopard-gecko`
- worktree root: `/repo/.leopard-gecko-worktrees/app`

## Proposed New Module

`src/leopard_gecko/worktree/manager.py`

Limit the responsibility to one thing:

- Ensure a worktree for a session and return path/branch information

Something roughly like the following is sufficient.

```python
class SessionWorktree(BaseModel):
    path: str
    branch: str
    base_ref: str
    created: bool = False


class WorktreeManager:
    def ensure(self, *, session_id: str) -> SessionWorktree:
        ...
```

This module is responsible for only:

- Checking whether the current `cwd` is a git repo
- Determining the worktree root
- Determining the branch name
- Reusing an existing worktree if present
- Creating one with `git worktree add` if not

## Orchestrator Integration

The key change is a single dispatch path in `pipeline.py`.

Currently:

- `_dispatch_task()` unconditionally passes `self.cwd` to the worker

After the change:

1. `session = _find_session(...)`
2. If the worktree feature is enabled, call `WorktreeManager.ensure(session_id=...)`
3. Save `worktree_path`, `worktree_branch`, `worktree_base_ref` to the session
4. For worker submit, use `cwd = Path(session.worktree_path) if session.worktree_path else self.cwd`

In other words, the worker adapter barely needs to change.
The center of the change is the orchestrator.

## Dispatch Rollback Rules

Rollback rules should be simple to match the current structure.

### New session + first worktree creation + dispatch failure

- Along with session creation rollback, only the just-created worktree can be removed

### Existing session + existing worktree reuse + dispatch failure

- Worktree is kept
- Only the task is returned to the global queue

### Existing session + worktree ensure failure

- Does not proceed to worker submit
- Handled by the current dispatch failure path

In short, you only need to know "whether a worktree was newly created in this dispatch".

## Git Branch Strategy

In the first phase, one branch per session is sufficient.

- Example branch name: `lg/<session_id>`
- base ref: config value if set, otherwise the current branch or `HEAD`

The important thing is not to create a new branch for every task.
This system wants **session isolation** rather than task isolation.

## Recovery and Observability

It is recommended to record the actual execution `cwd` in run metadata.

- `worker_runs/<session_id>/<task_id>.meta.json`
- Additional field: `cwd`

Then, even after a restart:

- Which worktree the execution ran in
- Whether the session state and disk state match

can be compared.

## Test Plan

The following minimum tests are sufficient.

### 1. Model serialization

- Whether the worktree fields of `Session` are saved/restored

### 2. Dispatch path

- Whether a session without a worktree uses the existing `cwd`
- Whether a session with a worktree passes that path to the worker

### 3. Worktree manager

- Whether `ensure()` properly returns path/branch in a git repo
- Whether an already existing worktree is not duplicated

### 4. Rollback

- Whether session rollback on first dispatch failure of a new session does not conflict with worktree state

## Implementation Order

1. Add worktree fields to the `Session` model
2. Add a small `WorktreeConfig` to config
3. Introduce `WorktreeManager`
4. Select per-session `cwd` in `_dispatch_task()`
5. Record `cwd` in run metadata
6. Strengthen rollback and tests

## Non-Goals

- Creating a new branch for every task
- Automatic merge/rebase
- Automatic dead session prune
- Copy-based workspace for non-git projects

At this stage, it is sufficient to cleanly establish "one session = one git worktree".
