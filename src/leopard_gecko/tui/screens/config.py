from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, Switch

from leopard_gecko.models.config import AppConfig, WorkerBackend
from leopard_gecko.tui.widgets.status_bar import StatusBar

if TYPE_CHECKING:
    from leopard_gecko.tui.app import LeopardGeckoApp


_NONE_OPTION = "__none__"


def _required_text(value: str, label: str) -> str:
    normalized = value.strip()
    if normalized:
        return normalized
    raise ValueError(f"{label} is required")


def _optional_text(value: str) -> str | None:
    normalized = value.strip()
    return normalized or None


def _parse_int(value: str, label: str) -> int:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    try:
        return int(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer") from exc


def _parse_float(value: str, label: str) -> float:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    try:
        return float(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number") from exc


@dataclass(slots=True)
class ConfigFormValues:
    max_terminal_num: int
    session_idle_timeout_min: int
    max_queue_per_session: int
    router_model: str
    router_api_key_env_var: str
    router_base_url: str
    router_timeout_sec: float
    router_history_limit: int
    router_reasoning_effort: str | None
    worker_backend: WorkerBackend
    worker_command: str
    worker_sandbox: str
    worker_approval_policy: str
    worker_model: str | None
    worker_profile: str | None
    worktree_enabled: bool
    worktree_root_dir: str | None
    worktree_branch_prefix: str
    worktree_base_ref: str | None


def build_updated_config(config: AppConfig, values: ConfigFormValues) -> AppConfig:
    payload = config.model_dump(mode="python")
    payload["max_terminal_num"] = values.max_terminal_num
    payload["session_idle_timeout_min"] = values.session_idle_timeout_min
    payload["queue_policy"]["max_queue_per_session"] = values.max_queue_per_session
    payload["router"]["agent"]["model"] = values.router_model
    payload["router"]["agent"]["api_key_env_var"] = values.router_api_key_env_var
    payload["router"]["agent"]["base_url"] = values.router_base_url
    payload["router"]["agent"]["timeout_sec"] = values.router_timeout_sec
    payload["router"]["agent"]["history_limit"] = values.router_history_limit
    payload["router"]["agent"]["reasoning_effort"] = values.router_reasoning_effort
    payload["worker"]["backend"] = values.worker_backend
    payload["worker"]["codex"]["command"] = values.worker_command
    payload["worker"]["codex"]["sandbox"] = values.worker_sandbox
    payload["worker"]["codex"]["approval_policy"] = values.worker_approval_policy
    payload["worker"]["codex"]["model"] = values.worker_model
    payload["worker"]["codex"]["profile"] = values.worker_profile
    payload["worktree"]["enabled"] = values.worktree_enabled
    payload["worktree"]["root_dir"] = values.worktree_root_dir
    payload["worktree"]["branch_prefix"] = values.worktree_branch_prefix
    payload["worktree"]["base_ref"] = values.worktree_base_ref
    return AppConfig.model_validate(payload)


class ConfigScreen(Screen):
    BINDINGS = [
        ("ctrl+s", "save", "Save"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Edit saved config values for this workspace.", id="config-header")
        with VerticalScroll(id="config-form"):
            yield Static("General", classes="config-section-title")
            yield Label("Max terminal count", classes="config-label")
            yield Input(id="config-max-terminal-num", classes="config-input")
            yield Label("Session idle timeout (min)", classes="config-label")
            yield Input(id="config-session-idle-timeout-min", classes="config-input")
            yield Label("Max queue per session", classes="config-label")
            yield Input(id="config-max-queue-per-session", classes="config-input")

            yield Static("Router", classes="config-section-title")
            yield Static("Router backend is fixed to agent.", classes="config-note")
            yield Label("Router model", classes="config-label")
            yield Input(id="config-router-model", classes="config-input")
            yield Label("OpenAI API key env var", classes="config-label")
            yield Input(id="config-router-api-key-env-var", classes="config-input")
            yield Label("Responses base URL", classes="config-label")
            yield Input(id="config-router-base-url", classes="config-input")
            yield Label("Router timeout (sec)", classes="config-label")
            yield Input(id="config-router-timeout-sec", classes="config-input")
            yield Label("Task history limit", classes="config-label")
            yield Input(id="config-router-history-limit", classes="config-input")
            yield Label("Reasoning effort", classes="config-label")
            yield Select(
                [
                    ("None", _NONE_OPTION),
                    ("low", "low"),
                    ("medium", "medium"),
                    ("high", "high"),
                ],
                allow_blank=False,
                id="config-router-reasoning-effort",
                classes="config-select",
            )

            yield Static("Worker", classes="config-section-title")
            yield Label("Worker backend", classes="config-label")
            yield Select(
                [(backend.value, backend.value) for backend in WorkerBackend],
                allow_blank=False,
                id="config-worker-backend",
                classes="config-select",
            )
            yield Label("Codex command", classes="config-label")
            yield Input(id="config-worker-command", classes="config-input")
            yield Label("Sandbox mode", classes="config-label")
            yield Input(id="config-worker-sandbox", classes="config-input")
            yield Label("Approval policy", classes="config-label")
            yield Input(id="config-worker-approval-policy", classes="config-input")
            yield Label("Worker model override", classes="config-label")
            yield Input(id="config-worker-model", classes="config-input")
            yield Label("Worker profile", classes="config-label")
            yield Input(id="config-worker-profile", classes="config-input")
            yield Static("Worktree", classes="config-section-title")
            with Horizontal(classes="config-toggle-row"):
                yield Label("Enable worktree support", classes="config-label config-toggle-label")
                yield Switch(id="config-worktree-enabled")
            yield Label("Worktree root dir", classes="config-label")
            yield Input(id="config-worktree-root-dir", classes="config-input")
            yield Label("Branch prefix", classes="config-label")
            yield Input(id="config-worktree-branch-prefix", classes="config-input")
            yield Label("Base ref", classes="config-label")
            yield Input(id="config-worktree-base-ref", classes="config-input")
        with Horizontal(id="config-actions"):
            yield Button("Save", id="config-save-btn", variant="primary")
            yield Button("Cancel", id="config-cancel-btn")
        yield StatusBar("Config")
        yield Footer()

    def on_mount(self) -> None:
        self._load_form()
        self.query_one("#config-max-terminal-num", Input).focus()
        self.refresh_state()

    def on_screen_resume(self) -> None:
        self._load_form()
        self.refresh_state()

    def refresh_state(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if app.current_state:
            self.query_one(StatusBar).update_from_state(app.current_state)

    def action_save(self) -> None:
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "config-save-btn":
            self._save()
            return
        if event.button.id == "config-cancel-btn":
            self.app.switch_screen("submit")

    def _load_form(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if not app.orchestrator:
            return

        config = app.orchestrator.load_config()
        self.query_one("#config-header", Static).update(
            f"Edit saved config values for this workspace.\n[dim]Data dir:[/] {config.data_dir or '-'}"
        )
        self.query_one("#config-max-terminal-num", Input).value = str(config.max_terminal_num)
        self.query_one("#config-session-idle-timeout-min", Input).value = str(
            config.session_idle_timeout_min
        )
        self.query_one("#config-max-queue-per-session", Input).value = str(
            config.queue_policy.max_queue_per_session
        )
        self.query_one("#config-router-model", Input).value = config.router.agent.model
        self.query_one("#config-router-api-key-env-var", Input).value = config.router.agent.api_key_env_var
        self.query_one("#config-router-base-url", Input).value = config.router.agent.base_url
        self.query_one("#config-router-timeout-sec", Input).value = str(config.router.agent.timeout_sec)
        self.query_one("#config-router-history-limit", Input).value = str(
            config.router.agent.history_limit
        )
        self.query_one("#config-router-reasoning-effort", Select).value = (
            config.router.agent.reasoning_effort or _NONE_OPTION
        )
        self.query_one("#config-worker-backend", Select).value = config.worker.backend.value
        self.query_one("#config-worker-command", Input).value = config.worker.codex.command
        self.query_one("#config-worker-sandbox", Input).value = config.worker.codex.sandbox
        self.query_one("#config-worker-approval-policy", Input).value = (
            config.worker.codex.approval_policy
        )
        self.query_one("#config-worker-model", Input).value = config.worker.codex.model or ""
        self.query_one("#config-worker-profile", Input).value = config.worker.codex.profile or ""
        self.query_one("#config-worktree-enabled", Switch).value = config.worktree.enabled
        self.query_one("#config-worktree-root-dir", Input).value = config.worktree.root_dir or ""
        self.query_one("#config-worktree-branch-prefix", Input).value = config.worktree.branch_prefix
        self.query_one("#config-worktree-base-ref", Input).value = config.worktree.base_ref or ""

    def _read_form(self) -> ConfigFormValues:
        reasoning_effort = self.query_one("#config-router-reasoning-effort", Select).value
        worker_backend = self.query_one("#config-worker-backend", Select).value
        if not isinstance(reasoning_effort, str) or not isinstance(worker_backend, str):
            raise ValueError("Select a value for all dropdown fields")

        return ConfigFormValues(
            max_terminal_num=_parse_int(
                self.query_one("#config-max-terminal-num", Input).value,
                "Max terminal count",
            ),
            session_idle_timeout_min=_parse_int(
                self.query_one("#config-session-idle-timeout-min", Input).value,
                "Session idle timeout",
            ),
            max_queue_per_session=_parse_int(
                self.query_one("#config-max-queue-per-session", Input).value,
                "Max queue per session",
            ),
            router_model=_required_text(
                self.query_one("#config-router-model", Input).value,
                "Router model",
            ),
            router_api_key_env_var=_required_text(
                self.query_one("#config-router-api-key-env-var", Input).value,
                "OpenAI API key env var",
            ),
            router_base_url=_required_text(
                self.query_one("#config-router-base-url", Input).value,
                "Responses base URL",
            ),
            router_timeout_sec=_parse_float(
                self.query_one("#config-router-timeout-sec", Input).value,
                "Router timeout",
            ),
            router_history_limit=_parse_int(
                self.query_one("#config-router-history-limit", Input).value,
                "Task history limit",
            ),
            router_reasoning_effort=(
                None if reasoning_effort == _NONE_OPTION else reasoning_effort
            ),
            worker_backend=WorkerBackend(worker_backend),
            worker_command=_required_text(
                self.query_one("#config-worker-command", Input).value,
                "Codex command",
            ),
            worker_sandbox=_required_text(
                self.query_one("#config-worker-sandbox", Input).value,
                "Sandbox mode",
            ),
            worker_approval_policy=_required_text(
                self.query_one("#config-worker-approval-policy", Input).value,
                "Approval policy",
            ),
            worker_model=_optional_text(self.query_one("#config-worker-model", Input).value),
            worker_profile=_optional_text(self.query_one("#config-worker-profile", Input).value),
            worktree_enabled=self.query_one("#config-worktree-enabled", Switch).value,
            worktree_root_dir=_optional_text(
                self.query_one("#config-worktree-root-dir", Input).value
            ),
            worktree_branch_prefix=_required_text(
                self.query_one("#config-worktree-branch-prefix", Input).value,
                "Branch prefix",
            ),
            worktree_base_ref=_optional_text(
                self.query_one("#config-worktree-base-ref", Input).value
            ),
        )

    def _save(self) -> None:
        app: LeopardGeckoApp = self.app  # type: ignore[assignment]
        if not app.orchestrator:
            self.notify("Orchestrator not ready", severity="error")
            return

        try:
            current = app.orchestrator.load_config()
            updated = build_updated_config(current, self._read_form())
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return

        app.orchestrator.config_repo.save(updated)
        self.notify("Config saved", severity="information")
        self.app.switch_screen("submit")
