# Session Worktrees Plan

> 목표: 각 session이 독립된 git checkout에서 작업하게 만들어, 서로 다른 세션의 파일 변경이 한 작업 디렉터리에서 섞이지 않게 한다.

## 문제

현재 worker dispatch는 항상 orchestrator의 `cwd` 하나만 사용한다.

이 구조에서는:

- 서로 다른 session이 같은 파일 집합을 동시에 건드릴 수 있고
- session별 브랜치/변경 내역이 분리되지 않으며
- "이 session이 어느 checkout에서 작업 중인지"를 상태 파일만 보고 알기 어렵다

멀티 세션을 진짜 병렬 작업 단위로 쓰려면 session마다 별도 workspace가 필요하다.

## 목표 상태

- 각 session은 필요 시 자기 전용 git worktree를 가진다.
- 같은 session에 라우팅된 후속 task는 같은 worktree를 재사용한다.
- worker는 항상 session에 연결된 worktree 경로에서 실행된다.
- dispatch 실패나 복구 시에도 어떤 worktree를 썼는지 추적 가능하다.

## 설계 원칙

### 1. worktree는 session identity의 일부다

session은 더 이상 단순한 `worker_context_id`만 가진 논리 슬롯이 아니다.
다음 두 축이 같이 유지돼야 한다.

- agent/runtime context: `worker_context_id`
- filesystem context: `worktree_path`

즉 "같은 session에 이어 붙인다"는 말은:

- 같은 Codex thread를 재사용하고
- 같은 git worktree를 재사용한다

는 뜻이 된다.

### 2. 생성은 lazy 하게 한다

session 객체를 만들었다고 바로 worktree를 만들 필요는 없다.
가장 단순한 시점은 **첫 dispatch 직전**이다.

이렇게 하면:

- global queue에만 머무는 session 때문에 checkout을 미리 만들지 않아도 되고
- worker submit 실패 시 rollback 범위가 명확하며
- 현재 `dispatch_with_rollback()` 구조에 자연스럽게 들어간다

### 3. 첫 단계에서는 자동 정리하지 않는다

dead/blocked session의 worktree를 즉시 삭제하면 디버깅과 수동 복구가 어려워진다.
초기 구현에서는 worktree를 남기고, 나중에 별도 prune command를 추가하는 편이 안전하다.

## 권장 데이터 모델 변경

`Session`에 아래 필드를 추가한다.

- `worktree_path: str | None`
- `worktree_branch: str | None`
- `worktree_base_ref: str | None`

이 정도면 충분하다.

- 실제 실행 경로를 알 수 있고
- 어느 브랜치인지 추적할 수 있으며
- 어떤 기준 ref에서 파생됐는지 복원 가능하다

별도의 `workspace_mode`, `worktree_status` 같은 상태 enum은 처음부터 넣지 않는 편이 낫다.
`worktree_path is None`이면 shared cwd 모드로 해석하면 된다.

## 권장 설정

`AppConfig`에 작은 설정만 추가한다.

```python
class WorktreeConfig(BaseModel):
    enabled: bool = False
    root_dir: str | None = None
    branch_prefix: str = "lg"
    base_ref: str | None = None
```

설명:

- `enabled`: session worktree 기능 on/off
- `root_dir`: worktree를 만들 루트. 기본값은 main repo 바깥의 별도 디렉터리 권장
- `branch_prefix`: session 브랜치 prefix
- `base_ref`: 비우면 현재 HEAD 또는 현재 브랜치 기준

중요한 점은 **worktree root를 현재 repo 내부에 두지 않는 것**이다.
기본 data dir이 `.leopard-gecko/`인 현재 구조에서는 그 아래에 worktree를 만들면 nested checkout이 생겨 다루기 불편하다.

권장 기본값 예시:

- main repo: `/repo/app`
- data dir: `/repo/app/.leopard-gecko`
- worktree root: `/repo/.leopard-gecko-worktrees/app`

## 새 모듈 제안

