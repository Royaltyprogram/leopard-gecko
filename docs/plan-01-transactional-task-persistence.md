# Transactional Task Persistence Plan

> 목표: `sessions.json`과 `tasks.jsonl` 사이의 불일치를 줄여, 큐에 들어간 task를 항상 다시 복원할 수 있게 만든다.

## 문제

현재 `submit()`은 `sessions.json`을 먼저 갱신한 뒤 `tasks.jsonl`에 `task_created`를 append한다.
이 사이에 프로세스가 종료되면 session queue 안의 `task_id`는 남는데 task 본문은 로그에 없을 수 있다.
이 상태에서는 큐 승격 시 task 복원이 실패한다.

## 목표 상태

- task 본문은 session queue에 들어가기 전에 durable 하게 저장된다.
- `submit()` 도중 중단돼도 이후 재시도나 복구가 가능하다.
- 큐 승격 로직은 더 이상 `task_created` 유실 때문에 깨지지 않는다.

## 구현 방향

### 방향 1. task snapshot 저장소 분리

가장 단순한 방법은 `tasks/<task_id>.json` 같은 per-task snapshot 저장소를 추가하는 것이다.

- `task_created` 이벤트는 감사 로그로 유지
- 실제 복원은 append-only 로그가 아니라 snapshot 파일에서 수행
- `_load_task()`는 먼저 snapshot 저장소를 읽고, 없을 때만 로그 fallback

이 방식은 현재 구조와 잘 맞고 복구 경로가 단순하다.

### 방향 2. 제출 절차 재정렬

`submit()` 순서를 아래처럼 바꾼다.

1. task 생성
2. task snapshot 저장
3. `task_created` 이벤트 append
4. `sessions.json` update
5. `task_routed` 이벤트 append
6. 필요 시 dispatch

핵심은 session이 task를 참조하기 전에 task 자체가 먼저 저장돼 있어야 한다는 점이다.

## 변경 대상

- `src/leopard_gecko/store/`
- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/task.py`
- `tests/test_pipeline.py`
- 신규 테스트 파일: `tests/test_task_store.py`

## 세부 구현 계획

### 1. `TaskRepository` 추가

- 역할: task snapshot 저장/조회
- 저장 형식: `data_dir/tasks/<task_id>.json`
- 인터페이스:
  - `initialize()`
  - `save(task: Task) -> None`
  - `load(task_id: str) -> Task`
  - `exists(task_id: str) -> bool`

### 2. `Orchestrator`에 task store 주입

- `self.task_repo` 추가
- `init_storage()`에서 task 저장소도 초기화
- `_load_task()`는 `task_repo.load()`를 기본 경로로 사용

### 3. 제출 순서 정리

- `Task` 생성 직후 snapshot 저장
- 그 다음 `task_created` 이벤트 기록
- 그 다음 sessions mutation
- sessions mutation이 실패하면 task snapshot은 orphan일 수 있지만, 이 상태는 복구 가능하다

### 4. 복구성 있는 로그 설계

- `task_created`는 snapshot 저장 이후에만 append
- `task_routed`는 sessions mutation 성공 이후에만 append
- 필요하면 이후 `task_dispatch_failed` 같은 이벤트도 추가 가능

## 테스트 계획

- task snapshot 저장 후 sessions update 전에 예외가 나도 task는 다시 읽을 수 있어야 함
- queue에 들어간 task가 snapshot에서 정상 복원되어야 함
- 기존 `task_created` 로그가 없어도 `_load_task()`가 snapshot으로 복원 가능해야 함
- 여러 task 제출 후 queue 승격이 기존과 동일하게 동작해야 함

## 구현 순서

1. `TaskRepository` 추가
2. `Orchestrator`에 연결
3. `submit()` 저장 순서 변경
4. `_load_task()` 경로 변경
5. 장애 상황 테스트 추가

## 비목표

- 완전한 멀티파일 트랜잭션 구현
- SQLite 도입
- 이벤트 소싱으로 전체 구조 변경

MVP에서는 snapshot + append-only log 조합이면 충분하다.
