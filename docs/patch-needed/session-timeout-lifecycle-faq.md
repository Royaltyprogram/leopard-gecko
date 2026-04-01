# Session Timeout And Lifecycle FAQ

> 형식: Q&A
> 목표: `session_idle_timeout_min`을 실제 lifecycle 규칙으로 연결하고, blocked/dead 세션이 capacity를 영구 점유하지 않게 만든다.

## Q1. 지금 뭐가 문제인가?

설정에는 `session_idle_timeout_min`이 있지만, 현재 코드는 이 값을 사용해 세션을 만료시키지 않는다.
그 결과:

- 오래된 idle session이 계속 살아 있는 것으로 계산될 수 있고
- blocked session도 slot을 계속 먹고
- dead 전환 규칙이 수동 테스트에만 머물게 된다.

## Q2. 이번 패치의 핵심 목표는?

세 가지다.

1. stale session을 자동으로 `dead`로 전환한다.
2. dead 전환 시 queue와 current task를 복구 가능한 위치로 이동시킨다.
3. capacity 계산에서 “실제로 다시 쓸 수 없는 세션”을 풀어준다.

## Q3. 언제 세션을 stale로 볼 건가?

기본 규칙은 단순하게 간다.

- `now - last_heartbeat > session_idle_timeout_min`

그리고 상태별 해석은 아래처럼 둔다.

| 상태 | stale 판단 시 처리 |
|---|---|
| `idle` | `dead` 전환 |
| `blocked` | `dead` 전환 |
| `busy` + active run 정보 존재 | 먼저 worker poll 결과를 보고, 그래도 진행 불명확하면 `dead` 전환 후보 |
| `busy` + active run 정보 없음 | 즉시 비정상 상태로 보고 `dead` 전환 후보 |

## Q4. dead 전환 시 task는 어떻게 처리하나?

가장 단순한 규칙을 택한다.

- `current_task_id`가 있으면 global queue 앞으로 이동
- session queue에 있던 task들도 순서를 유지한 채 global queue 앞으로 이동
- session의 `current_task_id`, `queue`, `active_run_*`는 비운다

왜 global queue인가?

- 특정 dead session에 task를 묶어 둘 이유가 없다.
- 이후 scheduler가 healthy session에 다시 분배하면 된다.

## Q5. blocked session은 바로 dead로 바꿀까?

아니다.

처음 blocked는 유지한다.
하지만 heartbeat가 오래 갱신되지 않으면 dead로 내린다.

이렇게 하면:

- 짧은 수동 확인 시간은 허용하고
- 무기한 slot 점유는 막을 수 있다.

## Q6. capacity 계산 함수는 어떻게 바꿔야 하나?

두 층으로 나누는 편이 안전하다.

- `live_session_count`: 기존 의미 유지 또는 최소 수정
- `allocatable_session_count` 또는 `routable_session_count`: 실제 새 task 배정 가능성에 가까운 계산

권장 방향:

- 라우팅과 global promotion에서 slot 가용성 판단은 stale cleanup 이후 상태를 기준으로 한다.
- 즉 먼저 stale session을 정리하고, 그 다음 기존 `live_session_count`를 써도 된다.

이 방식이 가장 덜 침습적이다.

## Q7. 이 로직은 어디서 실행해야 하나?

두 군데가 좋다.

1. `poll_runs()` 시작 직후
2. `submit()` 초반, route 결정 전에

이유:

- worker loop가 돌고 있으면 자동 회복
- worker loop가 느리거나 잠시 멈춰도 submit 시점에 stale slot을 치울 수 있음

## Q8. 어떤 helper를 추가하면 좋은가?

후보는 아래 둘이다.

```python
def _expire_stale_sessions(self, state: SessionsState, config: AppConfig, now: datetime) -> ExpireResult:
    ...

def _requeue_dead_session_tasks(session: Session, state: SessionsState) -> list[str]:
    ...
```

`ExpireResult`에는 아래 정도만 있으면 충분하다.

- `expired_session_ids`
- `requeued_task_ids`

## Q9. 어떤 이벤트를 남겨야 하나?

최소 이벤트:

- `session_expired`
- `task_requeued_from_dead_session`

payload 예시:

- `session_id`
- `previous_status`
- `reason=stale_timeout`
- `task_ids`

## Q10. 테스트는 어떻게 잡는가?

반드시 필요한 테스트:

- stale idle session이 submit 전에 dead 처리되고 새 session 생성이 가능해짐
- stale blocked session이 poll에서 dead 처리됨
- dead 전환 시 `current_task_id`와 queue task가 global queue로 이동
- dead 전환 후 세션 active run 정보가 비워짐
- dead 처리 이후 capacity가 회복돼 global queue 승격이 가능해짐

## Q11. 이번 패치에서 굳이 하지 않아도 되는 것은?

- 세밀한 grace period별 상태 추가
- “suspect”, “recovering” 같은 중간 상태
- OS 프로세스 레벨 생존 확인 강화

이번 단계는 stale timeout을 실제로 동작하게 만드는 데 집중하면 충분하다.
