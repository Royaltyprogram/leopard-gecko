from leopard_gecko.models.config import AppConfig, RouterBackend, WorkerBackend
from leopard_gecko.models.session import Session, SessionsState
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task


def test_task_defaults_and_trimming() -> None:
    task = Task(
        task_id="task_1",
        user_prompt="  add pagination to admin users  ",
        task_note="  admin/users domain  ",
    )

    assert task.user_prompt == "add pagination to admin users"
    assert task.task_note == "admin/users domain"
    assert task.queue_status is QueueStatus.PENDING
    assert task.routing.decision is RoutingDecision.PENDING
    assert task.created_at.tzinfo is not None


def test_config_defaults() -> None:
    config = AppConfig.default(data_dir=".leopard-gecko")

    assert config.max_terminal_num == 4
    assert config.queue_policy.max_queue_per_session == 5
    assert config.router.backend is RouterBackend.AGENT
    assert config.router.agent.model == "gpt-5-mini"
    assert config.router.agent.api_key_env_var == "OPENAI_API_KEY"
    assert config.worker.backend is WorkerBackend.NOOP
    assert config.worktree.enabled is False
    assert config.worktree.branch_prefix == "lg"
    assert config.data_dir == ".leopard-gecko"


def test_agent_router_runtime_model_uses_openai_model_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")

    config = AppConfig.default(data_dir=".leopard-gecko")

    assert config.router.agent.model == "gpt-5-mini"
    assert config.router.agent.runtime_model == "gpt-5"


def test_session_state_defaults() -> None:
    session = Session(session_id="sess_1")
    state = SessionsState(sessions=[session])

    assert state.global_queue == []
    assert session.queue == []
    assert session.current_task_id is None
    assert session.worker_backend is None
    assert session.worker_context_id is None
    assert session.worktree_path is None
    assert session.worktree_branch is None
    assert session.worktree_base_ref is None
    assert session.active_run_id is None
    assert session.active_pid is None
    assert session.active_run_started_at is None
    assert session.last_run_output_path is None


def test_session_runtime_fields_round_trip() -> None:
    session = Session(
        session_id="sess_1",
        worker_backend="codex",
        worker_context_id="thread_123",
        worktree_path="/tmp/worktrees/sess_1",
        worktree_branch="lg/sess_1",
        worktree_base_ref="main",
        active_run_id="run_123",
        active_pid=4321,
        last_run_output_path="/tmp/run.jsonl",
    )

    restored = Session.model_validate(session.model_dump(mode="json"))

    assert restored.worker_backend == "codex"
    assert restored.worker_context_id == "thread_123"
    assert restored.worktree_path == "/tmp/worktrees/sess_1"
    assert restored.worktree_branch == "lg/sess_1"
    assert restored.worktree_base_ref == "main"
    assert restored.active_run_id == "run_123"
    assert restored.active_pid == 4321
    assert restored.last_run_output_path == "/tmp/run.jsonl"
    assert restored.created_at.tzinfo is not None
