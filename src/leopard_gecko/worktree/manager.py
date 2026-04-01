import subprocess
from pathlib import Path

from pydantic import BaseModel

from leopard_gecko.models.config import WorktreeConfig


class WorktreeError(RuntimeError):
    pass


class SessionWorktree(BaseModel):
    path: str
    branch: str
    base_ref: str
    created: bool = False
    created_branch: bool = False


class WorktreeManager:
    def __init__(self, *, cwd: Path, config: WorktreeConfig) -> None:
        self._cwd = cwd.resolve()
        self._config = config

    def ensure(
        self,
        *,
        session_id: str,
        existing_path: str | None = None,
        existing_branch: str | None = None,
        existing_base_ref: str | None = None,
    ) -> SessionWorktree:
        repo_root = self._resolve_repo_root()
        branch = existing_branch or self._branch_name(session_id)
        base_ref = existing_base_ref or self._resolve_base_ref(repo_root)
        worktree_path = (
            Path(existing_path).expanduser().resolve()
            if existing_path is not None
            else (self._resolve_root_dir(repo_root) / session_id).resolve()
        )

        if self._is_git_worktree(worktree_path):
            return SessionWorktree(
                path=str(worktree_path),
                branch=branch,
                base_ref=base_ref,
            )

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        branch_exists = self._local_branch_exists(repo_root, branch)
        command = ["git", "-C", str(repo_root), "worktree", "add"]
        if branch_exists:
            command.extend([str(worktree_path), branch])
        else:
            command.extend(["-b", branch, str(worktree_path), base_ref])
        self._run(command)
        return SessionWorktree(
            path=str(worktree_path),
            branch=branch,
            base_ref=base_ref,
            created=True,
            created_branch=not branch_exists,
        )

    def remove(
        self,
        *,
        path: str | Path,
        branch: str | None = None,
        remove_branch: bool = False,
    ) -> None:
        repo_root = self._resolve_repo_root()
        worktree_path = Path(path).expanduser().resolve()

        if self._is_git_worktree(worktree_path):
            self._run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree_path)])

        if remove_branch and branch and self._local_branch_exists(repo_root, branch):
            self._run(["git", "-C", str(repo_root), "branch", "-D", branch])

    def _resolve_repo_root(self) -> Path:
        return Path(
            self._run(
                ["git", "-C", str(self._cwd), "rev-parse", "--show-toplevel"],
            )
        ).resolve()

    def _resolve_root_dir(self, repo_root: Path) -> Path:
        if self._config.root_dir:
            return Path(self._config.root_dir).expanduser().resolve()
        return (repo_root.parent / ".leopard-gecko-worktrees" / repo_root.name).resolve()

    def _resolve_base_ref(self, repo_root: Path) -> str:
        if self._config.base_ref:
            return self._config.base_ref

        branch_name = self._run(
            ["git", "-C", str(repo_root), "branch", "--show-current"],
            allow_empty=True,
        )
        return branch_name or "HEAD"

    def _branch_name(self, session_id: str) -> str:
        return f"{self._config.branch_prefix}/{session_id}"

    def _local_branch_exists(self, repo_root: Path, branch: str) -> bool:
        try:
            self._run(
                ["git", "-C", str(repo_root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
            )
        except WorktreeError:
            return False
        return True

    def _is_git_worktree(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            self._run(
                ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            )
        except WorktreeError:
            return False
        return True

    @staticmethod
    def _run(command: list[str], *, allow_empty: bool = False) -> str:
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            stdout = exc.stdout.strip()
            message = stderr or stdout or "git command failed"
            raise WorktreeError(message) from exc

        output = completed.stdout.strip()
        if output or allow_empty:
            return output
        return ""
