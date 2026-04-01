# Poll I/O Reduction Matrix

> Format: Table-driven design document
> Goal: Reduce the problem of `poll_runs()` excessively reading and writing files proportionally to the number of sessions and poll frequency.

## 1. Hot Path Diagnosis

| Point | Current Behavior | Cost Type | Why It Is a Problem |
|---|---|---|---|
| Active run collection | Full load of `sessions.json` | File read | Full session file deserialization on every poll |
| Running heartbeat reflection | `sessions_repo.update()` per active run | Lock + full file write | Write count increases linearly as run count grows |
| Completion/failure reflection | Separate update per run | Lock + full file write | Multiple full file rewrites in the same poll |
| Heartbeat event log | Append on every poll tick | Log growth | `tasks.jsonl` bloat during long-running execution |
| Global promotion | Additional update per promotion | Lock + write | Frequent small mutation repetition |

## 2. Target State

| Goal | Description |
|---|---|
| Minimize session file reads | Load only once per poll if possible |
| Batch session file writes | Reflect multiple run results in a single update |
| Restrain heartbeat logging | No log spamming when there is no state change |
| Simplify post-dispatch mutations | Promotion and finalize share the same mutation pass |

## 3. Recommended Design

### A. Two-phase poll structure

| Phase | Locking | What It Does |
|---|---|---|
| 1. Snapshot collection | No lock | Load `sessions.json` once, build active run list |
| 2. Worker poll | No lock | Query external worker state for each run |
| 3. Batch apply | Lock once | Reflect all results in a single `sessions_repo.update()` |

Key points:

- Do not hold the lock during external worker poll.
- Batch session reflections into a single update as much as possible.

### B. Heartbeat throttling

| Item | Proposal |
|---|---|
| Session `last_heartbeat` | Continue updating |
| `session_heartbeat` log event | Record only on change conditions, not every tick |
| Change condition examples | worker_context change, last_message change, or N seconds elapsed |

Recommendation for the first version:

- Do not leave event logs on every heartbeat.
- Reflect `last_heartbeat` only in the session file.

Reason:

- Log bloat cost is greater than the debugging information value.

## 4. Detailed Change Proposals

| Change Proposal | Difficulty | Impact | Notes |
|---|---|---|---|
| `_collect_active_runs()` reuses pre-loaded state | Low | Reduces reads by 1 | Can be done first |
| Collect poll results in a dict and apply in a single update | Medium | Significantly reduces write count | Recommended |
| Process finalize + promotion in the same update pass | Medium | Reduces lock count | Requires code structure cleanup |
| Heartbeat event throttling or removal | Low | Suppresses log bloat | Recommended |
| Keep `tasks_log.read_all()` hot path removal | Already mostly resolved | Maintains stability | Can maintain current state |

## 5. Specific Implementation Points

| File | Work |
|---|---|
| `src/leopard_gecko/orchestrator/pipeline.py` | Introduce batch poll/apply structure |
| `src/leopard_gecko/store/sessions_repo.py` | Review “load once then update” helper if needed |
| `src/leopard_gecko/models/task.py` | May add a small model for poll result aggregation |
| `tests/test_pipeline.py` | Behavioral equivalence tests |
| `tests/test_worker_loop.py` | Loop-level regression tests |

## 6. Recommended Implementation Approach

| Option | Advantages | Disadvantages | Recommended |
|---|---|---|---|
| Keep per-run update as is | Simple code | Large I/O inefficiency | Not recommended |
| Full sessions batch apply | Balance of performance and simplicity | Requires some refactoring | Recommended |
| Replace with SQLite | High scalability | Overkill | Not in scope for this patch |

## 7. Test Strategy

| Test | Expected Result |
|---|---|
| Poll with 3 active runs | All session states are reflected and results are identical to existing behavior |
| Mix of 1 completed, 2 running | `PollRunsResult` aggregation is maintained |
| State where only heartbeats keep coming | Session heartbeat is updated but logs do not explode |
| Poll including promotion | Dispatch count and session state have the same meaning as before |

## 8. Success Criteria

| Criterion | Evaluation Method |
|---|---|
| Reduced `sessions.json` write count per poll | Verify with repository spy or monkeypatch |
| No behavior regression | Maintain existing tests + add new tests |
| Log bloat suppression | Confirm reduced event count in heartbeat-only scenario |
