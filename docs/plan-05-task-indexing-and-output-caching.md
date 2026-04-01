# Task Indexing And Output Caching Plan

> 목표: task 수와 worker output이 늘어나도 `poll()`과 queue 승격 비용이 선형으로 악화되지 않게 만든다.

## 문제

현재 두 군데가 커질수록 느려진다.

1. task 복원
   `tasks.jsonl` 전체를 읽고 역순 탐색해서 `task_created`를 찾는다.

2. worker context 복원
   run output jsonl 전체를 매 poll마다 끝까지 읽어서 `worker_context_id`를 찾는다.

처음엔 괜찮지만 task 수와 output 크기가 늘면 polling 비용이 급격히 커진다.

## 목표 상태

- task 복원은 O(1)에 가깝게 처리한다.
- worker context와 last message는 output 전체 재스캔 없이 읽는다.
- append-only log는 감사 용도로 유지하되 hot path에서는 직접 읽지 않는다.

## 구현 방향

### 방향 1. task snapshot을 hot path로 사용

`plan-01`에서 추가하는 task snapshot 저장소를 기본 조회 경로로 삼는다.
이렇게 하면 queue 승격 시 더 이상 `tasks.jsonl` 전체 스캔이 필요 없다.

### 방향 2. worker state sidecar 파일 추가

worker output과 별도로 작은 state 파일을 유지한다.

- 경로 예시: `worker_runs/<session_id>/<task_id>.state.json`
- 필드:
  - `worker_context_id`
  - `last_message`
  - `updated_at`

`poll()`은 jsonl 전체를 스캔하지 말고 이 sidecar를 먼저 읽는다.

### 방향 3. output parsing 최소화

새 이벤트가 append될 때만 incremental 하게 갱신하는 구조가 이상적이다.
MVP에서는 더 단순하게:

- dispatch 시 state 파일 생성
- poll 시 output 파일 끝부분만 읽거나
- 종료 시 wrapper가 last message/state 파일을 직접 갱신

정도로 시작한다.

## 변경 대상

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/adapters/codex.py`
- `src/leopard_gecko/store/`
- `tests/test_workers.py`
- `tests/test_pipeline.py`

## 세부 구현 계획

### 1. task lookup 경로 교체

- `_load_task()`는 snapshot 저장소 사용
- `tasks.jsonl` 스캔은 fallback 또는 debug only 경로로 축소

### 2. worker state file 도입

`CodexAdapter`가 아래 파일을 관리한다.

- `.state.json`
- `.last_message.txt`
- 필요 시 `.exit.json`

`worker_context_id`와 `last_message`는 state file에 한번 정리해서 저장한다.

### 3. `poll()` 읽기 순서 최적화

`poll()`은 아래 우선순위를 따른다.

1. state file
2. last message file
3. output jsonl fallback

즉 가장 작은 파일부터 읽고, 큰 jsonl은 정말 필요할 때만 본다.

### 4. output parsing helper 분리

`codex.py` 안의 파일 읽기 로직을 helper로 분리한다.

- `load_run_state_files(...)`
- `parse_output_for_context_id(...)`

이렇게 나누면 테스트와 추후 교체가 쉬워진다.

## 테스트 계획

- `_load_task()`가 snapshot 경로를 우선 사용하는지 검증
- state file이 있으면 output jsonl 전체를 읽지 않아도 되는지 검증
- context id와 last message가 sidecar에서 정상 복원되는지 검증
- sidecar가 없을 때 기존 fallback 파싱이 동작하는지 검증

## 구현 순서

1. task snapshot 저장소를 hot path에 연결
2. run state sidecar 포맷 정의
3. `CodexAdapter.poll()` 읽기 순서 최적화
4. fallback 파싱 helper 분리
5. 성능 회귀 방지 테스트 추가

## 비목표

- 정밀 벤치마크 프레임워크
- 대규모 로그 저장소 교체
- 완전한 streaming parser

지금 단계에서는 "hot path에서 전체 파일 스캔을 피한다"는 목표만 달성하면 된다.
