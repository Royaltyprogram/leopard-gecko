import json

import pytest

from leopard_gecko.models.config import AgentRouterConfig, AppConfig
from leopard_gecko.models.session import Session, SessionStatus, TaskHistoryEntry, TaskHistoryStatus
from leopard_gecko.models.task import Task
from leopard_gecko.router.agent import AgentRouter
from leopard_gecko.router.policy import RouteAction, RoutingError, build_session_snapshots
from leopard_gecko.router.task_notes import AgentTaskNoteGenerator, TemplateTaskNoteGenerator


class FakeResponsesTransport:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {}
        self.calls: list[dict] = []

    def create(self, *, api_key: str, base_url: str, timeout_sec: float, payload: dict) -> dict:
        self.calls.append(
            {
                "api_key": api_key,
                "base_url": base_url,
                "timeout_sec": timeout_sec,
                "payload": payload,
            }
        )
        return self.response


def test_template_task_note_generator_trims_prompt() -> None:
    generator = TemplateTaskNoteGenerator()

    note = generator.make_note("  add pagination to admin users page  ")

    assert note == "add pagination to admin users page"


def test_build_session_snapshots_limits_recent_history() -> None:
    session = Session(
        session_id="sess_1",
        status=SessionStatus.IDLE,
        queue=["task_3"],
        task_history=[
            TaskHistoryEntry(
                task_id="task_1",
                user_prompt="first task",
                task_note="first note",
                status=TaskHistoryStatus.COMPLETED,
                summary="older summary",
            ),
            TaskHistoryEntry(
                task_id="task_2",
                user_prompt="second task",
                task_note="second note",
                status=TaskHistoryStatus.COMPLETED,
                summary="latest summary",
            ),
        ],
    )

    snapshots = build_session_snapshots([session], history_limit=1)

    assert len(snapshots) == 1
    assert snapshots[0].queue_size == 1
    assert [entry.task_id for entry in snapshots[0].recent_history] == ["task_2"]
    assert snapshots[0].recent_summary == "latest summary"


def test_agent_task_note_generator_calls_openai_responses(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    transport = FakeResponsesTransport(
        response={
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "task_note": "Appears to be an extension of the admin/users list feature.",
                                }
                            ),
                        }
                    ],
                }
            ]
        }
    )
    generator = AgentTaskNoteGenerator(
        AgentRouterConfig(model="gpt-5-mini"),
        transport=transport,
    )

    note = generator.make_note("add pagination to admin user list")

    assert note == "Appears to be an extension of the admin/users list feature."
    assert transport.calls[0]["api_key"] == "test-key"
    payload = transport.calls[0]["payload"]
    assert payload["model"] == "gpt-5"
    assert payload["text"]["format"]["name"] == "task_note"
    assert payload["reasoning"] == {"effort": "low"}


def test_agent_task_note_generator_raises_when_api_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    generator = AgentTaskNoteGenerator()

    with pytest.raises(RoutingError, match="Missing OpenAI API key"):
        generator.make_note("  add pagination to admin users page  ")


def test_agent_router_calls_openai_responses_with_structured_output(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5")
    transport = FakeResponsesTransport(
        response={
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "action": "assign_existing",
                                    "session_id": "sess_auth",
                                    "reason": "Same auth flow.",
                                }
                            ),
                        }
                    ],
                }
            ]
        }
    )
    router = AgentRouter(AgentRouterConfig(model="gpt-5-mini", history_limit=3), transport=transport)
    config = AppConfig.default()
    task = Task(
        task_id="task_new",
        user_prompt="continue with 401/403 error handling as well",
        task_note="auth error handling",
    )
    session = Session(
        session_id="sess_auth",
        status=SessionStatus.BUSY,
        current_task_id="task_running",
        task_history=[
            TaskHistoryEntry(
                task_id="task_auth",
                user_prompt="clean up auth error handling",
                task_note="auth error handling",
                status=TaskHistoryStatus.RUNNING,
            )
        ],
    )

    decision = router.decide(
        task=task,
        config=config,
        sessions=build_session_snapshots([session], history_limit=3),
        global_queue_size=0,
    )

    assert decision.action is RouteAction.ASSIGN_EXISTING
    assert decision.session_id == "sess_auth"
    assert transport.calls[0]["api_key"] == "test-key"
    payload = transport.calls[0]["payload"]
    assert payload["model"] == "gpt-5"
    assert payload["text"]["format"]["name"] == "route_decision"
    assert payload["reasoning"] == {"effort": "low"}


def test_agent_router_raises_when_api_key_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    router = AgentRouter(AgentRouterConfig(), transport=FakeResponsesTransport())

    with pytest.raises(RoutingError, match="Missing OpenAI API key"):
        router.decide(
            task=Task(task_id="task_1", user_prompt="test", task_note="test"),
            config=AppConfig.default(),
            sessions=[],
            global_queue_size=0,
        )


def test_agent_router_raises_on_invalid_response_payload(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    router = AgentRouter(
        AgentRouterConfig(),
        transport=FakeResponsesTransport(
            response={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"action":"create_new_session"}'}],
                    }
                ]
            }
        ),
    )

    with pytest.raises(RoutingError, match="invalid decision payload"):
        router.decide(
            task=Task(task_id="task_1", user_prompt="test", task_note="test"),
            config=AppConfig.default(),
            sessions=[],
            global_queue_size=0,
        )
