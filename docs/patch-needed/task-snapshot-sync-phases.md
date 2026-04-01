# Task Snapshot Sync Plan

> 형식: 단계별 실행 계획
> 목표: `TaskRepository` snapshot이 실제 queue/runtime 상태와 계속 맞도록 만든다.

## 배경

현재 task snapshot은 생성 시점과 submit 직후 정도만 갱신된다.
하지만 실제 lifecycle은 그 이후에도 계속 바뀐다.

- queued in session
- queued globally
- running
- completed
- failed
- interrupted

snapshot이 stale하면 hot path 저장소로서 신뢰하기 어렵다.

## Phase 1. 상태 변경 지점 목록을 고정한다

먼저 “언제 task snapshot을 반드시 다시 써야 하는가”를 코드 기준으로 확정한다.

### 반영 대상 전이

1. submit 직후 route 확정
2. global queue 진입
3. session queue 진입
4. dispatch 성공 후 running 유지
5. dispatch 실패 후 queued_globally 복원
6. run completed
7. run failed
8. run blocked / interrupted
9. session queue에서 다음 task 승격
10. dead session에서 global queue로 재배치

### 이번 단계 산출물

- 전이 표 1개
- 각 전이가 어느 함수에서 일어나는지 매핑

## Phase 2. task snapshot 갱신 경로를 한 군데로 모은다

여기서 중요한 건 “필요할 때마다 여기저기서 `task_repo.save()`를 직접 부르지 않는 것”이다.

추천 helper:

```python
def _update_task_snapshot(
    self,
    task_id: str,
    *,
    queue_status: QueueStatus | None = None,
    routing: TaskRouting | None = None,
) -> Task:
    ...
```

원칙:

- full object overwrite는 helper 안에서만
- 호출부는 필요한 상태만 넘긴다

## Phase 3. session mutation과 task snapshot mutation의 순서를 맞춘다

권장 순서:

1. `sessions_repo.update(...)`로 세션 상태를 먼저 확정
2. 그 전이 결과를 바탕으로 task snapshot 갱신
3. 마지막에 이벤트 append

이 순서를 권장하는 이유:

- queue의 실제 위치는 세션 상태가 정답이다.
- snapshot은 그 정답을 반영하는 파생 상태로 두는 편이 덜 꼬인다.

## Phase 4. Task 모델을 필요한 만큼만 확장한다

현재 모델로도 `queue_status`와 `routing`만 맞추면 1차 목표는 달성된다.
이번 패치에서는 모델 확장을 최소화한다.

### 유지

- `queue_status`
- `routing`

### 보류

- 종료 summary
- 최종 exit code
- 마지막 session_id 이력

이런 정보는 나중에 필요하면 넣되, 지금은 snapshot 신뢰성만 회복한다.

## Phase 5. lifecycle별 반영 규칙을 구체화한다

| 상황 | task snapshot 반영 |
|---|---|
| 기존 session queue에 들어감 | `queued_in_session` |
| global queue에 들어감 | `queued_globally` |
| dispatch 성공 | `running` |
| run 완료 | `completed` |
| run 실패 | `failed` |
| manual recovery 필요 | `failed` 대신 새 enum을 만들지 말고 일단 `failed` 또는 현행 유지 여부 결정 필요 |
| dead session에서 재배치 | `queued_globally` |

여기서 하나 결정이 필요하다.

### interrupted를 task snapshot에도 따로 둘 것인가?

권장 답:

- 이번 패치에서는 하지 않는다.

이유:

- `TaskHistoryStatus.INTERRUPTED`는 session-local detail이다.
- `QueueStatus` enum을 늘리면 영향 범위가 넓다.
- 우선은 global 재배치 시 `queued_globally`로 정규화하는 편이 단순하다.

## Phase 6. 테스트를 상태 전이 중심으로 다시 쓴다

필수 테스트 묶음:

1. submit 후 snapshot이 route 결과와 같음
2. queued task가 promotion되면 snapshot이 `running`으로 바뀜
3. completed 후 snapshot이 `completed`
4. failed 후 snapshot이 `failed`
5. dispatch rollback 후 snapshot이 `queued_globally`
6. dead session 재배치 후 snapshot이 `queued_globally`

## 구현 대상 파일

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/task.py`
- `src/leopard_gecko/store/task_repo.py`
- `tests/test_pipeline.py`

## 최종 완료 기준

- `TaskRepository`에 있는 모든 task는 마지막으로 알려진 queue/runtime 상태를 반영한다.
- `task_repo.load(task_id)` 결과만으로도 UI나 디버깅 출력의 기본 상태를 믿을 수 있다.
- lifecycle 테스트가 snapshot 기준으로도 통과한다.
