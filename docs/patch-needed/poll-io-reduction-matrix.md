# Poll I/O Reduction Matrix

> 형식: 표 중심 설계 문서
> 목표: `poll_runs()`가 세션 수와 poll 주기에 비례해 과하게 파일을 읽고 쓰는 문제를 줄인다.

## 1. Hot Path 진단

| 지점 | 현재 동작 | 비용 유형 | 왜 문제인가 |
|---|---|---|---|
| active run 수집 | `sessions.json` 전체 load | 파일 읽기 | poll마다 전체 세션 파일 역직렬화 |
| running heartbeat 반영 | active run마다 `sessions_repo.update()` | lock + 전체 파일 쓰기 | run 수가 늘수록 write 횟수 선형 증가 |
| completion/failure 반영 | run마다 별도 update | lock + 전체 파일 쓰기 | 같은 poll에서 여러 번 전체 파일 rewrite |
| heartbeat 이벤트 로그 | 매 poll tick append | 로그 증가 | 장기 실행 시 `tasks.jsonl` 팽창 |
| global promotion | promotion마다 추가 update | lock + 쓰기 | 빈번한 작은 mutation 반복 |

## 2. 목표 상태

| 목표 | 설명 |
|---|---|
| 세션 파일 읽기 최소화 | 한 poll에서 가능한 한 한 번만 load |
| 세션 파일 쓰기 배치화 | 여러 run 결과를 한 번의 update에 반영 |
| heartbeat 로그 절제 | 상태 변화가 없으면 로그 남발 금지 |
| dispatch 이후 후속 mutation 단순화 | promotion과 finalize가 같은 mutation pass를 공유 |

## 3. 권장 설계

### A. 2단계 poll 구조

| 단계 | 락 여부 | 하는 일 |
|---|---|---|
| 1. snapshot 수집 | 무락 | `sessions.json` 1회 load, active run 목록 생성 |
| 2. worker poll | 무락 | 각 run에 대해 외부 worker 상태 조회 |
| 3. batch apply | 락 1회 | 모든 결과를 `sessions_repo.update()` 한 번으로 반영 |

핵심:

- 외부 worker poll 동안 락을 잡지 않는다.
- 세션 반영은 가능한 한 1회 update로 묶는다.

### B. heartbeat throttling

| 항목 | 제안 |
|---|---|
| 세션 `last_heartbeat` | 계속 갱신 |
| `session_heartbeat` 로그 이벤트 | 매 tick이 아니라 변화 조건에서만 기록 |
| 변화 조건 예시 | worker_context 변경, last_message 변경, 또는 N초 이상 경과 |

첫 버전 권장:

- 이벤트 로그는 heartbeat마다 남기지 않는다.
- `last_heartbeat`는 세션 파일에만 반영한다.

이유:

- 디버깅 정보보다 로그 팽창 비용이 더 크다.

## 4. 상세 변경안

| 변경안 | 난이도 | 효과 | 비고 |
|---|---|---|---|
| `_collect_active_runs()`가 미리 load한 state를 재사용 | 낮음 | 읽기 1회 감소 | 가장 먼저 가능 |
| poll 결과를 dict로 모아 한 번의 update에 적용 | 중간 | 쓰기 횟수 크게 감소 | 추천 |
| finalize + promotion을 같은 update pass에서 처리 | 중간 | lock 수 감소 | 코드 구조 정리 필요 |
| heartbeat 이벤트 throttling 또는 제거 | 낮음 | 로그 팽창 억제 | 추천 |
| `tasks_log.read_all()` hot path 제거 유지 | 이미 대부분 해결 | 안정성 유지 | 현 상태 유지 가능 |

## 5. 구체 구현 포인트

| 파일 | 작업 |
|---|---|
| `src/leopard_gecko/orchestrator/pipeline.py` | batch poll/apply 구조 도입 |
| `src/leopard_gecko/store/sessions_repo.py` | 필요 시 “load once then update” helper 검토 |
| `src/leopard_gecko/models/task.py` | poll 결과 집계용 작은 모델 추가 가능 |
| `tests/test_pipeline.py` | 동작 동일성 테스트 |
| `tests/test_worker_loop.py` | loop 수준 회귀 테스트 |

## 6. 추천 구현 방식

| 선택지 | 장점 | 단점 | 추천 여부 |
|---|---|---|---|
| 지금처럼 per-run update 유지 | 코드 단순 | I/O 비효율 큼 | 비추천 |
| sessions 전체 batch apply | 성능과 단순성 균형 | 약간의 리팩터링 필요 | 추천 |
| SQLite로 교체 | 확장성 큼 | 과함 | 이번 패치 비대상 |

## 7. 테스트 전략

| 테스트 | 기대 결과 |
|---|---|
| active run 3개 poll | 세션 상태는 모두 반영되고 결과는 기존과 동일 |
| 완료 1개, running 2개 혼합 | `PollRunsResult` 집계가 유지됨 |
| heartbeat만 계속 오는 상태 | 세션 heartbeat는 갱신되지만 로그는 폭증하지 않음 |
| promotion 포함 poll | dispatch 개수와 session 상태가 기존 의미와 같음 |

## 8. 성공 기준

| 기준 | 판단 방법 |
|---|---|
| 한 poll에서 `sessions.json` write 횟수 감소 | 저장소 spy 또는 monkeypatch로 검증 |
| behavior regression 없음 | 기존 테스트 유지 + 신규 테스트 추가 |
| 로그 팽창 억제 | heartbeat-only 시나리오에서 이벤트 수 감소 확인 |
