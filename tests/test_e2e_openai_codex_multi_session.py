import os
from pathlib import Path
import shutil
import time

import pytest

from leopard_gecko.models.config import WorkerBackend
from leopard_gecko.models.session import SessionStatus, TaskHistoryStatus
from leopard_gecko.models.task import QueueStatus, RoutingDecision
from leopard_gecko.orchestrator.pipeline import Orchestrator


pytestmark = pytest.mark.e2e


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_CODEX_E2E") != "1",
    reason="Set RUN_OPENAI_CODEX_E2E=1 to execute the real OpenAI+Codex multi-session E2E test.",
)
@pytest.mark.skipif(
    shutil.which("codex") is None,
    reason="codex CLI is not installed.",
)
def test_real_openai_router_and_multiple_codex_sessions(tmp_path, monkeypatch) -> None:
    env_values = _load_env_file(Path.cwd() / ".env")
    api_key = env_values.get("OPENAI_API_KEY")
    model = env_values.get("OPENAI_MODEL")

    if not api_key or not model:
        pytest.skip("Missing OPENAI_API_KEY or OPENAI_MODEL in .env")

    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    monkeypatch.setenv("OPENAI_MODEL", model)

    data_dir = tmp_path / ".leopard-gecko"
    orchestrator = Orchestrator(
        data_dir=str(data_dir),
        worker_backend=WorkerBackend.CODEX,
    )

    config = orchestrator.init_storage()
    config = config.model_copy(
        update={
            "max_terminal_num": 2,
            "worker": config.worker.model_copy(update={"backend": WorkerBackend.CODEX}),
        }
    )
    orchestrator.config_repo.save(config)

    prompts = [
        "완전히 독립적인 작업이다. 새 세션을 시작해서 정확히 FIRST만 답하고 어떤 파일도 수정하지 말아줘.",
        "이전 작업과 완전히 unrelated한 다른 작업이다. 별도의 새 세션을 시작해서 정확히 SECOND만 답하고 어떤 파일도 수정하지 말아줘.",
    ]

    first = orchestrator.submit(prompts[0])
    second = orchestrator.submit(prompts[1])

    assert first.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert first.queue_status is QueueStatus.RUNNING
    assert second.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert second.queue_status is QueueStatus.RUNNING
    assert first.assigned_session_id is not None
    assert second.assigned_session_id is not None
    assert first.assigned_session_id != second.assigned_session_id

    after_submit = orchestrator.load_sessions()
    assert len(after_submit.sessions) == 2
    assert after_submit.global_queue == []
    assert {session.status for session in after_submit.sessions} == {SessionStatus.BUSY}
    assert {session.current_task_id for session in after_submit.sessions} == {first.task_id, second.task_id}

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        orchestrator.poll_runs()
        first_task = orchestrator.task_repo.load(first.task_id)
        second_task = orchestrator.task_repo.load(second.task_id)
        if (
            first_task.queue_status is QueueStatus.COMPLETED
            and second_task.queue_status is QueueStatus.COMPLETED
        ):
            break
        time.sleep(0.7)

    first_task = orchestrator.task_repo.load(first.task_id)
    second_task = orchestrator.task_repo.load(second.task_id)
    assert first_task.queue_status is QueueStatus.COMPLETED
    assert second_task.queue_status is QueueStatus.COMPLETED
    assert first_task.routing.assigned_session_id == first.assigned_session_id
    assert second_task.routing.assigned_session_id == second.assigned_session_id

    final_state = orchestrator.load_sessions()
    assert len(final_state.sessions) == 2
    assert final_state.global_queue == []

    sessions_by_id = {session.session_id: session for session in final_state.sessions}
    first_session = sessions_by_id[first.assigned_session_id]
    second_session = sessions_by_id[second.assigned_session_id]

    for session, task_id, expected_text in (
        (first_session, first.task_id, "FIRST"),
        (second_session, second.task_id, "SECOND"),
    ):
        assert session.status is SessionStatus.IDLE
        assert session.current_task_id is None
        assert session.active_run_id is None
        assert session.active_pid is None
        assert session.worker_context_id
        assert len(session.task_history) == 1
        assert session.task_history[0].task_id == task_id
        assert session.task_history[0].status is TaskHistoryStatus.COMPLETED
        assert session.task_history[0].summary is not None
        assert expected_text in session.task_history[0].summary

    assert first_session.worker_context_id != second_session.worker_context_id

    event_types = [event.event_type for event in orchestrator.tasks_log.read_all()]
    assert event_types.count("task_created") == 2
    assert event_types.count("task_routed") == 2
    assert event_types.count("task_dispatched") == 2
    assert event_types.count("task_completed") == 2
    assert "task_failed" not in event_types
