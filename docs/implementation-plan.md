# Leopard Gecko Implementation Plan

> 이 문서는 [`init.md`](./init.md)와 현재 코드베이스를 기준으로, 지금 구현 상태와 다음 구현 순서를 고정하기 위한 문서다.
> 목표는 worker runtime loop를 유지한 채, `init.md`가 의도한 컨텍스트 라우터 구조로 자연스럽게 옮겨가는 것이다.

---

## 1. 현재 코드 기준 상태

현재 코드에는 이미 아래가 구현되어 있다.

- `Task`, `Session`, `AppConfig` 모델
- `config.json`, `sessions.json`, `tasks.jsonl` 저장소
- `submit()` 시 task 생성 및 session 반영
- worker `submit + poll` 계약
- run 완료/실패 반영 루프
- session queue의 다음 task 자동 시작
- idle session 발생 시 global queue 승격

즉 현재 상태는:

- 단순 submit-only 스켈레톤은 넘었고
- 최소한의 runtime lifecycle은 갖췄다

하지만 아직 라우팅 단계는 `init.md` 기준과 다르다.

---

## 2. `init.md` 대비 핵심 미스매치

### 2.1 라우터가 독립 컴포넌트가 아니다

현재는 `Orchestrator.submit()` 안에서:

1. task 생성
2. `task_note` 생성
3. route 결정

이 한 번에 일어난다.

즉 컨텍스트 에이전트 역할이 독립된 경계로 분리되어 있지 않다.

### 2.2 `task_note`가 실질적인 라우팅 메모가 아니다

현재 `task_note`는 고정 템플릿 기반 preview에 가깝다.

- 짧은 내부 메모라는 형식은 맞다
- 하지만 실제 라우팅 판단의 중요한 근거로 쓰이지는 않는다

### 2.3 현재 라우팅은 휴리스틱 토큰 매칭이다

지금 라우팅은 사실상:

- 새 `user_prompt`
- 기존 `task_history`의 `user_prompt`

사이의 문자열 겹침으로만 session을 고른다.

이는 `init.md`가 의도한:

- task note 참고
- session 상태 확인
- context rot 위험 판단
- 같은 작업 축인지 판단

과 차이가 있다.

### 2.4 `init.md`의 terminal session 서술과 현재 runtime 모델이 다르다

현재 코드는 generic runtime field를 쓴다.

- `worker_context_id`
- `active_run_id`
- `active_pid`
- `last_run_output_path`

반면 `init.md`는 `terminal_id` 중심으로 설명한다.

이건 기능적 문제라기보다 문서와 구현의 관점 차이다.

---

## 3. 이번 이후 단계의 핵심 목표

다음 단계의 핵심은 worker lifecycle을 더 복잡하게 만드는 것이 아니다.

핵심은:

- `task_note` 생성과 route 결정을 분리하고
- 라우팅을 독립된 컨텍스트 컴포넌트로 만들고
- 기존 휴리스틱 라우터를 fallback으로 유지하면서
- `init.md` 기준의 얇은 OpenAI 라우터를 붙일 수 있게 구조를 고정하는 것

즉 다음 단계의 중심은 **worker refactor가 아니라 router refactor**다.

---

## 4. 고정할 경계

다음 단계에서는 아래 세 가지를 분리한다.

### 4.1 `TaskNotePort`

- 입력: `user_prompt`
- 출력: 짧은 `task_note`
- 역할: 라우팅용 내부 메모 생성
- 금지: worker 실행용 prompt 재작성

### 4.2 `ContextRouter`

- 입력:
  - `task`
  - `config`
  - session snapshot
  - global queue 크기
- 출력: `RouteDecision`
- 역할:
  - 기존 session 할당
  - 새 session 생성
  - global queue 판단

### 4.3 `WorkerPort`

- 입력: 실제 실행할 `user_prompt`
- 출력: `WorkerSubmission`, `WorkerRunState`
- 역할: 실행 시작과 상태 조회

이 경계가 분리되면 `Orchestrator.submit()`은 조정자 역할만 갖게 된다.

---

## 5. 라우터 입력 범위

`init.md` 기준 MVP에서 라우터는 전체 코드베이스를 조사하는 heavy agent가 아니다.

라이터가 아니라 배치기다.

따라서 라우터 입력은 아래 정도로 제한한다.

- session id
- session status
- current task id
- queue 길이
- 최근 N개 `task_history`
- 최근 summary

이번 단계에서 하지 않는 것:

- 자유로운 코드 검색 tool
- 파일 read tool 다중 호출
- repo 전체 탐색형 planner
- 복잡한 memory schema

즉 첫 OpenAI 라우터는 **session registry 기반 lightweight judge**여야 한다.

---

## 6. 제안 인터페이스

```python
class SessionSnapshot(BaseModel):
    session_id: str
    status: SessionStatus
    current_task_id: str | None
    queue_size: int
    recent_history: list[TaskHistoryEntry]


class TaskNotePort(Protocol):
    def make_note(self, user_prompt: str) -> str:
        ...


class ContextRouter(Protocol):
    def decide(
        self,
        *,
        task: Task,
        config: AppConfig,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        ...
```

원칙:

