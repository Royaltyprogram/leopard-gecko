# ADR: Router Availability Fallback

- Status: Proposed
- Date: 2026-04-01
- Decision Scope: Baseline availability of the submit path

## Context

The current default submit path depends on two OpenAI calls.

1. `task_note` generation
2. route decision

Problems with this structure:

- If there is no API key, the entire submit fails.
- API timeouts or structured output errors also immediately become user-facing failures.
- Even simple local development/testing requires network dependency.

Meanwhile, the current codebase only needs a lightweight router.
In other words, there is no need for a structure that “always requires calling the agent”.

## Decision

Add fallback to the default path.

### Adopted Content

1. Use `FallbackTaskNoteGenerator` for task note generation.
2. Use `FallbackRouter` for route decisions.
3. Fall back to heuristics when the agent call fails.
4. Submit prioritizes success as much as possible.

## Chosen Design

### Task note

Default order:

1. `AgentTaskNoteGenerator`
2. On failure, `TemplateTaskNoteGenerator`

Rules:

- Note generation failure must not be a reason for submit failure.
- In the worst case, a single template note is sufficient.

### Router

Default order:

1. `AgentRouter`
2. `HeuristicRouter` in the following situations:
   - No API key
   - network error
   - invalid JSON
   - invalid payload

### HeuristicRouter Draft

Input uses only the current session snapshot.

- `task.user_prompt`
- `task.task_note`
- `user_prompt`, `task_note` from recent history
- queue limit
- remaining capacity

Rules start simple.

- If related token overlap is sufficient, use existing session
- Otherwise, if capacity remains, create new session
- Otherwise, global queue

## Consequences

### Advantages

- Submit is possible even without an API key
- The entire orchestrator does not stop due to network issues
- Local usability improves while maintaining the e2e agent path

### Disadvantages

- Fallback results may be less refined than agent decisions
- The router implementation splits into two, increasing the number of tests

## Why This Over Alternatives?

### Alternative 1. Keep agent-only as is

Rejection reason:

- Development and operational availability is too low.

### Alternative 2. Fallback only for template note, router remains agent-only

Rejection reason:

- The real bottleneck is the router's own network dependency.

### Alternative 3. Switch heuristic to the default and make agent optional

Deferral reason:

- This could significantly change the current structure and user expectations.
- For now, “agent first, fallback on failure” is more incremental.

## Implementation Sketch

### New Types

- `HeuristicRouter`
- `FallbackRouter`
- `FallbackTaskNoteGenerator`

### Config Changes

Keep changes as small as possible.

- Do not add large enum extensions or threshold settings.

### Factory Changes

- `build_router()` returns `FallbackRouter(...)` instead of directly returning `AgentRouter`
- `_resolve_task_note_port()` also uses the fallback generator

## Files Affected

- `src/leopard_gecko/router/factory.py`
- `src/leopard_gecko/router/agent.py`
- `src/leopard_gecko/router/task_notes.py`
- `src/leopard_gecko/router/policy.py`
- `src/leopard_gecko/models/config.py`
- `tests/test_router.py`
- `tests/test_pipeline.py`

## Validation

- Submit must succeed even without an API key
- If the agent router returns an invalid payload, fall back to heuristic
- Existing e2e tests for the agent success path remain unchanged

## Follow-up

This ADR only covers up to the fallback.
If needed later, heuristic scoring can be made more sophisticated, but for now simple and reproducible rules take priority.
