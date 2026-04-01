import json
from collections.abc import Callable
from typing import TypeVar

from filelock import FileLock

from leopard_gecko.models.session import SessionsState
from leopard_gecko.store.paths import DataPaths, ensure_data_dir

T = TypeVar("T")


class SessionsRepository:
    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths
        self._lock = FileLock(str(paths.sessions_path) + ".lock")

    def load(self) -> SessionsState:
        if not self._paths.sessions_path.exists():
            return SessionsState()
        raw = json.loads(self._paths.sessions_path.read_text(encoding="utf-8"))
        return SessionsState.model_validate(raw)

    def save(self, state: SessionsState) -> None:
        ensure_data_dir(self._paths)
        payload = state.model_dump(mode="json")
        with self._lock:
            self._atomic_write(self._paths.sessions_path, payload)

    def initialize(self) -> SessionsState:
        state = self.load()
        self.save(state)
        return state

    def update(self, mutator: Callable[[SessionsState], T]) -> T:
        ensure_data_dir(self._paths)
        with self._lock:
            if self._paths.sessions_path.exists():
                raw = json.loads(self._paths.sessions_path.read_text(encoding="utf-8"))
                state = SessionsState.model_validate(raw)
            else:
                state = SessionsState()
            result = mutator(state)
            self._atomic_write(self._paths.sessions_path, state.model_dump(mode="json"))
            return result

    @staticmethod
    def _atomic_write(path, payload: dict) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
