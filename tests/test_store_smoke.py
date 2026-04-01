from leopard_gecko.models.session import Session, SessionsState
from leopard_gecko.models.task import TaskEvent
from leopard_gecko.store.config_repo import ConfigRepository
from leopard_gecko.store.paths import resolve_data_paths
from leopard_gecko.store.sessions_repo import SessionsRepository
from leopard_gecko.store.tasks_log import TasksLog


def test_store_round_trip(tmp_path) -> None:
    paths = resolve_data_paths(cwd=tmp_path)

    config_repo = ConfigRepository(paths)
    config = config_repo.initialize()
    assert paths.config_path.exists()
    assert config.data_dir == str(paths.root_dir)

    sessions_repo = SessionsRepository(paths)
    sessions_repo.save(SessionsState(sessions=[Session(session_id="sess_1")], global_queue=["task_1"]))
    loaded_state = sessions_repo.load()
    assert loaded_state.sessions[0].session_id == "sess_1"
    assert loaded_state.global_queue == ["task_1"]

    tasks_log = TasksLog(paths)
    tasks_log.initialize()
    tasks_log.append(TaskEvent(event_type="task_created", task_id="task_1", payload={"ok": True}))
    events = tasks_log.read_all()
    assert len(events) == 1
    assert events[0].payload == {"ok": True}

