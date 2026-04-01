from leopard_gecko.adapters.base import WorkerPort


class NoopWorkerAdapter(WorkerPort):
    def __init__(self) -> None:
        self.submissions: list[tuple[str, str]] = []

    def submit(self, session_id: str, user_prompt: str) -> None:
        self.submissions.append((session_id, user_prompt))

