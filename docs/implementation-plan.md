# Leopard Gecko Implementation Plan

> This document is based on [`init.md`](./init.md) and the current codebase, intended to pin down the current implementation state and the next implementation order.
> The goal is to naturally transition to the context router architecture intended by `init.md` while maintaining the worker runtime loop.

---

## 1. Current Codebase State

The current code already has the following implemented.

- `Task`, `Session`, `AppConfig` models
- `config.json`, `sessions.json`, `tasks.jsonl` storage
- Task creation and session reflection on `submit()`
- Worker `submit + poll` contract
- Run completion/failure reflection loop
- Automatic start of next task from session queue
- Global queue promotion when an idle session occurs

In other words, the current state is:

- Beyond a simple submit-only skeleton
- Has a minimal runtime lifecycle in place

However, the routing stage still differs from the `init.md` baseline.

---

## 2. Key Mismatches Against `init.md`

### 2.1 The router is not an independent component

Currently, inside `Orchestrator.submit()`:

1. Task creation
2. `task_note` generation
3. Route decision

all happen at once.

In other words, the context agent role is not separated into an independent boundary.

### 2.2 `task_note` is not a substantive routing memo

Currently, `task_note` is closer to a fixed-template-based preview.

- The format of a short internal memo is correct
- But it is not actually used as an important basis for routing decisions

### 2.3 Current routing is heuristic token matching

Current routing is effectively:

- New `user_prompt`
- Existing `task_history`'s `user_prompt`

selecting a session only based on string overlap between these.

This differs from what `init.md` intended:

- Referencing task note
- Checking session state
- Assessing context rot risk
- Determining whether it's the same task axis

### 2.4 `init.md`'s terminal session description differs from the current runtime model

The current code uses generic runtime fields.

- `worker_context_id`
- `active_run_id`
- `active_pid`
- `last_run_output_path`

Meanwhile, `init.md` describes things centered on `terminal_id`.

This is more of a perspective difference between documentation and implementation rather than a functional problem.

---

## 3. Core Goals for the Next Phase

The key for the next phase is not to make the worker lifecycle more complex.

The key is:

- Separate `task_note` generation and route decision
- Make routing an independent context component
- Keep the existing heuristic router as a fallback
- Lock down the structure so that a thin OpenAI router per `init.md` can be attached

In other words, the center of the next phase is **router refactor, not worker refactor**.

---

## 4. Boundaries to Lock Down

In the next phase, the following three things will be separated.

### 4.1 `TaskNotePort`

- Input: `user_prompt`
- Output: short `task_note`
- Role: generate internal memo for routing
- Forbidden: rewriting prompts for worker execution

### 4.2 `ContextRouter`

- Input:
  - `task`
  - `config`
  - session snapshot
  - global queue size
- Output: `RouteDecision`
- Role:
  - Assign to existing session
  - Create new session
  - Global queue decision

### 4.3 `WorkerPort`

- Input: actual `user_prompt` to execute
- Output: `WorkerSubmission`, `WorkerRunState`
- Role: start execution and query status

Once these boundaries are separated, `Orchestrator.submit()` will only have a coordinator role.

---

## 5. Router Input Scope

In the `init.md` MVP, the router is not a heavy agent that investigates the entire codebase.

It's a dispatcher, not a writer.

Therefore, router input is limited to roughly the following.

- session id
- session status
- current task id
- queue length
- recent N `task_history` entries
- recent summary

What is NOT done in this phase:

- Free code search tool
- Multiple file read tool calls
- Full repo exploration planner
- Complex memory schema

In other words, the first OpenAI router should be a **session registry-based lightweight judge**.

---

## 6. Proposed Interface

```python
class SessionSnapshot(BaseModel):
    session_id: str
    status: SessionStatus
    current_task_id: str | None
    queue_size: int
    recent_history: list[TaskHistoryEntry]


class TaskNotePort(Protocol):
    def make_note(self, user_prompt: str) -> str:
        ...


class ContextRouter(Protocol):
    def decide(
        self,
        *,
        task: Task,
        config: AppConfig,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        ...
```

Principles:

- The existing `router/policy.py` is not removed immediately
- First, it is moved down into a `HeuristicRouter` class
- Then an OpenAI-based implementation is added as `AgentRouter`

