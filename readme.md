# Leopard Gecko

**The First Context Engineer That Replaces You.**

코딩 에이전트 세션을 자동으로 라우팅하고 관리하는 컨텍스트 오케스트레이터.

---

## Why Leopard Gecko?

최근 코딩 에이전트들의 성능에 가장 큰 영향을 미치는 것이 컨텍스트라는 것은 이제 모두가 알고 있습니다. 이러한 컨텍스트 문제를 "불필요한 내용을 제거하거나, 이전 내용을 요약하여 전달하는 등" 같은 이른바 메모리 시스템으로 해결하려고 합니다. 하지만 이러한 접근법이 최선일까요?

저희는 이 문제의 꼬인 매듭을 모델이 아니라 **인간**으로 생각해봤습니다. 사실상 인간이 이전 컨텍스트와 관련성이 떨어지는 내용을 하나의 세션에서 질문하고, 대화가 길어져도 제때 새로운 세션을 시작해주지 않아서 그렇습니다. 저희는 이러한 인간의 작업들을 **Context Engineering**이라고 부르겠습니다.

Context Engineering의 핵심 병목은 **인간의 컨텍스트 윈도우**입니다. 여러 세션을 실행하고 있다면, 이전에 어떤 작업을 어떤 세션에서 실행했는지도 헷갈리고, 대화의 내용에 집중하다 보면 컨텍스트 윈도우가 얼마나 찼는지도 잊게 됩니다. 모델은 점차 멍청한 답을 뱉어내기 시작하죠.

**그래서 Leopard Gecko를 만들었습니다.**

Leopard Gecko는 인간을 대체하는 최초의 Context Engineer입니다. 백그라운드에서 실행되는 코딩 에이전트 세션들을 관리합니다. 인간이 프롬프트를 입력하면 이를 적절한 작업 세션으로 라우팅하거나, 컨텍스트 rot이나 포화가 예상된다면 새로운 세션을 시작하기도 합니다.

### Sub Agent와 뭐가 다른가요?

가장 중요한 차이: **Leopard Gecko는 당신의 코딩 에이전트 품질에 그 어떠한 영향도 주지 않습니다.**

기존 Sub Agent의 가장 큰 문제는 관리자 모델의 역량 병목이었습니다. 관리자 모델이 멍청한 지시를 내리면 Sub Agent들은 그저 행동할 뿐입니다. 사용자의 지시를 잘못 오인하면 완벽히 다른 방향이면서 엄청난 양의 결과물들이 쏟아져 나옵니다.

반면에 Leopard Gecko는 그저 여러 개의 코딩 에이전트 세션들과 인간 사이의 **어댑터** 역할만 해줄 뿐, 당신의 쿼리 내용을 추가하거나 삭제하지 않습니다. 그렇기에 온전히 사용자의 프롬프트 품질과 Codex, Claude Code 같은 에이전트 시스템의 본질적 성능에 의존합니다.

---

## Architecture

```
  User Prompt
       |
       v
  +-----------+     +-----------------+
  |    CLI    | --> |   Orchestrator  |
  |  / TUI    |     |   (Pipeline)    |
  +-----------+     +--------+--------+
                             |
                    +--------v--------+
                    |  Context Router |  <-- LLM-based routing (OpenAI)
                    |  (AgentRouter)  |
                    +--------+--------+
                             |
              +--------------+--------------+
              |              |              |
        +-----v----+  +-----v----+  +------v---+
        | Session 1 |  | Session 2 |  | Session N |
        | (Codex)   |  | (Codex)   |  | (Codex)   |
        +-----------+  +-----------+  +-----------+
```

### Core Components

| Component          | Description                                             |
| ------------------ | ------------------------------------------------------- |
| **Orchestrator**   | Task submission, worker polling, session lifecycle 관리 |
| **Context Router** | LLM 기반 라우팅 - 태스크를 가장 적합한 세션으로 배정    |
| **Worker Adapter** | 코딩 에이전트(Codex 등) 추상화 레이어                   |
| **Store**          | 파일 기반 atomic 영속성 (sessions.json, tasks.jsonl)    |
| **TUI**            | Textual 기반 인터랙티브 터미널 UI                       |

### Routing Decisions

Router는 각 태스크에 대해 세 가지 결정 중 하나를 내립니다:

- **`ASSIGN_EXISTING`** - 기존 세션에 배정 (컨텍스트가 관련 있을 때)
- **`CREATE_NEW_SESSION`** - 새 세션 생성 (컨텍스트 rot 방지)
- **`ENQUEUE_GLOBAL`** - 전역 큐에 대기 (세션 용량 초과 시)

---

## Getting Started

### Prerequisites

- Python 3.12+
- OpenAI API key (LLM 기반 라우팅에 필요)
- Codex CLI (worker backend로 사용 시)

