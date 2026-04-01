import json
from collections.abc import Callable
from typing import TypeVar

from filelock import FileLock
from pydantic import BaseModel

from leopard_gecko.models.session import SessionsState
from leopard_gecko.store.paths import DataPaths, ensure_data_dir

T = TypeVar("T")


class SessionsSnapshot(BaseModel):
    state: SessionsState
    modified_time_ns: int | None = None
    size: int | None = None


class SessionsRepository:
    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths
        self._lock = FileLock(str(paths.sessions_path) + ".lock")

    def load(self) -> SessionsState:
        if not self._paths.sessions_path.exists():
            return SessionsState()
        raw = json.loads(self._paths.sessions_path.read_text(encoding="utf-8"))
        return SessionsState.model_validate(raw)

    def load_snapshot(self) -> SessionsSnapshot:
        ensure_data_dir(self._paths)
        with self._lock:
            state = self._load_unlocked()
            modified_time_ns, size = self._current_file_stamp()
            return SessionsSnapshot(
                state=state,
                modified_time_ns=modified_time_ns,
                size=size,
            )

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
            state = self._load_unlocked()
            result = mutator(state)
            self._atomic_write(self._paths.sessions_path, state.model_dump(mode="json"))
            return result

    def update_from_snapshot(
        self,
        snapshot: SessionsSnapshot,
        mutator: Callable[[SessionsState], T],
    ) -> T:
        ensure_data_dir(self._paths)
        with self._lock:
            if self._matches_snapshot(snapshot):
                state = snapshot.state
            else:
                state = self._load_unlocked()
            result = mutator(state)
            self._atomic_write(self._paths.sessions_path, state.model_dump(mode="json"))
            return result

    def _load_unlocked(self) -> SessionsState:
        if not self._paths.sessions_path.exists():
            return SessionsState()
        raw = json.loads(self._paths.sessions_path.read_text(encoding="utf-8"))
        return SessionsState.model_validate(raw)

    def _current_file_stamp(self) -> tuple[int | None, int | None]:
        if not self._paths.sessions_path.exists():
            return None, None
        stat = self._paths.sessions_path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _matches_snapshot(self, snapshot: SessionsSnapshot) -> bool:
        modified_time_ns, size = self._current_file_stamp()
        return (
            modified_time_ns == snapshot.modified_time_ns
            and size == snapshot.size
        )

    @staticmethod
    def _atomic_write(path, payload: dict) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
