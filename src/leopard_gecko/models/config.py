import os
from enum import StrEnum

from pydantic import BaseModel, Field


DEFAULT_OPENAI_MODEL = "gpt-5-mini"


def _read_env_override(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None

    normalized = value.strip()
    return normalized or None


class QueuePolicy(BaseModel):
    max_queue_per_session: int = Field(default=5, ge=1)


class RouterBackend(StrEnum):
    AGENT = "agent"


class WorkerBackend(StrEnum):
    NOOP = "noop"
    CODEX = "codex"


class AgentRouterConfig(BaseModel):
    model: str = DEFAULT_OPENAI_MODEL
    api_key_env_var: str = "OPENAI_API_KEY"
    base_url: str = "https://api.openai.com/v1/responses"
    timeout_sec: float = Field(default=30.0, gt=0)
    history_limit: int = Field(default=5, ge=1)
    reasoning_effort: str | None = "low"

    @property
    def runtime_model(self) -> str:
        return _read_env_override("OPENAI_MODEL") or self.model


class RouterConfig(BaseModel):
    backend: RouterBackend = RouterBackend.AGENT
    agent: AgentRouterConfig = Field(default_factory=AgentRouterConfig)


class CodexWorkerConfig(BaseModel):
    command: str = "codex"
    sandbox: str = "workspace-write"
    approval_policy: str = "never"
    model: str | None = None
    profile: str | None = None
    completed_session_cooldown_sec: int = Field(default=15, ge=0)


class WorkerConfig(BaseModel):
    backend: WorkerBackend = WorkerBackend.NOOP
    codex: CodexWorkerConfig = Field(default_factory=CodexWorkerConfig)


class WorktreeConfig(BaseModel):
    enabled: bool = False
    root_dir: str | None = None
    branch_prefix: str = "lg"
    base_ref: str | None = None


class AppConfig(BaseModel):
    max_terminal_num: int = Field(default=4, ge=1)
    session_idle_timeout_min: int = Field(default=30, ge=1)
    queue_policy: QueuePolicy = Field(default_factory=QueuePolicy)
    router: RouterConfig = Field(default_factory=RouterConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    data_dir: str | None = None

    @classmethod
    def default(cls, data_dir: str | None = None) -> "AppConfig":
        return cls(data_dir=data_dir)
