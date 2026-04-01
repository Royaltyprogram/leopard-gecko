# ADR: Router Availability Fallback

- 상태: Proposed
- 날짜: 2026-04-01
- 결정 대상: submit 경로의 기본 가용성

## Context

현재 기본 submit 경로는 OpenAI 호출 두 번에 의존한다.

1. `task_note` 생성
2. route 결정

이 구조의 문제:

- API key가 없으면 submit 전체가 실패한다.
- API timeout이나 structured output 오류도 바로 사용자 실패가 된다.
- 단순한 local 개발/테스트에도 네트워크 의존이 생긴다.

반면 현재 코드베이스는 lightweight router만 필요로 한다.
즉 “항상 agent를 불러야만 하는” 구조일 필요가 없다.

## Decision

기본 경로에 fallback을 넣는다.

### 채택 내용

1. task note 생성은 `FallbackTaskNoteGenerator`를 둔다.
2. route 결정은 `FallbackRouter`를 둔다.
3. agent 호출 실패 또는 low confidence일 때 heuristic으로 내려간다.
4. submit은 가능한 한 성공을 우선한다.

## Chosen Design

### Task note

기본 순서:

1. `AgentTaskNoteGenerator`
2. 실패 시 `TemplateTaskNoteGenerator`

규칙:

- note 생성 실패는 submit 실패 사유가 되지 않는다.
- 최악의 경우 template note 하나면 충분하다.

### Router

기본 순서:

1. `AgentRouter`
2. 아래 상황이면 `HeuristicRouter`
   - API key 없음
   - network error
   - invalid JSON
   - invalid payload
   - confidence threshold 미만

### HeuristicRouter 초안

입력은 현재 session snapshot만 사용한다.

- `task.user_prompt`
- `task.task_note`
- 최근 history의 `user_prompt`, `task_note`
- queue limit
- 남는 capacity

규칙은 단순하게 시작한다.

- 관련 토큰 겹침이 충분하면 existing session
- 아니고 capacity 남으면 new session
- 아니면 global queue

## Consequences

### 장점

- API key 없이도 submit 가능
- network 문제로 전체 orchestrator가 멈추지 않음
- e2e agent path는 유지하면서 local usability가 좋아짐

### 단점

- fallback 결과가 agent 판단보다 덜 정교할 수 있음
- router 구현이 둘로 나뉘어 테스트 수가 늘어남

## Why This Over Alternatives?

### 대안 1. 현재처럼 agent-only 유지

기각 이유:

- 개발과 운영 가용성이 너무 낮다.

### 대안 2. template note만 fallback, router는 agent-only

기각 이유:

- 진짜 병목은 router 자체의 네트워크 의존이다.

### 대안 3. 아예 heuristic을 기본값으로 바꾸고 agent는 옵션화

보류 이유:

- 지금 구조와 사용자 기대를 크게 바꿀 수 있다.
- 우선은 “agent 우선, 실패 시 fallback”이 더 점진적이다.

## Implementation Sketch

### 새 타입

- `HeuristicRouter`
- `FallbackRouter`
- `FallbackTaskNoteGenerator`

### config 변경

가능하면 작게 간다.

- `AgentRouterConfig`에 `min_confidence: float = 0.6` 추가 검토

그 외에는 큰 enum 확장을 피한다.

### factory 변경

- `build_router()`는 직접 `AgentRouter`를 반환하지 않고 `FallbackRouter(...)`를 반환
- `_resolve_task_note_port()`도 fallback generator를 사용

## Files Affected

- `src/leopard_gecko/router/factory.py`
- `src/leopard_gecko/router/agent.py`
- `src/leopard_gecko/router/task_notes.py`
- `src/leopard_gecko/router/policy.py`
- `src/leopard_gecko/models/config.py`
- `tests/test_router.py`
- `tests/test_pipeline.py`

## Validation

- API key가 없어도 submit이 성공해야 함
- agent router가 invalid payload를 반환하면 heuristic으로 fallback
- low confidence면 fallback
- agent 성공 경로의 기존 e2e 테스트는 그대로 유지

## Follow-up

이번 ADR에서는 fallback까지만 다룬다.
추후 필요하면 heuristic scoring을 더 세련되게 만들 수 있지만, 지금은 단순하고 재현 가능한 규칙이 우선이다.
