from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess
from typing import Any

from leopard_gecko.adapters.base import WorkerPort, WorkerRunState, WorkerSubmission
from leopard_gecko.models.config import CodexWorkerConfig


@dataclass
class RunStateFiles:
    worker_context_id: str | None = None
    last_message: str | None = None


class CodexAdapter(WorkerPort):
    def __init__(self, config: CodexWorkerConfig | None = None) -> None:
        self._config = config or CodexWorkerConfig()
        self.processes: dict[str, subprocess.Popen[str]] = {}

    def submit(
        self,
        session_id: str,
        task_id: str,
        user_prompt: str,
        *,
        cwd: Path,
        data_dir: Path,
        worker_context_id: str | None = None,
    ) -> WorkerSubmission:
        run_dir = data_dir / "worker_runs" / session_id
        run_dir.mkdir(parents=True, exist_ok=True)

        stdout_path = run_dir / f"{task_id}.jsonl"
        last_message_path = run_dir / f"{task_id}.last_message.txt"
        meta_path = run_dir / f"{task_id}.meta.json"
        exit_path = run_dir / f"{task_id}.exit.json"
        run_id = f"codex:{session_id}:{task_id}"

        command = self._build_command(
            cwd=cwd,
            last_message_path=last_message_path,
            prompt=user_prompt,
            worker_context_id=worker_context_id,
        )

        exit_path.unlink(missing_ok=True)
        with stdout_path.open("a", encoding="utf-8") as handle:
            process = subprocess.Popen(
                self._build_wrapped_command(
                    command=command,
                    exit_path=exit_path,
                    prompt=user_prompt,
                ),
                cwd=str(cwd),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )

        self._write_json(
            meta_path,
            {
                "run_id": run_id,
                "task_id": task_id,
                "session_id": session_id,
                "pid": process.pid,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "worker_context_id": worker_context_id,
                "cwd": str(cwd),
                "output_path": str(stdout_path),
                "status": "running",
            },
        )
        self._write_state_file(output_path=stdout_path, worker_context_id=worker_context_id)
        self.processes[run_id] = process
        return WorkerSubmission(
            session_id=session_id,
            task_id=task_id,
            run_id=run_id,
            process_id=process.pid,
            worker_context_id=worker_context_id,
            output_path=str(stdout_path),
        )

    def poll(
        self,
        *,
        run_id: str | None,
        process_id: int | None,
        output_path: Path | None,
    ) -> WorkerRunState:
        output_path = output_path.resolve() if output_path is not None else None
        metadata = self._read_run_metadata(output_path)
        state_files = self.load_run_state_files(output_path)
        resolved_run_id = run_id or _read_str(metadata, "run_id")
        resolved_process_id = process_id or _read_int(metadata, "pid")
        worker_context_id = state_files.worker_context_id
        last_message = state_files.last_message

        if worker_context_id is None:
            worker_context_id = self.parse_output_for_context_id(output_path) or _read_str(
                metadata, "worker_context_id"
            )
        if last_message is None:
            last_message = self.parse_output_for_last_message(output_path)

        self._write_state_file(
            output_path=output_path,
            worker_context_id=worker_context_id,
            last_message=last_message,
        )
        process = self.processes.get(resolved_run_id) if resolved_run_id is not None else None

        if process is not None:
            exit_code = process.poll()
            if exit_code is None:
                return WorkerRunState(
                    run_id=resolved_run_id,
                    is_running=True,
                    worker_context_id=worker_context_id,
                    last_message=last_message,
                )

            self.processes.pop(resolved_run_id, None)
            return WorkerRunState(
                run_id=resolved_run_id,
                is_running=False,
                exit_code=exit_code,
                worker_context_id=worker_context_id,
                last_message=last_message,
            )

        if self._is_process_running(resolved_process_id):
            return WorkerRunState(
                run_id=resolved_run_id,
                is_running=True,
                worker_context_id=worker_context_id,
                last_message=last_message,
            )

        exit_payload = self._read_exit_payload(output_path)
        if exit_payload is not None:
            return WorkerRunState(
                run_id=resolved_run_id,
                is_running=False,
                exit_code=_read_int(exit_payload, "exit_code"),
                worker_context_id=worker_context_id,
                last_message=last_message,
            )

        return WorkerRunState(
            run_id=resolved_run_id,
            is_running=False,
            worker_context_id=worker_context_id,
            last_message=last_message,
            requires_manual_recovery=True,
            recovery_reason="missing_exit_metadata",
        )

    def _build_command(
        self,
        *,
        cwd: Path,
        last_message_path: Path,
        prompt: str,
        worker_context_id: str | None = None,
    ) -> list[str]:
        if worker_context_id:
            return self._build_resume_command(
                cwd=cwd,
                last_message_path=last_message_path,
                prompt=prompt,
                worker_context_id=worker_context_id,
            )
        return self._build_exec_command(
            cwd=cwd,
            last_message_path=last_message_path,
            prompt=prompt,
        )

    def _build_exec_command(
        self,
        *,
        cwd: Path,
        last_message_path: Path,
        prompt: str,
    ) -> list[str]:
        del prompt
        command = self._build_exec_base_command(cwd=cwd)
        command.extend(["--output-last-message", str(last_message_path), "-"])
        return command

    def _build_resume_command(
        self,
        *,
        cwd: Path,
        last_message_path: Path,
        prompt: str,
        worker_context_id: str,
    ) -> list[str]:
        del prompt
        command = self._build_exec_base_command(cwd=cwd)
        command.append("resume")
        command.extend(
            ["--output-last-message", str(last_message_path), "--", worker_context_id, "-"]
        )
        return command

    def _build_exec_base_command(self, *, cwd: Path) -> list[str]:
        command = [
            self._config.command,
            "exec",
            "--json",
            "--cd",
            str(cwd),
            "--sandbox",
            self._config.sandbox,
        ]
        command.extend(self._config_overrides())

        if self._config.profile:
            command.extend(["--profile", self._config.profile])
        if self._config.model:
            command.extend(["--model", self._config.model])

        return command

    def _config_overrides(self) -> list[str]:
        overrides: list[str] = []

        if self._config.approval_policy:
            overrides.extend(["-c", f'approval_policy="{self._config.approval_policy}"'])

        return overrides

    def _build_wrapped_command(
        self,
        *,
        command: list[str],
        exit_path: Path,
        prompt: str,
    ) -> list[str]:
        quoted_command = shlex.join(command)
        quoted_prompt = shlex.quote(prompt)
        python_code = (
            "import json, sys; "
            "from datetime import datetime, timezone; "
            "from pathlib import Path; "
            "path = Path(sys.argv[1]); "
            "payload = {"
            "'exit_code': int(sys.argv[2]), "
            "'finished_at': datetime.now(timezone.utc).isoformat()"
            "}; "
            "tmp_path = path.with_suffix(path.suffix + '.tmp'); "
            "tmp_path.write_text(json.dumps(payload, ensure_ascii=False) + '\\n', encoding='utf-8'); "
            "tmp_path.replace(path)"
        )
        script = "\n".join(
            [
                f"printf '%s' {quoted_prompt} | {quoted_command}",
                "exit_code=$?",
                f"python3 -c {shlex.quote(python_code)} {shlex.quote(str(exit_path))} \"$exit_code\"",
                'exit "$exit_code"',
            ]
        )
        return ["/bin/sh", "-c", script]

    @staticmethod
    def _is_process_running(process_id: int | None) -> bool:
        if process_id is None:
            return False
        try:
            os.kill(process_id, 0)
        except OSError:
            return False
        return True

    def load_run_state_files(self, output_path: Path | None) -> RunStateFiles:
        state_payload = self._read_json(self._state_path(output_path))
        state = RunStateFiles(
            worker_context_id=_read_str(state_payload, "worker_context_id"),
            last_message=_read_str(state_payload, "last_message"),
        )
        if state.last_message is None:
            state.last_message = self._read_last_message_file(output_path)
        return state

    @staticmethod
    def _read_last_message_file(output_path: Path | None) -> str | None:
        if output_path is None:
            return None
        last_message_path = output_path.with_name(f"{output_path.stem}.last_message.txt")
        if not last_message_path.exists():
            return None
        content = last_message_path.read_text(encoding="utf-8").strip()
        return content or None

    def parse_output_for_context_id(self, output_path: Path | None) -> str | None:
        if output_path is None or not output_path.exists():
            return None

        thread_id: str | None = None
        session_id: str | None = None
        for line in output_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            candidate_thread_id, candidate_session_id = self._extract_context_ids(event)
            thread_id = candidate_thread_id or thread_id
            session_id = candidate_session_id or session_id
        return thread_id or session_id

    def parse_output_for_last_message(self, output_path: Path | None) -> str | None:
        if output_path is None or not output_path.exists():
            return None

        last_message: str | None = None
        for line in output_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            last_message = self._extract_last_message(event) or last_message
        return last_message

    @staticmethod
    def _extract_context_ids(event: object) -> tuple[str | None, str | None]:
        if not isinstance(event, dict):
            return None, None

        thread_id: str | None = None
        session_id: str | None = None
        for candidate in (event, event.get("data"), event.get("payload")):
            if not isinstance(candidate, dict):
                continue
            if thread_id is None:
                thread_id = _read_non_empty_str(candidate.get("thread_id"))
            if session_id is None:
                session_id = _read_non_empty_str(candidate.get("session_id"))
        return thread_id, session_id

    @staticmethod
    def _extract_last_message(event: object) -> str | None:
        if not isinstance(event, dict):
            return None

        for candidate in (event, event.get("data"), event.get("payload")):
            text = _extract_message_text(candidate)
            if text is not None:
                return text
        return None

    @staticmethod
    def _read_run_metadata(output_path: Path | None) -> dict[str, Any] | None:
        return CodexAdapter._read_json(CodexAdapter._meta_path(output_path))

    @staticmethod
    def _read_exit_payload(output_path: Path | None) -> dict[str, Any] | None:
        return CodexAdapter._read_json(CodexAdapter._exit_path(output_path))

    @staticmethod
    def _meta_path(output_path: Path | None) -> Path | None:
        if output_path is None:
            return None
        return output_path.with_name(f"{output_path.stem}.meta.json")

    @staticmethod
    def _exit_path(output_path: Path | None) -> Path | None:
        if output_path is None:
            return None
        return output_path.with_name(f"{output_path.stem}.exit.json")

    @staticmethod
    def _state_path(output_path: Path | None) -> Path | None:
        if output_path is None:
            return None
        return output_path.with_name(f"{output_path.stem}.state.json")

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any] | None:
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_path.replace(path)

    def _write_state_file(
        self,
        *,
        output_path: Path | None,
        worker_context_id: str | None = None,
        last_message: str | None = None,
    ) -> None:
        state_path = self._state_path(output_path)
        if state_path is None:
            return

        current = self._read_json(state_path)
        current_worker_context_id = _read_str(current, "worker_context_id")
        current_last_message = _read_str(current, "last_message")
        payload_worker_context_id = worker_context_id or current_worker_context_id
        payload_last_message = last_message or current_last_message

        if (
            current_worker_context_id == payload_worker_context_id
            and current_last_message == payload_last_message
        ):
            return

        payload = {
            "worker_context_id": payload_worker_context_id,
            "last_message": payload_last_message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(state_path, payload)


def _read_int(payload: dict[str, Any] | None, key: str) -> int | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _read_str(payload: dict[str, Any] | None, key: str) -> str | None:
    if payload is None:
        return None
    return _read_non_empty_str(payload.get(key))


def _read_non_empty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _extract_message_text(candidate: object) -> str | None:
    if isinstance(candidate, str):
        text = candidate.strip()
        return text or None
    if not isinstance(candidate, dict):
        return None

    for key in ("last_message", "message", "content", "text"):
        text = _normalize_message_value(candidate.get(key))
        if text is not None:
            return text
    return None


def _normalize_message_value(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            text = _normalize_message_value(item)
            if text is not None:
                parts.append(text)
        if parts:
            return "\n".join(parts)
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            text = _normalize_message_value(value.get(key))
            if text is not None:
                return text
    return None
