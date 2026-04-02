from datetime import datetime, timedelta, timezone

import pytest
from textual.widgets import Static

from leopard_gecko.models.session import (
    Session,
    SessionsState,
    SessionStatus,
    TaskHistoryEntry,
    TaskHistoryStatus,
)
from leopard_gecko.models.task import QueueStatus, RoutingDecision, Task, TaskRouting
from leopard_gecko.tui.app import LeopardGeckoApp
from leopard_gecko.tui.widgets.task_list import TaskList


@pytest.mark.asyncio
async def test_detail_screen_lists_tasks_including_tasks_from_dead_sessions(tmp_path) -> None:
    app = LeopardGeckoApp(data_dir=str(tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()

        now = datetime.now(timezone.utc)
        app.orchestrator.task_repo.save(  # type: ignore[union-attr]
            Task(
                task_id="task_live",
                user_prompt="still running task",
                task_note="still running task",
                queue_status=QueueStatus.RUNNING,
                routing=TaskRouting(
                    assigned_session_id="sess_live",
                    decision=RoutingDecision.ASSIGNED_EXISTING,
                ),
                created_at=now - timedelta(minutes=10),
            )
        )
        app.orchestrator.task_repo.save(  # type: ignore[union-attr]
            Task(
                task_id="task_done",
                user_prompt="finished task",
                task_note="finished task",
                queue_status=QueueStatus.COMPLETED,
                routing=TaskRouting(
                    assigned_session_id="sess_done",
                    decision=RoutingDecision.ASSIGNED_EXISTING,
                ),
                created_at=now - timedelta(minutes=1),
            )
        )

        app.orchestrator.sessions_repo.save(  # type: ignore[union-attr]
            SessionsState(
                sessions=[
                    Session(
                        session_id="sess_live",
                        status=SessionStatus.BUSY,
                        current_task_id="task_live",
                        created_at=now - timedelta(minutes=12),
                        last_heartbeat=now - timedelta(minutes=1),
                    ),
                    Session(
                        session_id="sess_done",
                        status=SessionStatus.DEAD,
                        task_history=[
                            TaskHistoryEntry(
                                task_id="task_done",
                                user_prompt="finished task",
                                task_note="finished task",
                                status=TaskHistoryStatus.COMPLETED,
                                summary="done",
                                updated_at=now,
                            )
                        ],
                        created_at=now - timedelta(minutes=3),
                        last_heartbeat=now,
                    ),
                ]
            )
        )

        await app.action_refresh()
        app.selected_task_id = "task_done"
        app.switch_screen("detail")
        await pilot.pause()

        screen = app.screen
        task_list = screen.query_one("#task-list", TaskList)
        header = screen.query_one("#detail-header", Static)

        assert [task.task_id for task in task_list._tasks] == ["task_done", "task_live"]
        assert app.selected_task_id == "task_done"
        assert "task_done" in str(header.content)
        assert "sess_done" in str(header.content)


@pytest.mark.asyncio
async def test_detail_screen_auto_selects_latest_task(tmp_path) -> None:
    app = LeopardGeckoApp(data_dir=str(tmp_path))

    async with app.run_test() as pilot:
        await pilot.pause()

        now = datetime.now(timezone.utc)
        app.orchestrator.task_repo.save(  # type: ignore[union-attr]
            Task(
                task_id="task_old",
                user_prompt="older prompt",
                task_note="older prompt",
                queue_status=QueueStatus.COMPLETED,
                created_at=now - timedelta(minutes=20),
            )
        )
        app.orchestrator.task_repo.save(  # type: ignore[union-attr]
            Task(
                task_id="task_recent",
                user_prompt="recent prompt",
                task_note="recent prompt",
                queue_status=QueueStatus.COMPLETED,
                created_at=now - timedelta(minutes=2),
            )
        )

        await app.action_refresh()
        app.switch_screen("detail")
        await pilot.pause()

        header = app.screen.query_one("#detail-header", Static)

        assert app.selected_task_id == "task_recent"
        assert "task_recent" in str(header.content)
