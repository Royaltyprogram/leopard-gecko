import json

from filelock import FileLock

from leopard_gecko.models.task import TaskEvent
from leopard_gecko.store.paths import DataPaths, ensure_data_dir


class TasksLog:
    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths
        self._lock = FileLock(str(paths.tasks_log_path) + ".lock")

    def initialize(self) -> None:
        ensure_data_dir(self._paths)
        self._paths.tasks_log_path.touch(exist_ok=True)

    def append(self, event: TaskEvent) -> None:
        ensure_data_dir(self._paths)
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            with self._paths.tasks_log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_all(self) -> list[TaskEvent]:
        if not self._paths.tasks_log_path.exists():
            return []
        events: list[TaskEvent] = []
        with self._paths.tasks_log_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                events.append(TaskEvent.model_validate_json(stripped))
        return events

