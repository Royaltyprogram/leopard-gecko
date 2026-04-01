# Patch Needed

This directory contains the implementation plans for 6 patches that need to be prioritized in the current codebase.

| Patch | Document | Format | Core Purpose |
|---|---|---|---|
| 01 | [dispatch-failure-rollback.md](./dispatch-failure-rollback.md) | Checklist-driven plan | Rollback so sessions don't remain in a broken state when worker submit fails |
| 02 | [global-queue-autopromotion-rfc.md](./global-queue-autopromotion-rfc.md) | RFC | Global queue advances automatically even without active runs |
| 03 | [session-timeout-lifecycle-faq.md](./session-timeout-lifecycle-faq.md) | Q&A / FAQ | Clarify timeout, blocked, and dead session lifecycle |
| 04 | [task-snapshot-sync-phases.md](./task-snapshot-sync-phases.md) | Phased execution plan | Synchronize task snapshot with actual runtime state |
| 05 | [poll-io-reduction-matrix.md](./poll-io-reduction-matrix.md) | Table-driven design document | Reduce file I/O and log write costs in poll hot path |
| 06 | [router-availability-adr.md](./router-availability-adr.md) | ADR | Add fallback to the OpenAI-dependent submit path |

## Recommended Application Order

1. `dispatch-failure-rollback.md`
2. `global-queue-autopromotion-rfc.md`
3. `session-timeout-lifecycle-faq.md`
4. `task-snapshot-sync-phases.md`
5. `poll-io-reduction-matrix.md`
6. `router-availability-adr.md`

## Rationale for the Order

- 01 is a safety guard that prevents state corruption.
- 02 immediately reduces current queue starvation.
- 03 enables capacity recovery and operational stability.
- 04 makes snapshots trustworthy as a hot path store.
- 05 can then reduce I/O costs without destabilizing the design.
- 06 finally improves baseline availability.

## Common Principles

- Fix data integrity first.
- Keep `sessions.json` as the single source of truth, but actively use small snapshot files in the hot path.
- Continue passing only `user_prompt` to workers.
- Even when adding new states or events, keep the model as simple as possible.
