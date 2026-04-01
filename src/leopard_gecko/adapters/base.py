from typing import Protocol


class WorkerPort(Protocol):
    def submit(self, session_id: str, user_prompt: str) -> None:
        """Submit a raw user prompt to an assigned worker session."""

