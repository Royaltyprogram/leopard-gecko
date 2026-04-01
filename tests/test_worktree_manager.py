from pathlib import Path
import subprocess

from leopard_gecko.models.config import WorktreeConfig
from leopard_gecko.worktree import WorktreeManager


def test_worktree_manager_creates_session_worktree(tmp_path) -> None:
    repo_dir = _init_git_repo(tmp_path / "repo")
    root_dir = tmp_path / "worktrees"
    manager = WorktreeManager(
        cwd=repo_dir,
        config=WorktreeConfig(enabled=True, root_dir=str(root_dir)),
    )

    worktree = manager.ensure(session_id="sess_1")

    assert worktree.created is True
    assert worktree.branch == "lg/sess_1"
    assert worktree.base_ref == "main"
    assert worktree.path == str(root_dir / "sess_1")
    assert _git_stdout(Path(worktree.path), "branch", "--show-current") == "lg/sess_1"


def test_worktree_manager_reuses_existing_worktree(tmp_path) -> None:
    repo_dir = _init_git_repo(tmp_path / "repo")
    root_dir = tmp_path / "worktrees"
    manager = WorktreeManager(
        cwd=repo_dir,
        config=WorktreeConfig(enabled=True, root_dir=str(root_dir)),
    )

    first = manager.ensure(session_id="sess_1")
    second = manager.ensure(session_id="sess_1")

    assert first.created is True
    assert second.created is False
    assert second.path == first.path
    assert second.branch == first.branch


def test_worktree_manager_defaults_root_outside_repo(tmp_path) -> None:
    repo_dir = _init_git_repo(tmp_path / "repo")
    manager = WorktreeManager(
        cwd=repo_dir,
        config=WorktreeConfig(enabled=True),
    )

    worktree = manager.ensure(session_id="sess_1")

    assert worktree.path == str(tmp_path / ".leopard-gecko-worktrees" / "repo" / "sess_1")
    assert Path(worktree.path).exists()


def _init_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")
    return path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_stdout(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()
