# Run Recovery Plan

> 목표: 오케스트레이터 프로세스가 재시작돼도 실행 중이던 worker run을 가능한 한 정확하게 추적하고, 이미 끝난 작업을 잘못 `failed`로 처리하지 않게 만든다.

## 문제

현재 `CodexAdapter`는 실행 중인 subprocess를 메모리의 `self.processes`에 저장한다.
프로세스 재시작 뒤에는 이 정보가 사라지고, `poll()`은 PID 존재 여부만 보거나 종료된 경우 `exit_code=1`로 간주한다.
그 결과 실제 성공 종료한 run도 복구 시점에는 실패처럼 보일 수 있다.

## 목표 상태

- 재시작 후에도 기존 run 상태를 최대한 복원할 수 있다.
- 종료된 run은 출력 파일과 종료 정보를 바탕으로 성공/실패를 구분한다.
- 메모리 캐시는 최적화일 뿐, 정답의 원천은 디스크 상태가 된다.

## 구현 방향

### 방향 1. run metadata 파일 추가

각 dispatch 시 run 메타데이터를 별도 JSON으로 저장한다.

- 경로 예시: `worker_runs/<session_id>/<task_id>.meta.json`
- 저장 필드:
  - `run_id`
  - `task_id`
  - `session_id`
  - `pid`
  - `started_at`
  - `worker_context_id`
  - `output_path`
  - `status`

### 방향 2. 종료 결과 파일 추가

subprocess가 끝난 뒤 exit code를 별도 파일에 기록할 수 있게 한다.

- 경로 예시: `worker_runs/<session_id>/<task_id>.exit.json`
- 저장 필드:
  - `exit_code`
  - `finished_at`

`poll()`은 메모리 dict가 비어 있어도 이 파일을 보고 종료 상태를 판별할 수 있다.

## 변경 대상

- `src/leopard_gecko/adapters/codex.py`
- `src/leopard_gecko/adapters/base.py`
- `src/leopard_gecko/orchestrator/pipeline.py`
- `tests/test_workers.py`
- 신규 테스트 파일: `tests/test_run_recovery.py`

## 세부 구현 계획

### 1. run metadata 쓰기

- `submit()` 성공 직후 meta 파일 생성
- `WorkerSubmission`에 필요한 경우 metadata 경로 필드 추가
- session에는 기존처럼 `run_id`, `pid`, `output_path`를 저장

### 2. `poll()` 복구 경로 추가

`CodexAdapter.poll()` 순서를 아래처럼 바꾼다.

1. 메모리 `self.processes`에 있으면 우선 사용
2. 없으면 meta 파일과 output 파일 확인
3. PID가 살아 있으면 `is_running=True`
4. PID가 없고 exit 파일이 있으면 그 exit code 사용
5. PID가 없고 last message만 있으면 conservative 하게 `exit_code=0/unknown` 정책을 별도 정의

### 3. 종료 코드 기록 방식 결정

가장 단순한 구현은 subprocess wrapper shell을 두는 것이다.

- `codex exec ...`
- 종료 후 `$?`를 exit file에 기록

이렇게 하면 재시작 후에도 exit code를 신뢰할 수 있다.

### 4. 상태 불명확 시 정책

출력 파일은 있으나 exit 정보가 없고 PID도 없을 때는 즉시 `failed`로 두지 않는다.

- 후보 1: `unknown_terminated` 상태를 내부적으로 도입
- 후보 2: `failed` 대신 `blocked`로 두고 수동 확인

MVP에서는 `blocked`로 보내는 쪽이 더 안전하다.

## 테스트 계획

- 동일 프로세스에서 submit 후 poll 시 기존 동작 유지
- 새 adapter 인스턴스를 만든 뒤 기존 run을 poll 해도 상태가 복원되어야 함
- exit file이 있으면 그 exit code를 그대로 사용해야 함
- PID가 없고 종료 정보도 없을 때 보수적 상태 전이를 검증해야 함

## 구현 순서

1. run meta/exit 파일 포맷 정의
2. `CodexAdapter.submit()`에서 meta 저장
3. 종료 코드 기록 wrapper 추가
4. `poll()` 복구 경로 추가
5. 재시작 시나리오 테스트 추가

## 비목표

- 임의 외부 worker 프로세스 전부 지원
- OS별 완벽한 프로세스 상태 추적

지금 단계에서는 `codex` worker 하나를 안정적으로 복구하는 데 집중한다.
