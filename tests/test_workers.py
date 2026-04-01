import json

from leopard_gecko.adapters.codex import CodexAdapter
from leopard_gecko.adapters.factory import build_worker
from leopard_gecko.adapters.noop import NoopWorkerAdapter
from leopard_gecko.models.config import AppConfig, WorkerBackend


def test_build_worker_uses_configured_backend() -> None:
    base_config = AppConfig.default()
    config = base_config.model_copy(
        update={
            "worker": base_config.worker.model_copy(update={"backend": WorkerBackend.CODEX})
        }
    )

    worker = build_worker(config)

    assert isinstance(worker, CodexAdapter)


def test_codex_adapter_builds_expected_command(tmp_path) -> None:
    adapter = CodexAdapter()

    command = adapter._build_command(
        cwd=tmp_path,
        last_message_path=tmp_path / "last.txt",
        prompt="fix pagination bug",
    )

    assert command[:2] == ["codex", "exec"]
    assert "--json" in command
    assert "--cd" in command
    assert "--sandbox" in command
    assert command[-1] == "fix pagination bug"


def test_codex_adapter_builds_resume_command(tmp_path) -> None:
    adapter = CodexAdapter()

    command = adapter._build_command(
        cwd=tmp_path,
        last_message_path=tmp_path / "last.txt",
        prompt="continue",
        worker_context_id="thread_123",
    )

    assert command[:4] == ["codex", "exec", "resume", "thread_123"]
    assert "--json" in command
    assert command[-1] == "continue"


def test_codex_adapter_submit_persists_run_metadata(tmp_path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakePopen:
        def __init__(self, command, **kwargs) -> None:
            seen["command"] = command
            seen["kwargs"] = kwargs
            self.pid = 4321

    monkeypatch.setattr("leopard_gecko.adapters.codex.subprocess.Popen", FakePopen)

    adapter = CodexAdapter()
    submission = adapter.submit(
        "sess_1",
        "task_1",
        "recover this run",
        cwd=tmp_path,
        data_dir=tmp_path,
    )

    meta_path = tmp_path / "worker_runs" / "sess_1" / "task_1.meta.json"
    payload = json.loads(meta_path.read_text(encoding="utf-8"))

    assert submission.run_id == "codex:sess_1:task_1"
    assert submission.process_id == 4321
    assert payload["run_id"] == submission.run_id
    assert payload["pid"] == 4321
    assert payload["status"] == "running"
    assert payload["cwd"] == str(tmp_path)
    assert payload["output_path"] == submission.output_path
    assert seen["command"][:2] == ["/bin/sh", "-c"]


def test_codex_adapter_poll_prefers_state_sidecar(tmp_path, monkeypatch) -> None:
    output_path = tmp_path / "worker_runs" / "sess_1" / "task_1.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('{"data":{"thread_id":"ctx_from_output"}}\n', encoding="utf-8")

    state_path = output_path.with_name("task_1.state.json")
    state_path.write_text(
        json.dumps(
            {
                "worker_context_id": "ctx_from_state",
                "last_message": "done from state",
                "updated_at": "2026-04-01T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = CodexAdapter()
    monkeypatch.setattr(
        adapter,
        "parse_output_for_context_id",
        lambda output: (_ for _ in ()).throw(AssertionError(f"unexpected output scan: {output}")),
    )
    monkeypatch.setattr(
        adapter,
        "parse_output_for_last_message",
        lambda output: (_ for _ in ()).throw(AssertionError(f"unexpected output scan: {output}")),
    )

    run_state = adapter.poll(run_id="missing", process_id=None, output_path=output_path)

    assert run_state.worker_context_id == "ctx_from_state"
    assert run_state.last_message == "done from state"


def test_codex_adapter_poll_backfills_state_from_small_files_and_output(tmp_path) -> None:
    output_path = tmp_path / "worker_runs" / "sess_1" / "task_1.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        '{"type":"thread","data":{"thread_id":"ctx_from_output"}}\n'
        '{"type":"message","data":{"text":"done from output"}}\n',
        encoding="utf-8",
    )
    output_path.with_name("task_1.last_message.txt").write_text("done from file\n", encoding="utf-8")

    adapter = CodexAdapter()
    run_state = adapter.poll(run_id="missing", process_id=None, output_path=output_path)
    state_path = output_path.with_name("task_1.state.json")
    payload = json.loads(state_path.read_text(encoding="utf-8"))

    assert run_state.worker_context_id == "ctx_from_output"
    assert run_state.last_message == "done from file"
    assert payload["worker_context_id"] == "ctx_from_output"
    assert payload["last_message"] == "done from file"


def test_build_worker_override_prefers_explicit_backend() -> None:
    config = AppConfig.default()

    worker = build_worker(config, WorkerBackend.NOOP)

    assert isinstance(worker, NoopWorkerAdapter)
