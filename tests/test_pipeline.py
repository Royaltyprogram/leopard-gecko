from leopard_gecko.adapters.noop import NoopWorkerAdapter
from leopard_gecko.models.task import QueueStatus, RoutingDecision
from leopard_gecko.orchestrator.pipeline import Orchestrator


def test_submit_creates_then_reuses_related_session(tmp_path) -> None:
    worker = NoopWorkerAdapter()
    orchestrator = Orchestrator(data_dir=str(tmp_path / ".leopard-gecko"), worker=worker)

    first = orchestrator.submit("admin users pagination 추가해줘")
    second = orchestrator.submit("admin users pagination 버튼 스타일도 맞춰줘")

    assert first.routing_decision is RoutingDecision.CREATED_NEW_SESSION
    assert first.queue_status is QueueStatus.RUNNING
    assert first.assigned_session_id is not None

    assert second.routing_decision is RoutingDecision.ASSIGNED_EXISTING
    assert second.queue_status is QueueStatus.QUEUED_IN_SESSION
    assert second.assigned_session_id == first.assigned_session_id

    sessions_state = orchestrator.load_sessions()
    assert len(sessions_state.sessions) == 1
    assert sessions_state.sessions[0].queue == [second.task_id]
    assert worker.submissions == [(first.assigned_session_id, "admin users pagination 추가해줘")]