### Installation

```bash
git clone https://github.com/your-org/leopard-gecko.git
cd leopard-gecko

python3.12 -m venv venv
source venv/bin/activate

pip install -e .
```

### Quick Start

```bash
# 1. 초기화
lg init --worker-backend codex

# 2. 환경변수 설정
export OPENAI_API_KEY="your-key-here"

# 3. 태스크 제출
lg submit "Add pagination to the users API endpoint"

# 4. 상태 확인
lg status

# 5. 백그라운드 워커 실행
lg worker --interval-sec 2.0

# 6. 또는 TUI 실행
lg tui
```

---

## CLI Commands

| Command              | Description                    |
| -------------------- | ------------------------------ |
| `lg init`            | 데이터 디렉토리 및 설정 초기화 |
| `lg submit <prompt>` | 새 태스크 제출 및 라우팅       |
| `lg status`          | 세션/큐 요약 표시              |
| `lg sessions`        | 전체 세션 상세 목록            |
| `lg poll`            | 워커 상태 1회 폴링             |
| `lg worker`          | 백그라운드 폴링 루프 실행      |
| `lg tui`             | 인터랙티브 터미널 UI 실행      |

공통 옵션:

- `--data-dir` : 데이터 디렉토리 지정 (기본: `~/.leopard-gecko`)
- `--worker-backend` : 워커 백엔드 선택 (`NOOP`, `CODEX`)

---

## Configuration

`~/.leopard-gecko/config.json` 에서 설정을 관리합니다.

```jsonc
{
  "max_terminal_num": 4, // 최대 동시 세션 수
  "session_idle_timeout_min": 30, // 세션 idle 타임아웃 (분)
  "queue_policy": {
    "max_queue_per_session": 5, // 세션당 최대 큐 크기
  },
  "router": {
    "backend": "AGENT", // LLM 기반 라우팅
    "agent": {
      "model": "gpt-5.4-mini", // 라우팅용 모델
      "history_limit": 5, // 라우팅 판단 시 참고할 히스토리 수
      "max_turns_per_session": 5, // 세션당 최대 턴 수
    },
  },
  "worker": {
    "backend": "CODEX", // 워커 백엔드
  },
  "worktree": {
    "enabled": false, // Git worktree 격리 (선택)
  },
}
```

---

## How It Works

### Task Lifecycle

```
PENDING  -->  QUEUED_IN_SESSION / QUEUED_GLOBALLY / RUNNING
                                                      |
                                            COMPLETED / FAILED
```

1. 사용자가 프롬프트를 입력합니다
2. Orchestrator가 태스크를 생성하고 짧은 라우팅 메모(task_note)를 만듭니다
3. Context Router가 세션 히스토리를 분석하여 라우팅을 결정합니다
4. 태스크가 세션에 배정되고 워커가 실행합니다
5. **사용자의 원본 프롬프트만** 워커에 전달됩니다 (라우팅 메모는 내부용)

### Session Lifecycle

```
IDLE  --[task assigned]-->  BUSY  --[task done, queue empty]-->  IDLE
                                  --[task done, queue has next]--> BUSY
IDLE  --[timeout]-->  DEAD
```

### Git Worktree (Optional)

세션별 독립된 작업 디렉토리를 제공하여 여러 세션이 동시에 같은 리포지토리를 수정할 때 충돌을 방지합니다.

---

## Supported Workers

| Backend         | Status    | Description                              |
| --------------- | --------- | ---------------------------------------- |
| **Codex**       | Supported | OpenAI Codex CLI subprocess              |
| **Noop**        | Testing   | 즉시 완료 반환 (테스트용)                |
| **Claude Code** | Planned   | `WorkerPort` 프로토콜 구현으로 추가 가능 |

새로운 코딩 에이전트를 추가하려면 `WorkerPort` 프로토콜의 `submit()`과 `poll()` 두 메서드만 구현하면 됩니다.

---

## Design Principles

1. **Prompt Preservation** - 사용자의 원본 프롬프트를 절대 수정하지 않음
2. **Routing-Only Adapter** - 세션 간 라우팅만 담당, 에이전트 품질에 영향 없음
3. **History-Driven Routing** - 세션의 작업 히스토리 기반 라우팅 결정
4. **Atomic Persistence** - 파일 잠금 + 원자적 쓰기로 데이터 무결성 보장
5. **Pluggable Architecture** - Router, Worker, TaskNote 모두 프로토콜 기반

---

## Development

```bash
# 개발 의존성 설치
pip install -e ".[dev]"

# 테스트 실행
pytest

# E2E 테스트 (외부 서비스 호출)
pytest -m e2e

# 린트
ruff check src/ tests/
```

---

## License

TBD
