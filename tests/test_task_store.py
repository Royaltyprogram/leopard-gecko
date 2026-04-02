from leopard_gecko.models.task import QueueStatus, Task
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


def test_task_repository_caches_list_queries(tmp_path, monkeypatch) -> None:
    paths = resolve_data_paths(cwd=tmp_path)
    repo = TaskRepository(paths)
    repo.initialize()
    repo.save(
        Task(
            task_id="task_1",
            user_prompt="still running task",
            task_note="still running task",
            queue_status=QueueStatus.RUNNING,
        )
    )
    repo.save(
        Task(
            task_id="task_2",
            user_prompt="completed task",
            task_note="completed task",
            queue_status=QueueStatus.COMPLETED,
        )
    )

    load_calls = 0
    original_loader = repo._load_task_from_path

    def counting_loader(path):
        nonlocal load_calls
        load_calls += 1
        return original_loader(path)

    monkeypatch.setattr(repo, "_load_task_from_path", counting_loader)

    assert [task.task_id for task in repo.list_all()] == ["task_1", "task_2"]
    assert load_calls == 2
    assert [task.task_id for task in repo.list_by_status(QueueStatus.RUNNING)] == ["task_1"]
    assert load_calls == 2
    assert [task.task_id for task in repo.list_all()] == ["task_1", "task_2"]
    assert load_calls == 2
