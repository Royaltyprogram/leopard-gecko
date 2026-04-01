import os
from pathlib import Path

import pytest

from leopard_gecko.adapters.noop import NoopWorkerAdapter
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
    os.getenv("RUN_E2E") != "1",
    reason="Set RUN_E2E=1 to execute the real OpenAI-backed E2E test.",
)
def test_submit_routes_with_real_agent_router_using_dotenv(tmp_path, monkeypatch) -> None:
    env_values = _load_env_file(Path.cwd() / ".env")
    api_key = env_values.get("OPENAI_API_KEY")
    model = env_values.get("OPENAI_MODEL")

    if not api_key or not model:
        pytest.skip("Missing OPENAI_API_KEY or OPENAI_MODEL in .env")

    monkeypatch.setenv("OPENAI_API_KEY", api_key)
    monkeypatch.setenv("OPENAI_MODEL", model)

    worker = NoopWorkerAdapter()
    data_dir = tmp_path / ".leopard-gecko"
    orchestrator = Orchestrator(data_dir=str(data_dir), worker=worker)

    result = orchestrator.submit("관리자 유저 목록 pagination 작업용 새 세션을 시작해줘")
    sessions_state = orchestrator.load_sessions()

    assert result.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert result.queue_status is QueueStatus.RUNNING
    assert result.assigned_session_id is not None
    assert len(worker.submissions) == 1
    assert worker.submissions[0].task_id == result.task_id
    assert len(sessions_state.sessions) == 1
    assert sessions_state.sessions[0].session_id == result.assigned_session_id
