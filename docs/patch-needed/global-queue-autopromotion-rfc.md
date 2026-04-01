# RFC: Global Queue Auto Promotion

## 1. Summary

현재 global queue 승격은 “어떤 run이 막 끝났을 때만” 일어난다.
이 RFC는 active run이 하나도 없어도 `poll_runs()`가 global queue를 스스로 전진시키도록 바꾸는 계획을 정의한다.

## 2. Motivation

다음 상태가 실제로 가능하다.

- session 하나는 idle
- `global_queue`에는 task가 남아 있음
- active run은 0개

이 경우 지금 구현은 아무 것도 하지 않는다.
즉 system이 멈춘 것은 아니지만, 스스로 다시 시작하지 못한다.

## 3. Non-Goals

- 우선순위 큐 도입
- starvation-free scheduler
- 여러 task를 공정하게 섞는 복잡한 스케줄러

## 4. Proposed Behavior

`poll_runs()`는 아래 두 단계를 항상 수행한다.

1. active run 상태를 수집하고 반영한다.
2. 그 결과와 무관하게 dispatch 가능한 global queue task를 가능한 만큼 승격한다.

승격 우선순위는 유지한다.

1. idle session 재사용
2. 남는 capacity가 있으면 새 session 생성
3. 둘 다 아니면 대기 유지

## 5. API / Code Shape

새 helper를 도입한다.

```python
def _promote_dispatchable_global_tasks(self, config: AppConfig) -> int:
    ...
```

의도:

- 한 번의 poll에서 실제 dispatch한 개수를 반환
- 내부에서 `while` 루프로 여러 task를 처리 가능
- 더 이상 dispatch할 수 없는 순간 멈춤

`poll_runs()`는 마지막에 아래처럼 사용한다.

```python
poll_result.dispatched += self._promote_dispatchable_global_tasks(config)
```

## 6. Dispatch Limit Rule

한 번의 poll에서 무한히 돌지 않게 제한이 필요하다.

권장 규칙:

- 시작 시점의 idle session 수 + 남는 capacity 수 만큼만 승격

이 방식의 장점:

- 같은 poll에서 세션 수보다 많은 작업을 시작하려 하지 않는다.
- 구현이 단순하다.

## 7. Error Handling

dispatch 실패는 RFC 01의 rollback 규칙을 그대로 따른다.

- 승격 도중 하나가 실패해도 이미 성공한 dispatch는 유지
- 실패한 task는 global queue로 복원
- 같은 poll에서 계속 진행할지, 즉시 멈출지는 단순성을 위해 “즉시 멈춤”으로 시작

## 8. Invariants

이 변경 뒤에는 아래가 항상 성립해야 한다.

- idle session이 있고 global queue가 비어 있지 않으면, 다음 poll 이후 둘 중 하나는 변한다.
  - task가 dispatch된다.
  - dispatch 불가 사유가 남는다.
- active run 수가 0이어도 global queue는 전진할 수 있다.

## 9. Implementation Notes

- `_promote_next_global_task()`는 남겨도 되지만, 내부 구현은 새 bulk helper가 재사용하도록 바꾼다.
- `poll_runs()`의 기존 완료 후 승격 분기는 단순화할 수 있다.
- 가능한 경우 승격은 poll 마지막 한 번에 모아서 처리하는 편이 흐름이 읽기 쉽다.

## 10. Files To Change

- `src/leopard_gecko/orchestrator/pipeline.py`
- `tests/test_pipeline.py`
- 필요 시 `tests/test_worker_loop.py`

## 11. Test Plan

필수 테스트:

- active run 0개 + idle session 1개 + global queue 1개 -> dispatch 1건
- active run 0개 + idle session 0개 + 남는 capacity 있음 -> 새 session 생성 후 dispatch
- active run 0개 + capacity full -> dispatch 0건
- global queue 2개 + idle 1개 + 남는 capacity 1개 -> 한 poll에서 최대 2건 dispatch
- 승격 중 첫 task dispatch 실패 -> rollback 후 같은 iteration 종료

## 12. Migration / Rollout

데이터 포맷 변경은 없다.
동작만 바뀐다.
따라서 순수 코드 패치로 끝낼 수 있다.

## 13. Open Question

한 poll에서 여러 개를 dispatch할지, 하나만 dispatch할지?

권장 답:

- 여러 개 dispatch

이유:

- 남는 capacity가 명확할 때 처리량이 좋아진다.
- 별도 daemon이 느린 주기로 돈다면 하나씩만 꺼내는 것은 불필요하게 답답하다.
