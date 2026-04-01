from leopard_gecko.adapters.base import WorkerPort
from leopard_gecko.adapters.noop import NoopWorkerAdapter
from leopard_gecko.models.config import AppConfig, WorkerBackend


def build_worker(config: AppConfig, backend_override: WorkerBackend | None = None) -> WorkerPort:
    backend = backend_override or config.worker.backend

    if backend is WorkerBackend.CODEX:
        from leopard_gecko.adapters.codex import CodexAdapter

        return CodexAdapter(config.worker.codex)

    return NoopWorkerAdapter()