---

## 7. OpenAI Router MVP Scope

The first OpenAI router needs only roughly the following.

- Input:
  - `user_prompt`
  - `task_note`
  - session snapshot list
- Output:
  - `assign_existing`
  - `create_new_session`
  - `enqueue_global`
  - `reason`

Excluded from the first implementation:

- Codebase file lookups
- Complex tool call loops
- Exploration of systems outside sessions

In other words, it only goes as far as "an agent that looks at session state and makes a decision."

---

## 8. Fallback Strategy

The OpenAI router can always fail for the following reasons.

- API failure
- timeout
- structured output parsing failure

Therefore, the following principle is locked down.

- Primary: `AgentRouter`
- On failure: `HeuristicRouter`

In other words, the current heuristic router is not a deprecation target but a fallback.

---

## 9. `task_note` Principles

`task_note` continues to be kept simple.

- 1-2 lines
- Brief memo on whether it belongs to the same feature group/domain axis
- For routing reference

What will NOT be done:

- Tag schema design
- Adding intent/domain structured fields
- Generating execution-oriented refined prompts

In other words, `task_note` is an internal memo and continues to not be delivered to the worker.

---

## 10. `Session` Model Related Decisions

The current implementation is based on generic runtime fields.

It's better to maintain this direction.

Reasons:

- The `submit + poll` structure has already been organized on this premise
- `terminal_id` is closer to UI/environment details than a backend-independent model
- The essence of `init.md` is context routing, not terminal management

Therefore, the recommended approach for the next phase is:

- Code maintains the current generic runtime structure
- If needed, update `init.md` document wording to match the generic worker/session model

---

## 11. Implementation Order

The following implementation order is appropriate for the next phase.

### 11.1 Phase 1: Router boundary separation

- Add `ContextRouter` interface
- Move existing `decide_route()` to `HeuristicRouter` class

### 11.2 Phase 2: Task note generator separation

- Move `_make_task_note()` to a `TaskNotePort` implementation
- Initial implementation can remain template-based

### 11.3 Phase 3: Orchestrator wiring change

- Change `Orchestrator.submit()` so it no longer directly calculates routes
- Lock down the sequence as follows

1. Task creation
2. Note generation
3. Router call
4. State reflection
5. Dispatch if needed

### 11.4 Phase 4: Snapshot introduction

- Only `SessionSnapshot` is passed to the router, not the full `Session`
- Introduce a cap on the number of recent history entries

### 11.5 Phase 5: Add OpenAI-based `AgentRouter`

- Use structured output
- Include `reason`
- Make decisions based only on session snapshots

### 11.6 Phase 6: Fallback + trace logging

- `HeuristicRouter` on invalid output / timeout
- Add routing trace events to `tasks.jsonl`

### 11.7 Phase 7: Operational enhancements

- `lg poll`
- `lg tasks`
- Router backend selection option if needed

---

## 12. Events to Add

For the next phase, the following events are recommended for `tasks.jsonl`.

- `task_note_created`
- `task_routing_started`
- `task_routing_completed`
- `task_routing_fallback_used`

Keep the payload simple.

- router kind
- decision
- assigned session id
- short reason

---

## 13. Test Plan

### 13.1 Unit Tests

- `TaskNotePort` basic implementation test
- `HeuristicRouter` session selection test
- Router fallback condition test

### 13.2 Orchestrator Tests

- Test note generation followed by router call order
- Test decision reflection on `AgentRouter` success
- Test heuristic fallback on `AgentRouter` failure
- Test that only `user_prompt` is delivered to the worker

### 13.3 Integration Tests

Optionally:

- Router smoke test that runs only in environments with an OpenAI API key
- Structured output parsing verification

---

## 14. Actual Goal of the Next Phase

The next implementation goal can be summarized as follows.

> While maintaining the current runtime loop, separate `task_note` generation and session selection decisions into a `ContextRouter` boundary, keep the existing heuristic router as a fallback, and attach a thin OpenAI routing agent per `init.md`.

In other words, the key for the next phase is:

- Not further generalizing the worker side
- But rather making routing responsibility independent
- Promoting `task_note` to an actual routing input
- Maintaining an MVP that doesn't become overly heavy even with an AI router attached
