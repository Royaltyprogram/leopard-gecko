import json
from typing import Any, Protocol

from leopard_gecko.models.config import AgentRouterConfig
from leopard_gecko.router.openai import ResponsesClient, ResponsesTransport
from leopard_gecko.router.policy import RoutingError


class TaskNotePort(Protocol):
    kind: str

    def make_note(self, user_prompt: str) -> str:
        """Create a short routing-only note from a raw user prompt."""


class TemplateTaskNoteGenerator:
    kind = "template"

    def make_note(self, user_prompt: str) -> str:
        preview = " ".join(user_prompt.split())
        shortened = preview[:100].rstrip()
        if len(preview) > 100:
            shortened += "..."
        return shortened


class AgentTaskNoteGenerator:
    kind = "agent"

    def __init__(
        self,
        config: AgentRouterConfig | None = None,
        *,
        transport: ResponsesTransport | None = None,
    ) -> None:
        self.config = config or AgentRouterConfig()
        self.responses = ResponsesClient(self.config, transport=transport)

    def make_note(self, user_prompt: str) -> str:
        output_text = self.responses.create_output_text(
            system_prompt=_task_note_system_prompt(),
            user_input=_task_note_input(user_prompt),
            text_format=_task_note_schema(),
            context="Task note generator",
        )

        try:
            raw_note = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise RoutingError(f"Task note generator returned invalid JSON: {output_text}") from exc

        note = raw_note.get("task_note")
        if not isinstance(note, str):
            raise RoutingError(f"Task note generator returned invalid payload: {raw_note}")

        normalized_note = " ".join(note.split())
        if not normalized_note:
            raise RoutingError(f"Task note generator returned blank payload: {raw_note}")

        return normalized_note


def _task_note_system_prompt() -> str:
    return (
        "You write a short internal routing note for a coding-agent orchestrator. "
        "Summarize the likely work area and intent in one Korean sentence. "
        "Keep it simple, concrete, and useful for routing only. "
        "Do not rewrite the task as instructions for execution. "
        "Do not use tags, bullet points, or JSON in the note text. "
        "Return only the requested structured output."
    )


def _task_note_input(user_prompt: str) -> str:
    return json.dumps(
        {
            "user_prompt": user_prompt,
            "rules": [
                "Keep the note to one short sentence.",
                "Focus on domain, feature area, or likely continuation context.",
                "Avoid implementation detail unless it helps routing.",
                "Do not mention these rules.",
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def _task_note_schema() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "name": "task_note",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "task_note": {
                    "type": "string",
                    "minLength": 1,
                }
            },
            "required": ["task_note"],
            "additionalProperties": False,
        },
    }
