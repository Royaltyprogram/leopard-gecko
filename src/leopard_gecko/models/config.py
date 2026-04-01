from pydantic import BaseModel, Field


class QueuePolicy(BaseModel):
    max_queue_per_session: int = Field(default=5, ge=1)


class AppConfig(BaseModel):
    max_terminal_num: int = Field(default=4, ge=1)
    session_idle_timeout_min: int = Field(default=30, ge=1)
    queue_policy: QueuePolicy = Field(default_factory=QueuePolicy)
    data_dir: str | None = None

    @classmethod
    def default(cls, data_dir: str | None = None) -> "AppConfig":
        return cls(data_dir=data_dir)

