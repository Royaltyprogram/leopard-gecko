좋아. 지금까지 이야기한 내용을 기준으로, 최종 기획사항을 한 번에 볼 수 있게 정리해줄게.

1. 제품 정의

이 시스템은 하나의 사용자 인터페이스 뒤에서 여러 코딩 에이전트 terminal session을 관리하는 컨텍스트 라우팅 CLI다.

사용자는 오직 하나의 에이전트에게만 자연어로 명령한다.
그러면 상위의 컨텍스트 에이전트가:
	•	사용자의 명령을 기록하고
	•	짧은 라우팅용 메모를 남기고
	•	현재 살아 있는 session들을 확인한 뒤
	•	가장 적절한 session에 작업을 큐잉하거나
	•	맞는 session이 없으면 새 terminal session을 만들어 할당한다

핵심 목적은 하나다.

서로 관련 없는 작업이 같은 세션에 섞여 들어가면서 생기는 context rot를 줄이고,
세션을 기능/도메인 단위로 유지하면서 효율적으로 라우팅하는 것.

⸻

2. 핵심 철학

이 시스템은 “작업을 더 잘 구현하는 에이전트”라기보다, 먼저 작업을 적절한 세션에 붙이는 오케스트레이터다.

중요한 원칙은 아래와 같다.

1) 사용자 원문 프롬프트는 그대로 보존

상위 에이전트가 사용자의 말을 재작성해서 실제 코딩 에이전트에 주입하지 않는다.

2) task note는 라우팅용 참고 메모일 뿐

task note는 구현 지시문이 아니다.
짧게 1~2줄로 “이 작업이 어느 영역에 가까운지”만 기록한다.

3) task note는 worker session에 넘기지 않음

task note는 컨텍스트 에이전트가 다음 라우팅 때 참고하기 위한 내부 메모다.
실제 코딩 에이전트에는 user_prompt만 전달한다.

4) sessions.json에는 과거 작업을 계속 누적

각 세션이 지금까지 어떤 작업들을 맡아왔는지가 다음 라우팅의 핵심 근거가 된다.

5) 기존 세션 재사용을 우선하되, context rot 위험이 있으면 분리

관련성이 높으면 같은 세션에 큐잉하고, 아니면 새 세션을 만든다.

⸻

3. 최종 워크플로우

Step 1. 사용자 명령 입력

사용자가 하나의 인터페이스에서 자연어로 명령한다.

예:
	•	“관리자 유저 목록에 pagination 붙여줘”
	•	“auth 에러 처리 세션에 401/403 구분도 추가해줘”

Step 2. task 생성

컨텍스트 에이전트는 이 명령을 기반으로 task를 만든다.

하지만 여기서 하는 일은 refine prompt 생성이 아니다.
대신 아래 두 가지만 남긴다.
	•	user_prompt: 사용자의 원문
	•	task_note: 라우팅용 짧은 메모

예:
	•	“admin/users 영역 확장으로 보임. 관련 세션 있으면 우선 라우팅.”
	•	“기존 auth 에러 처리 작업과 가까워 보임.”

Step 3. 현재 sessions.json 조회

컨텍스트 에이전트는 현재 살아 있는 terminal session들의 상태와 과거 작업 히스토리를 읽는다.

확인 대상:
	•	현재 상태: idle / busy / blocked / dead
	•	현재 작업
	•	큐 길이
	•	과거 task_history
	•	최근 이 세션이 맡아온 작업 성격

Step 4. 라우팅 판단

새 task를 어느 session에 붙일지 판단한다.

판단 기준:
	•	새 user_prompt와 기존 session의 과거 작업이 얼마나 문맥적으로 상통되는지
	•	task_note 기준으로 같은 도메인/기능군인지
	•	현재 그 session에 큐잉하는 게 자연스러운지
	•	잘못 붙였을 때 context rot 위험이 큰지
	•	이미 queue가 너무 길지 않은지

Step 5. 기존 세션이 적절하면 큐잉

적절한 session이 있으면:
	•	idle이면 바로 할당
	•	busy면 현재 작업이 끝난 뒤 수행되도록 queue에 추가

즉, 기존 작업을 중간에 끊지 않고 순차 처리한다.

Step 6. 적절한 세션이 없으면 새 세션 생성 검토

관련 세션이 없으면 config에 설정된 max_terminal_num을 본다.
	•	남는 terminal 수가 있으면 새 session 생성
	•	이미 한도에 도달했으면 global pending queue에 넣고 대기

Step 7. worker에는 user_prompt만 전달

실제 코딩 에이전트 session에는 내부 메모를 주지 않는다.

전달되는 것은 기본적으로:
	•	사용자의 원문 프롬프트

필요시 최소한의 시스템 수준 세션 관리 정보만 별도로 줄 수 있지만,
라우팅용 task_note는 주입하지 않는다.