- 기존 `router/policy.py`는 바로 제거하지 않는다
- 먼저 `HeuristicRouter` 클래스로 내린다
- 이후 OpenAI 기반 구현을 `AgentRouter`로 추가한다

---

## 7. OpenAI 라우터 MVP 범위

첫 OpenAI 라우터는 아래 정도면 충분하다.

- 입력:
  - `user_prompt`
  - `task_note`
  - session snapshot 목록
- 출력:
  - `assign_existing`
  - `create_new_session`
  - `enqueue_global`
  - `reason`
  - `confidence`

첫 구현에서 제외:

- 코드베이스 파일 조회
- 복잡한 tool call 루프
- session 외부 시스템 탐색

즉 “세션 상태를 보고 판단하는 agent”까지만 한다.

---

## 8. Fallback 전략

OpenAI 라우터는 아래 이유로 항상 실패 가능성이 있다.

- API 실패
- timeout
- structured output 파싱 실패
- confidence 낮음

따라서 다음 원칙을 고정한다.

- 1순위: `AgentRouter`
- 실패 또는 low-confidence: `HeuristicRouter`

즉 현재 휴리스틱 라우터는 폐기 대상이 아니라 fallback이다.

---

## 9. `task_note` 원칙

`task_note`는 계속 단순하게 유지한다.

- 1~2줄
- 같은 기능군/도메인 축인지 짧게 메모
- 라우팅 참고용

하지 않을 것:

- 태그 스키마 설계
- intent/domain structured fields 추가
- 실행용 refined prompt 생성

즉 `task_note`는 내부 메모고, worker에는 계속 전달하지 않는다.

---

## 10. `Session` 모델 관련 결정

현재 구현은 generic runtime field 기반이다.

이 방향을 유지하는 편이 낫다.

이유:

- 이미 `submit + poll` 구조가 그 전제 위에서 정리됐다
- `terminal_id`는 backend 독립적 모델보다 UI/환경 세부사항에 가깝다
- `init.md`의 본질은 terminal 관리보다 context routing이다

따라서 다음 단계 권장안은:

- 코드는 current generic runtime 구조 유지
- 필요하면 `init.md` 문서 표현을 generic worker/session 모델에 맞게 갱신

---

## 11. 구현 순서

다음 구현 순서는 아래가 적절하다.

### 11.1 1단계: router 경계 분리

- `ContextRouter` 인터페이스 추가
- 기존 `decide_route()`를 `HeuristicRouter` 클래스로 이동

### 11.2 2단계: task note 생성기 분리

- `_make_task_note()`를 `TaskNotePort` 구현체로 이동
- 초기 구현은 템플릿 기반 유지 가능

### 11.3 3단계: orchestrator wiring 변경

- `Orchestrator.submit()`이 직접 route 계산하지 않게 변경
- 순서를 아래처럼 고정

1. task 생성
2. note 생성
3. router 호출
4. state 반영
5. 필요 시 dispatch

### 11.4 4단계: snapshot 도입

- 라우터에는 `Session` 전체가 아니라 `SessionSnapshot`만 전달
- 최근 history 개수 cap 도입

### 11.5 5단계: OpenAI 기반 `AgentRouter` 추가

- structured output 사용
- `reason`, `confidence` 포함
- session snapshot만 보고 판단

### 11.6 6단계: fallback + trace logging

- invalid output / timeout / low confidence 시 `HeuristicRouter`
- `tasks.jsonl`에 라우팅 trace 이벤트 추가

### 11.7 7단계: 운영성 보강

- `lg poll`
- `lg tasks`
- 필요 시 router backend 선택 옵션

---

## 12. 추가할 이벤트

다음 단계에서 `tasks.jsonl`에 아래 이벤트를 권장한다.

- `task_note_created`
- `task_routing_started`
- `task_routing_completed`
- `task_routing_fallback_used`

payload는 간단히 유지한다.

- router kind
- decision
- assigned session id
- confidence
- short reason

---

## 13. 테스트 계획

### 13.1 단위 테스트

- `TaskNotePort` 기본 구현 테스트
- `HeuristicRouter` session 선택 테스트
- low-confidence fallback 조건 테스트

### 13.2 오케스트레이터 테스트

- note 생성 후 router 호출 순서 테스트
- `AgentRouter` 성공 시 결정 반영 테스트
- `AgentRouter` 실패 시 heuristic fallback 테스트
- worker에는 `user_prompt`만 전달되는지 테스트

### 13.3 통합 테스트

선택적으로:

- OpenAI API 키가 있는 환경에서만 도는 router smoke test
- structured output 파싱 검증

---

## 14. 다음 단계의 실제 목표

다음 구현 목표는 아래처럼 요약할 수 있다.

> 현재 runtime loop는 유지한 채, `task_note` 생성과 session 선택 판단을 `ContextRouter` 경계로 분리하고, 기존 휴리스틱 라우터를 fallback으로 유지하면서 `init.md` 기준의 얇은 OpenAI 라우팅 agent를 붙인다.

즉 다음 단계의 핵심은:

- worker 쪽을 더 일반화하는 것이 아니라
- 라우팅 책임을 독립시키고
- `task_note`를 실제 라우팅 입력으로 승격시키고
- AI 라우터를 붙여도 과하게 무거워지지 않는 MVP를 유지하는 것

이다.
