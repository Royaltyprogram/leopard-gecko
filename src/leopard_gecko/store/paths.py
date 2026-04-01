from dataclasses import dataclass
from pathlib import Path


CONFIG_FILENAME = "config.json"
SESSIONS_FILENAME = "sessions.json"
TASKS_LOG_FILENAME = "tasks.jsonl"
TASKS_DIRNAME = "tasks"
WORKER_RUNS_DIRNAME = "worker_runs"


@dataclass(frozen=True)
class DataPaths:
    root_dir: Path
    config_path: Path
    sessions_path: Path
    tasks_log_path: Path
    tasks_dir: Path
    worker_runs_dir: Path


def resolve_data_paths(data_dir: str | None = None, cwd: Path | None = None) -> DataPaths:
    base_dir = Path(data_dir).expanduser() if data_dir else (cwd or Path.cwd()) / ".leopard-gecko"
    return DataPaths(
        root_dir=base_dir,
        config_path=base_dir / CONFIG_FILENAME,
        sessions_path=base_dir / SESSIONS_FILENAME,
        tasks_log_path=base_dir / TASKS_LOG_FILENAME,
        tasks_dir=base_dir / TASKS_DIRNAME,
        worker_runs_dir=base_dir / WORKER_RUNS_DIRNAME,
    )


def ensure_data_dir(paths: DataPaths) -> None:
    paths.root_dir.mkdir(parents=True, exist_ok=True)
