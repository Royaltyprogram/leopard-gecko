import pytest

from leopard_gecko.models.config import AppConfig, WorkerBackend
from leopard_gecko.tui.app import LeopardGeckoApp
from leopard_gecko.tui.screens.config import ConfigFormValues, build_updated_config


def test_build_updated_config_updates_nested_fields() -> None:
    config = AppConfig.default(data_dir=".leopard-gecko")

    updated = build_updated_config(
        config,
        ConfigFormValues(
            max_terminal_num=7,
            session_idle_timeout_min=45,
            max_queue_per_session=9,
            router_model="gpt-test",
            router_api_key_env_var="CUSTOM_OPENAI_KEY",
            router_base_url="https://example.com/v1/responses",
            router_timeout_sec=12.5,
            router_history_limit=8,
            router_reasoning_effort=None,
            worker_backend=WorkerBackend.CODEX,
            worker_command="custom-codex",
            worker_sandbox="danger-full-access",
            worker_approval_policy="on-request",
            worker_model="gpt-5",
            worker_profile="dev",
            worktree_enabled=True,
            worktree_root_dir="/tmp/worktrees",
            worktree_branch_prefix="feature",
            worktree_base_ref="main",
        ),
    )

    assert updated.max_terminal_num == 7
    assert updated.session_idle_timeout_min == 45
    assert updated.queue_policy.max_queue_per_session == 9
    assert updated.router.agent.model == "gpt-test"
    assert updated.router.agent.api_key_env_var == "CUSTOM_OPENAI_KEY"
    assert updated.router.agent.base_url == "https://example.com/v1/responses"
    assert updated.router.agent.timeout_sec == 12.5
    assert updated.router.agent.history_limit == 8
    assert updated.router.agent.reasoning_effort is None
    assert updated.worker.backend is WorkerBackend.CODEX
    assert updated.worker.codex.command == "custom-codex"
    assert updated.worker.codex.sandbox == "danger-full-access"
    assert updated.worker.codex.approval_policy == "on-request"
    assert updated.worker.codex.model == "gpt-5"
    assert updated.worker.codex.profile == "dev"
    assert updated.worktree.enabled is True
    assert updated.worktree.root_dir == "/tmp/worktrees"
    assert updated.worktree.branch_prefix == "feature"
    assert updated.worktree.base_ref == "main"


@pytest.mark.asyncio
async def test_config_screen_saves_updated_config(tmp_path) -> None:
    app = LeopardGeckoApp(data_dir=str(tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()

        screen = app.screen
        screen.query_one("#config-max-terminal-num").value = "6"
        screen.query_one("#config-router-model").value = "gpt-5"
        screen.query_one("#config-worker-backend").value = WorkerBackend.CODEX.value
        screen.query_one("#config-worktree-enabled").value = True

        await pilot.click("#config-save-btn")
        await pilot.pause()

    config = app.orchestrator.load_config()  # type: ignore[union-attr]
    assert config.max_terminal_num == 6
    assert config.router.agent.model == "gpt-5"
    assert config.worker.backend is WorkerBackend.CODEX
    assert config.worktree.enabled is True
