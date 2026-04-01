# Poll Worker Loop Plan

> 목표: `lg poll` 수동 실행에 의존하지 않고, run 완료 처리와 queue 승격이 자동으로 진행되게 만든다.

## 문제

현재 lifecycle 전이는 `lg poll` 호출에 의존한다.
사용자나 외부 supervisor가 poll을 주기적으로 돌려주지 않으면:

- 완료 반영
- session queue 다음 task 실행
- global queue 승격
- heartbeat 갱신

이 모두 멈춘다.

## 목표 상태

- 기본 사용 시에도 poll loop가 자동으로 유지된다.
- CLI는 one-shot submit만 제공하더라도 백그라운드 루프가 동일 data dir에서 상태를 계속 전진시킨다.
- 수동 `lg poll`은 디버깅/운영용 fallback으로 남긴다.

## 구현 방향

### 방향 1. 별도 `lg worker` 커맨드 추가

가장 단순하고 예측 가능한 방식은 long-running loop를 명시적 커맨드로 두는 것이다.

- `lg worker`
- 일정 주기로 `orchestrator.poll_runs()` 호출
- 한 번에 여러 active run 처리
- 종료 신호를 받으면 안전하게 빠짐

이 방식은 hidden daemon보다 단순하고 테스트가 쉽다.

### 방향 2. 추후 자동 기동은 선택 사항으로 남김

당장은 submit 시 worker를 몰래 띄우기보다, 명시적 프로세스로 운영하는 편이 낫다.
MVP에서는 관찰 가능성과 단순성이 더 중요하다.

## 변경 대상

- `src/leopard_gecko/cli/main.py`
- `src/leopard_gecko/orchestrator/pipeline.py`
- 신규 파일: `src/leopard_gecko/orchestrator/worker_loop.py`
- `tests/test_pipeline.py`
- 신규 테스트 파일: `tests/test_worker_loop.py`

## 세부 구현 계획

### 1. worker loop abstraction 추가

`worker_loop.py`에 아래와 같은 thin loop를 둔다.

- `run_worker_loop(orchestrator, interval_sec, once=False) -> int`

역할:

- `poll_runs()`
- 결과 집계
- sleep
- signal handling

### 2. CLI command 추가

`main.py`에 아래 커맨드를 추가한다.

- `lg worker`
- 옵션:
  - `--interval-sec`
  - `--once`
  - `--data-dir`

`--once`는 현재의 `poll`을 대체하거나 내부적으로 재사용할 수 있다.

### 3. idle backoff 정책

active run이 없고 global queue도 비어 있으면 polling 간격을 늘리는 단순 backoff를 넣을 수 있다.
다만 첫 버전은 고정 interval로 유지하는 편이 더 단순하다.

### 4. 상태 출력 정리

worker loop는 사람이 읽을 수 있는 간단한 로그만 남긴다.

- `running`
- `completed`
- `failed`
- `dispatched`

너무 많은 상세 출력은 피한다.

## 테스트 계획

- `--once` 모드에서 현재 `poll`과 동일하게 동작해야 함
- 완료된 task가 자동으로 다음 queue task를 dispatch하는지 검증
- 빈 상태에서도 loop가 예외 없이 종료되는지 검증
- interrupt signal 처리 테스트는 가능 범위에서 최소화

## 구현 순서

1. worker loop 함수 분리
2. `lg worker` CLI 추가
3. `poll`과 공통 출력 경로 정리
4. loop 테스트 추가

## 비목표

- OS 서비스 등록
- 완전한 daemon supervisor
- 멀티프로세스 클러스터링

지금 단계에서는 "수동 poll이 아니어도 시스템이 움직인다"까지만 만들면 충분하다.