Step 8. 작업 완료 후 sessions.json 갱신

task가 끝나면 해당 세션의 task_history에 누적 저장한다.

남겨야 할 것:
	•	어떤 프롬프트였는지
	•	어떤 task_note였는지
	•	완료/실패/중단 여부
	•	필요시 짧은 결과 메모

이 히스토리가 이후 라우팅의 근거가 된다.

⸻

4. task의 최종 개념

task는 실행 명세가 아니라 라우팅 단위다.

필수 개념은 이 정도다.
	•	task_id
	•	user_prompt
	•	task_note
	•	routing
	•	queue_status
	•	created_at

여기서 task_note는 아주 단순해야 한다.

예시:

{
  "task_id": "task_20260401_014",
  "user_prompt": "관리자 유저 목록에 pagination 붙여줘",
  "task_note": "admin/users 쪽 목록 기능 확장으로 보임. 관련 세션이 있으면 우선 라우팅.",
  "routing": {
    "assigned_session_id": null,
    "decision": "pending",
    "reason": null
  },
  "queue_status": "pending",
  "created_at": "2026-04-01T10:20:00Z"
}

중요한 점:
	•	task_note는 구조화 태그 묶음이 아니다
	•	intent, domain_tags 같은 복잡한 중간 스키마는 MVP에서 제거
	•	컨텍스트 에이전트가 한두 줄 메모 남기는 수준으로 유지

⸻

5. sessions.json의 최종 역할

sessions.json은 단순 현재 상태 파일이 아니라,
현재 상태 + 과거 작업 누적 히스토리를 담는 레지스트리다.

세션마다 적어도 아래 정보가 있어야 한다.
	•	session_id
	•	terminal_id
	•	status
	•	current_task_id
	•	queue
	•	task_history
	•	created_at
	•	last_heartbeat

예시 구조:

{
  "sessions": [
    {
      "session_id": "sess_admin_01",
      "terminal_id": "term_2",
      "status": "busy",
      "current_task_id": "task_20260401_014",
      "queue": ["task_20260401_015"],
      "task_history": [
        {
          "task_id": "task_20260401_003",
          "user_prompt": "관리자 유저 테이블에 정렬 기능 붙여줘",
          "task_note": "admin/users 테이블 관련 작업. 기존 관리자 세션과 잘 맞음.",
          "status": "completed"
        },
        {
          "task_id": "task_20260401_014",
          "user_prompt": "관리자 유저 목록에 pagination 붙여줘",
          "task_note": "admin/users 쪽 목록 기능 확장으로 보임. 관련 세션이 있으면 우선 라우팅.",
          "status": "running"
        }
      ],
      "created_at": "2026-04-01T09:28:00Z",
      "last_heartbeat": "2026-04-01T10:22:14Z"
    }
  ]
}

핵심은 이것이다.

다음 프롬프트가 들어왔을 때,
컨텍스트 에이전트는 현재 session 상태와 과거 task_history를 보고
“이 세션이 원래 어떤 종류의 일들을 해온 세션인지” 판단한다.

⸻

6. 라우팅 정책

라우팅은 아래 우선순위로 동작한다.

1) 기존 세션에 자연스럽게 이어질 수 있는가

예:
	•	같은 기능군
	•	같은 하위 도메인
	•	과거 프롬프트들과 결이 비슷함
	•	같은 맥락으로 이어 붙였을 때 세션 오염이 적음

그렇다면 기존 세션으로 보낸다.

2) 기존 세션이 있지만 붙이면 세션이 더러워지는가

예:
	•	비슷해 보이지만 실제로는 다른 기능 축
	•	같은 폴더 근처지만 목적이 다름
	•	이미 세션이 너무 넓어짐

이 경우는 관련성이 약간 있더라도 새 세션 생성이 더 낫다.

3) 적절한 세션이 없으면 새 세션 생성

단, max_terminal_num 이하일 때만.

4) 새 세션도 못 만들면 글로벌 대기

terminal 한도를 넘었으면 global queue에 넣어 대기한다.

⸻

7. 큐 정책

큐는 두 종류가 필요하다.

세션 내부 큐

이미 특정 session에 배정된 작업들이 대기하는 곳.

예:
	•	같은 admin/users 세션에 붙은 후속 명령들

글로벌 대기 큐

아직 어떤 session에도 못 들어간 작업들.

필요한 경우:
	•	관련 session 없음
	•	terminal 최대 수 초과
	•	생성 대기 필요

즉 상태는 최소 이 정도가 된다.
	•	pending
	•	queued_in_session
	•	queued_globally
	•	running
	•	completed
	•	failed

⸻

8. 컨텍스트 에이전트와 worker 에이전트의 역할 분리

이 설계에서 두 역할은 명확히 분리된다.

