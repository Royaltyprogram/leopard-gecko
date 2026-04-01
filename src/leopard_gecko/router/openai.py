import json
import os
from typing import Any, Protocol
from urllib import error, request

from leopard_gecko.models.config import AgentRouterConfig
from leopard_gecko.router.policy import RoutingError


class ResponsesTransport(Protocol):
    def create(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_sec: float,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a request to the OpenAI Responses API."""


class UrllibResponsesTransport:
    def create(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_sec: float,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        http_request = request.Request(
            base_url,
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with request.urlopen(http_request, timeout=timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RoutingError(f"OpenAI request failed with HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise RoutingError(f"OpenAI request failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RoutingError("OpenAI request timed out") from exc


class ResponsesClient:
    def __init__(
        self,
        config: AgentRouterConfig,
        *,
        transport: ResponsesTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibResponsesTransport()

    def create_output_text(
        self,
        *,
        system_prompt: str,
        user_input: str,
        text_format: dict[str, Any],
        context: str,
    ) -> str:
        api_key = os.getenv(self.config.api_key_env_var)
        if not api_key:
            raise RoutingError(
                f"Missing OpenAI API key in environment variable {self.config.api_key_env_var}."
            )

        payload = {
            "model": self.config.runtime_model,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            "text": {"format": text_format},
        }
        if self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}

        response = self.transport.create(
            api_key=api_key,
            base_url=self.config.base_url,
            timeout_sec=self.config.timeout_sec,
            payload=payload,
        )
        return extract_output_text(response, context=context)


def extract_output_text(response: dict[str, Any], *, context: str) -> str:
    texts: list[str] = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)

    if texts:
        return "".join(texts)

    raise RoutingError(f"{context} returned no message text: {response}")
