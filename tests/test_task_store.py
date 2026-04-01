from leopard_gecko.models.task import Task
from leopard_gecko.store.paths import resolve_data_paths
from leopard_gecko.store.task_repo import TaskRepository


def test_task_repository_round_trip(tmp_path) -> None:
    paths = resolve_data_paths(cwd=tmp_path)
    repo = TaskRepository(paths)
    task = Task(
        task_id="task_1",
        user_prompt="add admin users pagination",
        task_note="admin users domain",
    )

    repo.initialize()
    repo.save(task)

    assert repo.exists("task_1") is True
    loaded = repo.load("task_1")
    assert loaded.task_id == "task_1"
    assert loaded.user_prompt == "add admin users pagination"
    assert loaded.task_note == "admin users domain"