`src/leopard_gecko/worktree/manager.py`

책임은 하나로 제한한다.

- session용 worktree를 보장하고 경로/브랜치 정보를 돌려준다

대략 이런 형태면 충분하다.

```python
class SessionWorktree(BaseModel):
    path: str
    branch: str
    base_ref: str
    created: bool = False


class WorktreeManager:
    def ensure(self, *, session_id: str) -> SessionWorktree:
        ...
```

이 모듈은:

- 현재 `cwd`가 git repo인지 확인
- worktree root 결정
- branch 이름 결정
- 기존 worktree가 있으면 재사용
- 없으면 `git worktree add`로 생성

까지만 담당하면 된다.

## 오케스트레이터 연결 방식

핵심 변경은 `pipeline.py`의 dispatch 경로 하나다.

현재:

- `_dispatch_task()`가 무조건 `self.cwd`를 worker에 넘긴다

변경 후:

1. `session = _find_session(...)`
2. worktree 기능이 켜져 있으면 `WorktreeManager.ensure(session_id=...)`
3. session에 `worktree_path`, `worktree_branch`, `worktree_base_ref` 저장
4. worker submit에는 `cwd = Path(session.worktree_path) if session.worktree_path else self.cwd`

즉 worker adapter는 거의 바꾸지 않아도 된다.
변경의 중심은 orchestrator다.

## dispatch rollback 규칙

현재 구조에 맞추면 rollback 규칙은 단순해야 한다.

### 새 session + 첫 worktree 생성 + dispatch 실패

- 세션 생성 rollback과 함께 방금 만든 worktree만 제거 가능

### 기존 session + 기존 worktree 재사용 + dispatch 실패

- worktree는 유지
- task만 global queue로 되돌림

### 기존 session + worktree ensure 실패

- worker submit까지 가지 않음
- 현재 dispatch failure 경로로 처리

즉 "이번 dispatch에서 새로 만든 worktree인가"만 알면 된다.

## git 브랜치 전략

첫 단계에서는 session당 브랜치 1개면 충분하다.

- 브랜치명 예시: `lg/<session_id>`
- base ref: config 값이 있으면 그것, 없으면 현재 브랜치 또는 `HEAD`

중요한 건 브랜치를 task마다 새로 만들지 않는 것이다.
이 시스템은 task isolation보다 **session isolation**을 원한다.

## 복구와 관측성

run metadata에 실제 실행 `cwd`를 기록하는 걸 권장한다.

- `worker_runs/<session_id>/<task_id>.meta.json`
- 추가 필드: `cwd`

그러면 재시작 후에도:

- 어느 worktree에서 실행됐는지
- 세션 상태와 디스크 상태가 맞는지

를 비교할 수 있다.

## 테스트 계획

최소 테스트는 아래면 충분하다.

### 1. 모델 직렬화

- `Session`의 worktree 필드가 저장/복원되는지

### 2. dispatch 경로

- worktree 없는 session은 기존 `cwd`를 쓰는지
- worktree 있는 session은 그 경로를 worker에 넘기는지

### 3. worktree manager

- git repo에서 `ensure()`가 경로/브랜치를 정상 반환하는지
- 이미 존재하는 worktree를 중복 생성하지 않는지

### 4. rollback

- 새 session 첫 dispatch 실패 시 세션 rollback이 worktree 상태와 충돌하지 않는지

## 구현 순서

1. `Session` 모델에 worktree 필드 추가
2. config에 작은 `WorktreeConfig` 추가
3. `WorktreeManager` 도입
4. `_dispatch_task()`에서 session별 `cwd` 선택
5. run metadata에 `cwd` 기록
6. rollback 및 테스트 보강

## 비목표

- task마다 새 브랜치 생성
- 자동 merge/rebase
- dead session 자동 prune
- non-git 프로젝트를 위한 copy-based workspace

지금 단계에서는 "session 하나 = 하나의 git worktree"만 깔끔하게 성립하면 충분하다.
