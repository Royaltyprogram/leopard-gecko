import json
from collections.abc import Sequence
from typing import Any

from leopard_gecko.models.config import AgentRouterConfig, AppConfig
from leopard_gecko.models.session import SessionStatus
from leopard_gecko.models.task import Task
from leopard_gecko.router.openai import ResponsesClient, ResponsesTransport
from leopard_gecko.router.policy import ContextRouter, RouteAction, RouteDecision, RoutingError, SessionSnapshot


class AgentRouter(ContextRouter):
    kind = "agent"

    def __init__(
        self,
        config: AgentRouterConfig,
        *,
        transport: ResponsesTransport | None = None,
    ) -> None:
        self.config = config
        self.history_limit = config.history_limit
        self.responses = ResponsesClient(config, transport=transport)

    def decide(
        self,
        *,
        task: Task,
        config: AppConfig,
        sessions: list[SessionSnapshot],
        global_queue_size: int,
    ) -> RouteDecision:
        output_text = self.responses.create_output_text(
            system_prompt=_system_prompt(),
            user_input=_router_input(
                task=task,
                config=config,
                sessions=sessions,
                global_queue_size=global_queue_size,
            ),
            text_format=_route_decision_schema(),
            context="Agent router",
        )

        try:
            raw_decision = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise RoutingError(f"Agent router returned invalid JSON: {output_text}") from exc

        session_id = raw_decision.get("session_id") or None
        try:
            return RouteDecision(
                action=raw_decision["action"],
                session_id=session_id,
                reason=raw_decision["reason"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RoutingError(f"Agent router returned invalid decision payload: {raw_decision}") from exc


def _system_prompt() -> str:
    return (
        "You are a context router for a coding-agent orchestrator. "
        "Choose exactly one routing action for the incoming task. "
        "Keep sessions short and avoid letting a single session grow past its turn budget. "
        "Respect the provided capacity and queue constraints. "
        "Return only the requested structured output."
    )


def _router_input(
    *,
    task: Task,
    config: AppConfig,
    sessions: Sequence[SessionSnapshot],
    global_queue_size: int,
) -> str:
    live_sessions = sum(1 for session in sessions if session.status is not SessionStatus.DEAD)
    payload = {
        "task": {
            "task_id": task.task_id,
            "user_prompt": task.user_prompt,
            "task_note": task.task_note,
        },
        "constraints": {
            "max_terminal_num": config.max_terminal_num,
            "max_queue_per_session": config.queue_policy.max_queue_per_session,
            "max_turns_per_session": config.router.agent.max_turns_per_session,
            "live_session_count": live_sessions,
            "global_queue_size": global_queue_size,
        },
        "sessions": [session.model_dump(mode="json") for session in sessions],
        "routing_rules": [
            "assign_existing requires a non-empty session_id from the sessions list",
            "create_new_session is allowed only when live_session_count is less than max_terminal_num",
            "enqueue_global is for waiting globally when no suitable session should be reused and a new session cannot be started now",
            "prefer keeping related work in the same session, but avoid mixing unrelated work",
            (
                "generally do not let a single session exceed "
                f"{config.router.agent.max_turns_per_session} turns"
            ),
            (
                "if a session already has "
                f"{config.router.agent.max_turns_per_session} turns, do not assign_existing to it; "
                "create_new_session instead when capacity allows, otherwise enqueue_global"
            ),
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _route_decision_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "route_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [action.value for action in RouteAction],
                },
                "session_id": {
                    "type": "string",
                    "description": "Existing session_id when action is assign_existing, otherwise empty string.",
                },
                "reason": {
                    "type": "string",
                },
            },
            "required": ["action", "session_id", "reason"],
            "additionalProperties": False,
        },
    }
