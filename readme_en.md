<p align="center">
  <img src="assets/logo.PNG" alt="Leopard Gecko" width="400" />
</p>

<h1 align="center">Leopard Gecko</h1>

<p align="center">
  <strong>The First Context Engineer That Replaces You.</strong><br/>
  A context orchestrator that automatically routes and manages coding agent sessions.
</p>

<p align="center">
  <video src="assets/laopard-demo.mp4" autoplay loop muted playsinline width="800"></video>
</p>

---

## Why Leopard Gecko?

It's now widely understood that **context** is the biggest factor affecting coding agent performance. Many try to solve this with so-called memory systems that "strip unnecessary content or summarize previous conversations." But is this really the best approach?

We looked at the tangled knot of this problem not from the model's perspective, but from the **human's**. In reality, humans ask questions unrelated to previous context within a single session, and fail to start a new session when conversations grow too long. We call these human tasks **Context Engineering**.

The core bottleneck of Context Engineering is the **human's context window**. When running multiple sessions, you lose track of which task ran in which session. While focusing on conversation content, you forget how full the context window has gotten. The model starts producing increasingly poor answers.

**That's why we built Leopard Gecko.**

Leopard Gecko is the first Context Engineer that replaces the human. It manages coding agent sessions running in the background. When a human enters a prompt, it routes it to the appropriate working session, or starts a new one if context rot or saturation is expected.

### How is this different from Sub Agents?

The most important difference: **Leopard Gecko has zero impact on your coding agent's quality.**

The biggest problem with existing Sub Agents is the capability bottleneck of the manager model. If the manager model gives poor instructions, Sub Agents simply execute them. If the user's intent is misinterpreted, a massive amount of output heads in a completely wrong direction.

In contrast, Leopard Gecko only serves as an **adapter** between multiple coding agent sessions and the human -- it never adds to or removes from your query content. Therefore, it relies entirely on the user's prompt quality and the intrinsic performance of agent systems like Codex or Claude Code.

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

| Component          | Description                                              |
| ------------------ | -------------------------------------------------------- |
| **Orchestrator**   | Task submission, worker polling, session lifecycle mgmt  |
| **Context Router** | LLM-based routing -- assigns tasks to the best session   |
| **Worker Adapter** | Abstraction layer for coding agents (e.g., Codex)        |
| **Store**          | File-based atomic persistence (sessions.json, tasks.jsonl) |
| **TUI**            | Interactive terminal UI built with Textual               |

### Routing Decisions

The Router makes one of three decisions for each task:

- **`ASSIGN_EXISTING`** -- Assign to an existing session (when context is relevant)
- **`CREATE_NEW_SESSION`** -- Create a new session (to prevent context rot)
- **`ENQUEUE_GLOBAL`** -- Enqueue globally (when session capacity is exceeded)

---

## Getting Started

### Prerequisites

- Python 3.12+
- OpenAI API key (required for LLM-based routing)
- Codex CLI (when used as worker backend)

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
# 1. Initialize
lg init --worker-backend codex

# 2. Set environment variables
export OPENAI_API_KEY="your-key-here"

# 3. Submit a task
lg submit "Add pagination to the users API endpoint"

# 4. Check status
lg status

# 5. Run background worker
lg worker --interval-sec 2.0

# 6. Or launch the TUI
lg tui
```

---

## CLI Commands

| Command              | Description                          |
| -------------------- | ------------------------------------ |
| `lg init`            | Initialize data directory and config |
| `lg submit <prompt>` | Submit a new task and route it       |
| `lg status`          | Display session/queue summary        |
| `lg sessions`        | List all sessions in detail          |
| `lg poll`            | Poll worker status once              |
| `lg worker`          | Run background polling loop          |
| `lg tui`             | Launch interactive terminal UI       |

Common options:

- `--data-dir` : Specify data directory (default: `~/.leopard-gecko`)
- `--worker-backend` : Select worker backend (`NOOP`, `CODEX`)

---

## Configuration

Settings are managed in `~/.leopard-gecko/config.json`.

```jsonc
{
  "max_terminal_num": 4, // Maximum concurrent sessions
  "session_idle_timeout_min": 30, // Session idle timeout (minutes)
  "queue_policy": {
    "max_queue_per_session": 5, // Max queue size per session
  },
  "router": {
    "backend": "AGENT", // LLM-based routing
    "agent": {
      "model": "gpt-5.4-mini", // Model for routing
      "history_limit": 5, // History entries for routing decisions
      "max_turns_per_session": 5, // Max turns per session
    },
  },
  "worker": {
    "backend": "CODEX", // Worker backend
  },
  "worktree": {
    "enabled": false, // Git worktree isolation (optional)
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

1. The user enters a prompt
2. The Orchestrator creates a task and generates a short routing memo (task_note)
3. The Context Router analyzes session history to make a routing decision
4. The task is assigned to a session and executed by a worker
5. **Only the user's original prompt** is passed to the worker (routing memos are internal)

### Session Lifecycle

```
IDLE  --[task assigned]-->  BUSY  --[task done, queue empty]-->  IDLE
                                  --[task done, queue has next]--> BUSY
IDLE  --[timeout]-->  DEAD
```

### Git Worktree (Optional)

Provides an independent working directory per session to prevent conflicts when multiple sessions modify the same repository simultaneously.

---

## Supported Workers

| Backend         | Status    | Description                                    |
| --------------- | --------- | ---------------------------------------------- |
| **Codex**       | Supported | OpenAI Codex CLI subprocess                    |
| **Noop**        | Testing   | Returns completion immediately (for testing)   |
| **Claude Code** | Planned   | Can be added by implementing `WorkerPort`      |

To add a new coding agent, simply implement the `submit()` and `poll()` methods of the `WorkerPort` protocol.

---

## Design Principles

1. **Prompt Preservation** -- Never modifies the user's original prompt
2. **Routing-Only Adapter** -- Only handles routing between sessions, no impact on agent quality
3. **History-Driven Routing** -- Routing decisions based on session task history
4. **Atomic Persistence** -- Data integrity via file locking + atomic writes
5. **Pluggable Architecture** -- Router, Worker, and TaskNote are all protocol-based

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# E2E tests (calls external services)
pytest -m e2e

# Lint
ruff check src/ tests/
```

---

## License

TBD
