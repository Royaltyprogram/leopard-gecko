# Dispatch Failure Rollback

> 형식: 체크리스트 중심 구현 계획서
> 목표: `worker.submit()` 실패가 세션 상태를 오염시키지 않게 만든다.

## 문제 요약

- 현재 `submit()`과 queue 승격 경로는 세션 상태를 먼저 `running`으로 바꾼 뒤 worker dispatch를 시도한다.
- 그 다음 `_dispatch_task()`가 예외를 던지면 세션은 `busy`, `current_task_id`가 설정된 상태로 남을 수 있다.
- 하지만 `active_run_id`, `active_pid`가 비어 있으면 이후 `poll_runs()`가 이 task를 추적하지 못한다.

## 이번 패치의 완료 조건

- [ ] dispatch 실패 후에도 세션이 “추적 불가능한 running state”로 남지 않는다.
- [ ] 실패한 task는 재시도 가능한 queue 상태로 되돌아간다.
- [ ] 새로 만든 session에서 실패했으면 세션을 정리하거나 idle 상태로 되돌린다.
- [ ] 실패 사실이 `tasks.jsonl`에 명시적으로 남는다.
- [ ] direct submit, session queue 승격, global queue 승격 세 경로가 모두 같은 rollback 규칙을 쓴다.

## 채택할 규칙

- dispatch 실패 시 task는 `queued_globally`로 되돌린다.
- dispatch 실패 시 대상 session은 `idle` 또는 제거 가능한 상태로 정리한다.
- `active_run_*` 필드는 반드시 비운다.
- 실패한 task는 `task_dispatch_failed` 이벤트를 남긴다.

이 규칙을 고른 이유:

- session queue 앞쪽 복원까지 같이 풀면 상태 조합이 늘어난다.
- 현재 구조에서는 global queue로 되돌리는 쪽이 가장 단순하고 복구 경로가 명확하다.
- 다음 패치인 global queue 자동 승격과 자연스럽게 연결된다.

## 구현 체크리스트

### 1. dispatch 호출부를 예외 안전하게 감싼다

- [ ] `submit()`에서 `_dispatch_task()` 호출을 `try/except`로 감싼다.
- [ ] `poll_runs()`에서 `transition.next_dispatch`와 `_promote_next_global_task()` 내부 dispatch도 같은 방식으로 감싼다.
- [ ] rollback 로직은 중복하지 말고 `_rollback_failed_dispatch(...)` 같은 helper로 모은다.

### 2. rollback helper의 입력을 명확히 한다

- [ ] 입력에는 `task_id`, `session_id`, `created_session`, `promoted_from_queue`, `original_queue_source`가 들어가야 한다.
- [ ] helper는 `sessions_repo.update(...)` 안에서만 세션 상태를 수정한다.
- [ ] task snapshot도 같이 갱신할 수 있게 task queue status 변경 훅을 연결한다.

### 3. 세션별 rollback 규칙을 고정한다

- [ ] 새 session 생성 후 dispatch 실패:
  새 session이 해당 task 외에 아무것도 들고 있지 않으면 세션을 제거한다.
- [ ] 기존 idle session dispatch 실패:
  `current_task_id`를 비우고 `status=idle`로 되돌린다.
- [ ] session queue에서 다음 task 승격 후 실패:
  session은 `idle`로 두고 실패한 task는 global queue 앞으로 보낸다.
- [ ] global queue 승격 후 실패:
  해당 task를 global queue 앞에 복원한다.

### 4. 로그와 이벤트를 추가한다

- [ ] `task_dispatch_failed` 이벤트 추가
- [ ] payload에는 최소한 아래 필드를 넣는다.
  - `session_id`
  - `task_id`
  - `source`
  - `created_session`
  - `error`
- [ ] 필요하면 `session_rollback` 이벤트를 별도로 두지 말고 `task_dispatch_failed` 하나로 시작한다.

### 5. 에러 전파 정책을 정한다

- [ ] direct submit에서 dispatch 실패는 사용자에게 에러를 그대로 돌려준다.
- [ ] 다만 상태는 rollback이 끝난 뒤에 예외를 다시 던진다.
- [ ] poll loop 안에서는 예외를 삼키지 말고, rollback 후 해당 iteration에서만 실패 카운트나 로그를 남길지 결정한다.

추천:

- submit 경로는 예외 재전파
- poll 경로는 rollback 후 이벤트만 남기고 loop는 계속 진행

## 변경 대상 파일

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/task.py`
- `tests/test_pipeline.py`
- 필요 시 `tests/test_worker_loop.py`

## 테스트 시나리오

- [ ] 새 session 생성 직후 worker submit 실패 시 세션이 남더라도 idle이고 active run 정보가 없다.
- [ ] direct submit 실패 후 task는 `queued_globally`로 남는다.
- [ ] session queue의 다음 task dispatch 실패 시 기존 completed task의 정리는 유지된다.
- [ ] global queue 승격 dispatch 실패 시 queue 순서가 깨지지 않는다.
- [ ] rollback 뒤 다음 poll에서 같은 task를 다시 승격할 수 있다.

## 구현 순서

1. 실패 이벤트 스키마 추가
2. `_rollback_failed_dispatch(...)` helper 추가
3. direct submit 경로 적용
4. poll completion / promotion 경로 적용
5. rollback 테스트 추가

## 이번 패치에서 하지 않을 것

- 새로운 복잡한 retry scheduler
- backoff 정책
- dispatch retry 횟수 저장
- session별 retry budget