컨텍스트 에이전트
	•	사용자 입력 받기
	•	task 만들기
	•	task_note 남기기
	•	sessions.json 확인
	•	session 라우팅
	•	queue 관리
	•	새 terminal/session 생성 결정
	•	상태 파일 갱신

worker 코딩 에이전트
	•	배정된 user_prompt 수행
	•	해당 세션 terminal 안에서 실제 코딩
	•	결과 반환
	•	종료 시 결과 상태 반영

즉:

컨텍스트 에이전트는 배치와 정리 담당
코딩 에이전트는 실행 담당

⸻

9. 왜 refine prompt를 없앴는가

이건 이번 논의에서 중요한 변경사항이었다.

초기엔 사용자의 입력을 더 명확한 prompt로 다시 써서 task에 넣으려 했지만, 최종적으로는 제외했다.

이유는 다음과 같다.

1) 라우팅용 문장이 실행용 프롬프트를 오염시킬 수 있음

상위 에이전트의 해석이 worker 성능을 떨어뜨릴 수 있다.

2) 상위 에이전트는 구현 지시자가 아님

상위 계층의 역할은 “어떻게 구현할지”가 아니라 “어디에 붙일지”다.

3) 사용자 원문 프롬프트를 그대로 쓰는 게 안전

worker는 사용자의 실제 의도를 직접 받는 편이 낫다.

그래서 최종 결론은:

refined_prompt는 만들지 않는다.
대신 task_note만 남기고, 그것은 라우팅 참고용으로만 쓴다.

⸻

10. 파일 구성

MVP 기준으로 필요한 파일은 아래 정도다.

config.json

시스템 설정

예:
	•	max_terminal_num
	•	queue_policy
	•	session_idle_timeout_min

sessions.json

현재 살아 있는 세션 레지스트리 + 과거 작업 히스토리

tasks.jsonl 또는 tasks.json

모든 task 생성/라우팅/상태 변경 이력 저장

권장:
	•	전체 이력은 append-only 로그로 tasks.jsonl
	•	현재 세션 상태는 sessions.json

⸻

11. 최소 기능 범위(MVP)

이번 기획 기준으로 MVP는 아래까지다.

포함
	•	사용자 명령 입력
	•	task 생성
	•	짧은 task_note 생성
	•	sessions.json 조회
	•	기존 세션 라우팅
	•	세션 큐잉
	•	새 세션 생성
	•	terminal 최대 수 제한
	•	sessions.json에 task_history 누적
	•	worker에는 user_prompt만 전달

아직 제외
	•	복잡한 multi-agent planner
	•	세션 간 협업 DAG
	•	자동 PR 생성
	•	고급 충돌 해결
	•	장기 기억 최적화
	•	복잡한 의미 벡터 검색

즉 MVP는 얇고 실용적인 세션 라우터다.

⸻

12. 예상되는 예외 상황

최종 설계상 꼭 고려해야 할 예외는 이 정도다.

1) sessions.json에는 살아있다고 나오는데 실제 terminal이 죽어 있음

heartbeat로 감지해서 dead 처리 필요

2) 관련 session은 있지만 queue가 너무 길다

관련성만 볼 게 아니라 load도 같이 봐야 함

3) 비슷해 보여도 붙이면 context rot가 심해짐

단순 키워드 일치보다 “같은 작업 축인지”가 중요

4) terminal 최대 수 초과

새 session 생성 대신 global queue로 보내야 함

5) 사용자의 하나의 명령이 사실 여러 작업일 수 있음

MVP에서는 우선 하나의 task로 처리해도 되지만, 나중엔 분할 가능성 고려 가능

⸻

13. 최종 한 줄 정의

이제 네 시스템은 이렇게 정의하면 가장 정확하다.

사용자의 원문 프롬프트를 그대로 보존한 채, 라우팅용 짧은 task note를 남기고, 세션별 누적 작업 히스토리와 현재 상태를 바탕으로 적절한 terminal session에 작업을 큐잉하거나 새 세션을 생성해 할당하는 CLI 기반 컨텍스트 오케스트레이터

⸻

14. 아주 짧게 요약하면

이 시스템은 결국 이렇게 동작한다.
	1.	사용자가 하나의 에이전트에게 말한다
	2.	컨텍스트 에이전트가 그 말을 task로 기록하고 짧은 메모를 남긴다
	3.	기존 세션 히스토리를 보고 어디에 붙일지 판단한다
	4.	맞는 세션이 있으면 큐잉한다
	5.	없으면 terminal 여유를 보고 새 세션을 만든다
	6.	worker에는 원문 프롬프트만 넘긴다
	7.	작업이 끝나면 그 세션의 작업 히스토리에 계속 누적한다

원하면 다음 단계로 바로 이어서 최소 스키마 초안 (config.json, tasks.jsonl, sessions.json) 형태로 작성해줄게.