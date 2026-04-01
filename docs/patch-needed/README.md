# Patch Needed

이 디렉터리는 현재 코드베이스에서 우선적으로 손봐야 할 6개 패치의 구현 계획을 모아둔 곳이다.

| Patch | 문서 | 형식 | 핵심 목적 |
|---|---|---|---|
| 01 | [dispatch-failure-rollback.md](./dispatch-failure-rollback.md) | 체크리스트 중심 계획서 | worker submit 실패 시 세션이 망가진 상태로 남지 않게 롤백 |
| 02 | [global-queue-autopromotion-rfc.md](./global-queue-autopromotion-rfc.md) | RFC | active run이 없어도 global queue가 자동으로 전진 |
| 03 | [session-timeout-lifecycle-faq.md](./session-timeout-lifecycle-faq.md) | Q&A / FAQ | timeout, blocked, dead 세션 수명주기 정리 |
| 04 | [task-snapshot-sync-phases.md](./task-snapshot-sync-phases.md) | 단계별 실행 계획 | task snapshot과 실제 runtime 상태 동기화 |
| 05 | [poll-io-reduction-matrix.md](./poll-io-reduction-matrix.md) | 표 중심 설계 문서 | poll hot path의 파일 I/O와 로그 쓰기 비용 절감 |
| 06 | [router-availability-adr.md](./router-availability-adr.md) | ADR | OpenAI 의존 submit 경로에 fallback 추가 |

## 권장 적용 순서

1. `dispatch-failure-rollback.md`
2. `global-queue-autopromotion-rfc.md`
3. `session-timeout-lifecycle-faq.md`
4. `task-snapshot-sync-phases.md`
5. `poll-io-reduction-matrix.md`
6. `router-availability-adr.md`

## 순서 이유

- 01은 상태 오염을 막는 안전장치다.
- 02는 현재 queue starvation을 바로 줄인다.
- 03은 capacity 회복과 운영 안정성을 만든다.
- 04는 snapshot을 hot path답게 믿을 수 있게 만든다.
- 05는 그 다음에 I/O 비용을 줄여도 설계가 덜 흔들린다.
- 06은 마지막으로 기본 가용성을 올린다.

## 공통 원칙

- 데이터 무결성을 먼저 고친다.
- `sessions.json`을 single source of truth로 유지하되, hot path에서는 작은 snapshot 파일을 적극 활용한다.
- worker에는 계속 `user_prompt`만 전달한다.
- 새 상태나 이벤트를 추가하더라도 모델은 가능한 한 단순하게 유지한다.
