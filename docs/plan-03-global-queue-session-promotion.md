# Global Queue Session Promotion Plan

> 목표: global queue에 쌓인 task가 idle session이 없더라도 capacity가 남아 있으면 새 session을 만들어 실행되게 한다.

## 문제

현재 global queue 승격은 idle session 재사용만 지원한다.
세션이 하나도 없거나 모두 dead 상태인 경우, `max_terminal_num`이 남아 있어도 global queue는 계속 대기할 수 있다.

## 목표 상태

- global queue의 task는 아래 우선순위로 승격된다.
  1. idle session 재사용
  2. capacity가 남으면 새 session 생성
  3. 둘 다 안 되면 queue 유지

- submit 경로와 poll 경로가 같은 세션 생성 규칙을 공유한다.

## 구현 방향

### 방향 1. global queue 승격 로직을 route-like policy로 정리

지금 `_reserve_global_dispatch()`는 idle session만 찾는다.
이 로직을 좀 더 일반화해서:

- idle session 선택
- 새 session 생성 가능 여부 판단
- dispatch request 반환

까지 한 번에 처리한다.

### 방향 2. 새 session 생성 helper 분리

submit 시 세션 생성과 global queue 승격 시 세션 생성이 따로 놀지 않게 공통 helper를 둔다.

- `_start_task_in_new_session(...)`
- `_start_task_in_existing_idle_session(...)`

이런 식으로 경계를 분리하면 중복이 줄고 테스트가 쉬워진다.

## 변경 대상

- `src/leopard_gecko/orchestrator/pipeline.py`
- `src/leopard_gecko/models/session.py`
- `tests/test_pipeline.py`

## 세부 구현 계획

### 1. live session 계산 함수 도입

- dead가 아닌 session 수를 계산하는 helper 추가
- submit validation과 global promotion이 같은 기준을 사용하게 맞춘다

### 2. global queue 승격 절차 확장

`_reserve_global_dispatch()`를 아래 순서로 변경한다.

1. global queue 비었는지 확인
2. idle session 있으면 그 세션에 task 장착
3. 없으면 live session 수와 `max_terminal_num` 비교
4. capacity가 남으면 새 session 생성 후 task 장착
5. 아니면 `None` 반환

### 3. 승격 이벤트 구분

`task_promoted_from_queue`의 `source`는 유지하되, 새 session 생성인 경우 추가 메타데이터를 넣는다.

- `source=global`
- `created_session=true`

이렇게 두면 이후 디버깅이 쉬워진다.

### 4. dead session 고려

capacity 계산에서는 dead session을 제외한다.
필요하면 이후 dead session 청소 루틴을 따로 붙인다.

## 테스트 계획

- idle session이 있으면 기존처럼 그 세션에 붙어야 함
- idle session이 없고 capacity가 남아 있으면 새 session이 생성되어야 함
- capacity가 꽉 차 있으면 global queue에 그대로 남아야 함
- dead session만 있는 경우에도 새 session 생성이 가능해야 함

## 구현 순서

1. 세션 생성 helper 분리
2. `_reserve_global_dispatch()` 확장
3. 이벤트 payload 보강
4. 승격 관련 테스트 추가

## 비목표

- global queue를 다시 router에 재평가시키는 기능
- 우선순위 큐
- starvation 방지 스케줄러

현재 단계에서는 "막혀 있는 global queue를 자연스럽게 풀기"에만 집중한다.
