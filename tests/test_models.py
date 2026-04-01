from leopard_gecko.models.config import AppConfig
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
    assert config.data_dir == ".leopard-gecko"


def test_session_state_defaults() -> None:
    session = Session(session_id="sess_1")
    state = SessionsState(sessions=[session])

    assert state.global_queue == []
    assert session.queue == []
    assert session.current_task_id is None

