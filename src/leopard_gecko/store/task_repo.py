import json
from pathlib import Path
from threading import Lock

from filelock import FileLock

from leopard_gecko.models.task import QueueStatus, Task
from leopard_gecko.store.paths import DataPaths, ensure_data_dir


class TaskRepository:
    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths
        self._cache_lock = Lock()
        self._cache_stamp: tuple[tuple[str, int, int], ...] | None = None
        self._cached_tasks: list[Task] | None = None
        self._cached_tasks_by_status: dict[QueueStatus, list[Task]] = {}

    def initialize(self) -> None:
        ensure_data_dir(self._paths)
        self._paths.tasks_dir.mkdir(parents=True, exist_ok=True)

    def save(self, task: Task) -> None:
        self.initialize()
        payload = task.model_dump(mode="json")
        path = self._task_path(task.task_id)
        with FileLock(str(path) + ".lock"):
            self._atomic_write(path, payload)
        self._invalidate_cache()

    def load(self, task_id: str) -> Task:
        path = self._task_path(task_id)
        if not path.exists():
            raise ValueError(f"Unknown task_id: {task_id}")
        return self._load_task_from_path(path)

    def exists(self, task_id: str) -> bool:
        return self._task_path(task_id).exists()

    def list_all(self) -> list[Task]:
        tasks, _ = self._load_cached_task_lists()
        return [task.model_copy(deep=True) for task in tasks]

    def list_by_status(self, queue_status: QueueStatus) -> list[Task]:
        _, tasks_by_status = self._load_cached_task_lists()
        return [
            task.model_copy(deep=True)
            for task in tasks_by_status.get(queue_status, [])
        ]

    def _task_path(self, task_id: str):
        return self._paths.tasks_dir / f"{task_id}.json"

    def _load_cached_task_lists(
        self,
    ) -> tuple[list[Task], dict[QueueStatus, list[Task]]]:
        self.initialize()
        paths, stamp = self._scan_task_paths()

        with self._cache_lock:
            if stamp == self._cache_stamp and self._cached_tasks is not None:
                return self._cached_tasks, self._cached_tasks_by_status

        tasks = [self._load_task_from_path(path) for path in paths]
        tasks_by_status = {status: [] for status in QueueStatus}
        for task in tasks:
            tasks_by_status[task.queue_status].append(task)

        with self._cache_lock:
            self._cache_stamp = stamp
            self._cached_tasks = tasks
            self._cached_tasks_by_status = tasks_by_status
            return self._cached_tasks, self._cached_tasks_by_status

    def _scan_task_paths(self) -> tuple[list[Path], tuple[tuple[str, int, int], ...]]:
        paths = sorted(self._paths.tasks_dir.glob("*.json"))
        stamp: list[tuple[str, int, int]] = []
        for path in paths:
            stat = path.stat()
            stamp.append((path.name, stat.st_mtime_ns, stat.st_size))
        return paths, tuple(stamp)

    def _load_task_from_path(self, path: Path) -> Task:
        with FileLock(str(path) + ".lock"):
            return Task.model_validate_json(path.read_text(encoding="utf-8"))

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache_stamp = None
            self._cached_tasks = None
            self._cached_tasks_by_status = {}

    @staticmethod
    def _atomic_write(path, payload: dict) -> None:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)
