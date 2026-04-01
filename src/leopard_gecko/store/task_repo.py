import json

from filelock import FileLock

from leopard_gecko.models.task import Task
from leopard_gecko.store.paths import DataPaths, ensure_data_dir


class TaskRepository:
    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths

    def initialize(self) -> None:
        ensure_data_dir(self._paths)
        self._paths.tasks_dir.mkdir(parents=True, exist_ok=True)

    def save(self, task: Task) -> None:
        self.initialize()
        payload = task.model_dump(mode="json")
        path = self._task_path(task.task_id)
        with FileLock(str(path) + ".lock"):
            self._atomic_write(path, payload)

    def load(self, task_id: str) -> Task:
        path = self._task_path(task_id)
        if not path.exists():
            raise ValueError(f"Unknown task_id: {task_id}")
        with FileLock(str(path) + ".lock"):
            return Task.model_validate_json(path.read_text(encoding="utf-8"))

    def exists(self, task_id: str) -> bool:
        return self._task_path(task_id).exists()

    def _task_path(self, task_id: str):
        return self._paths.tasks_dir / f"{task_id}.json"

    @staticmethod
    def _atomic_write(path, payload: dict) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
