"""Safe, allowlisted cluster execution adapters (local_subprocess, herdr_hc, ssh_runner).

Security invariants:
1. ``shell=False`` everywhere — zero shell string interpolation from user inputs.
2. Strict ``study_id``, ``host``, and ``allowed_command_templates`` validation.
3. Isolated sandbox workspace under ``<workspace_root>/<study_id>/`` with path-traversal guard.
4. Read-only PIT data lake enforcement (``QUANT_LAKE_READ_ONLY=1``) and stripped secret env vars.
5. Hard process-group timeout termination and deterministic cleanup.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from quant_core.schemas import ClusterAdapterType, ClusterWorkerSpec

from quant_api import deps

_STUDY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_ENV_SUBSTRINGS = (
    "TOKEN",
    "SECRET",
    "KEY",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "COOKIE",
    "PRIVATE",
)

# Fixed allowlisted command template catalog (never accepts arbitrary shell strings).
COMMAND_TEMPLATE_REGISTRY: dict[str, str] = {
    "run_study_script": "quant-study-runner",
    "run_engine_sandbox": "quant-engine-sandbox",
    "agent_factor_dev": "quant-agent-factor-dev",
}

# Fixed, constant sandbox runner snippet executed by LocalSubprocessAdapter (no user text injected).
_SAFE_LOCAL_RUNNER_SCRIPT = """
import json, os, sys
from pathlib import Path

brief_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
brief = json.loads(brief_path.read_text(encoding="utf-8"))
read_only = os.environ.get("QUANT_LAKE_READ_ONLY") == "1"
expr = brief.get("expression") or ""
ast_safe = True
ast_warnings = []
if expr:
    try:
        from quant_core.factors.ast_guard import validate_factor_ast
        ast_safe, ast_warnings = validate_factor_ast(expr)
    except Exception as exc:
        ast_safe, ast_warnings = False, [str(exc)]

payload = {
    "candidate_expression": expr,
    "candidate_factor_id": brief.get("factor_id") or brief.get("study_id"),
    "worker_claimed_metrics": {
        "ast_precheck_safe": ast_safe,
        "ast_precheck_warnings": ast_warnings,
        "read_only_lake_enforced": read_only,
    },
    "notes": "local_subprocess isolated pre-execution completed",
}
out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"status": "ok", "read_only_lake": read_only, "ast_safe": ast_safe}))
""".strip()


class AdapterSecurityError(PermissionError):
    """Raised when an adapter call violates command template, host, or path-isolation rules."""


class TemplateNotAllowedError(AdapterSecurityError):
    """Raised when a requested command template is not in the worker's allowlist."""


class WorkerTaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    study_id: str
    command_template: str = "run_study_script"
    snapshot_id: str
    universe_id: str = "CSI500"
    title: str
    hypothesis: str = ""
    factor_id: str | None = None
    expression: str | None = None
    timeout_sec: int = 120
    extra_payload: dict[str, Any] = Field(default_factory=dict)


class WorkerOutputBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_run_id: str
    cluster_id: str
    adapter: str
    succeeded: bool
    exit_code: int = 0
    candidate_expression: str | None = None
    candidate_factor_id: str | None = None
    worker_claimed_metrics: dict[str, Any] = Field(default_factory=dict)
    math_derivation: dict[str, Any] | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    sandbox_dir: str = ""
    timed_out: bool = False
    error: str | None = None


class ClusterAdapter(Protocol):
    def health_check(self, spec: ClusterWorkerSpec) -> dict[str, Any]: ...

    def launch(
        self,
        spec: ClusterWorkerSpec,
        task: WorkerTaskEnvelope,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutputBundle: ...

    def cancel(self, spec: ClusterWorkerSpec, worker_run_id: str) -> bool: ...


def validate_study_id(study_id: str) -> str:
    if not study_id or not _STUDY_ID_RE.match(study_id):
        raise AdapterSecurityError(f"Invalid study_id for sandbox isolation: {study_id!r}")
    return study_id


def validate_command_template(spec: ClusterWorkerSpec, template_id: str) -> str:
    if template_id not in COMMAND_TEMPLATE_REGISTRY or template_id not in spec.allowed_command_templates:
        raise TemplateNotAllowedError(
            f"Command template {template_id!r} is not allowlisted for cluster {spec.cluster_id!r}"
        )
    return COMMAND_TEMPLATE_REGISTRY[template_id]


def validate_host(host: str) -> str:
    if not host or host.startswith("-") or not _HOST_RE.match(host):
        raise AdapterSecurityError(f"Unsafe or malformed cluster host: {host!r}")
    return host


def build_sanitized_env(data_root: Path | None = None) -> dict[str, str]:
    """Build a minimal subprocess environment stripped of secrets with read-only lake enforced."""
    safe: dict[str, str] = {}
    allow_exact = {"PATH", "PYTHONPATH", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV"}
    for key, val in os.environ.items():
        upper = key.upper()
        if any(tok in upper for tok in _SECRET_ENV_SUBSTRINGS):
            continue
        if key in allow_exact:
            safe[key] = val
    resolved_data = (data_root or deps.data_root()).resolve()
    safe["QUANT_DATA_ROOT"] = str(resolved_data)
    safe["QUANT_LAKE_READ_ONLY"] = "1"
    safe["PYTHONDONTWRITEBYTECODE"] = "1"
    return safe


def prepare_sandbox_dir(spec: ClusterWorkerSpec, study_id: str, task: WorkerTaskEnvelope) -> tuple[Path, Path, Path]:
    """Create isolated ``<workspace_root>/<study_id>/`` and write ``STUDY_BRIEF.json``."""
    safe_id = validate_study_id(study_id)
    ws_path = Path(spec.workspace_root)
    base = ws_path.resolve() if ws_path.is_absolute() else (deps.repo_root() / ws_path).resolve()
    sandbox = (base / safe_id).resolve()
    try:
        sandbox.relative_to(base)
    except ValueError as exc:
        raise AdapterSecurityError(f"Sandbox path escapes workspace_root: {sandbox}") from exc

    sandbox.mkdir(parents=True, exist_ok=True)
    brief_path = sandbox / "STUDY_BRIEF.json"
    output_path = sandbox / "candidate_output.json"
    brief_doc = {
        **task.model_dump(mode="json"),
        "lake_mount_mode": spec.lake_mount_mode,
        "read_only_lake": True,
    }
    brief_path.write_text(json.dumps(brief_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return sandbox, brief_path, output_path


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except OSError:
            return


def _parse_candidate_output(output_path: Path, stdout_text: str) -> dict[str, Any]:
    if output_path.is_file():
        try:
            parsed = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return {}


class LocalSubprocessAdapter:
    """Isolated local subprocess adapter (``shell=False``, read-only lake, hard timeout kill)."""

    def __init__(
        self,
        *,
        argv_builder: Callable[[ClusterWorkerSpec, WorkerTaskEnvelope, Path, Path], list[str]] | None = None,
    ) -> None:
        self._argv_builder = argv_builder
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen[str]] = {}

    def health_check(self, spec: ClusterWorkerSpec) -> dict[str, Any]:
        return {
            "status": "online" if spec.enabled else "offline",
            "adapter": ClusterAdapterType.LOCAL_SUBPROCESS.value,
            "cluster_id": spec.cluster_id,
            "python": sys.executable,
        }

    def launch(
        self,
        spec: ClusterWorkerSpec,
        task: WorkerTaskEnvelope,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutputBundle:
        validate_command_template(spec, task.command_template)
        sandbox, brief_path, output_path = prepare_sandbox_dir(spec, task.study_id, task)
        env = build_sanitized_env()
        worker_run_id = f"lsub-{uuid.uuid4().hex[:10]}"
        timeout = max(1, min(int(task.timeout_sec), int(spec.max_timeout_sec)))

        if self._argv_builder is not None:
            argv = self._argv_builder(spec, task, brief_path, output_path)
        else:
            argv = [sys.executable, "-c", _SAFE_LOCAL_RUNNER_SCRIPT, str(brief_path), str(output_path)]

        if on_event:
            on_event(
                {
                    "stage": "DEVELOPING",
                    "event_type": "progress",
                    "progress_pct": 20,
                    "message": f"Launched isolated local_subprocess ({worker_run_id}) with QUANT_LAKE_READ_ONLY=1",
                }
            )

        proc = subprocess.Popen(
            argv,
            shell=False,
            cwd=str(sandbox),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        with self._lock:
            self._active[worker_run_id] = proc

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc)
            try:
                stdout, stderr = proc.communicate(timeout=2)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""
            return WorkerOutputBundle(
                worker_run_id=worker_run_id,
                cluster_id=spec.cluster_id,
                adapter=ClusterAdapterType.LOCAL_SUBPROCESS.value,
                succeeded=False,
                exit_code=-9,
                stdout_tail=(stdout or "")[-2000:],
                stderr_tail=(stderr or "")[-2000:],
                sandbox_dir=str(sandbox),
                timed_out=True,
                error=f"TIMEOUT: subprocess killed after {timeout}s",
            )
        finally:
            with self._lock:
                self._active.pop(worker_run_id, None)

        parsed = _parse_candidate_output(output_path, stdout or "")
        return WorkerOutputBundle(
            worker_run_id=worker_run_id,
            cluster_id=spec.cluster_id,
            adapter=ClusterAdapterType.LOCAL_SUBPROCESS.value,
            succeeded=(proc.returncode == 0),
            exit_code=int(proc.returncode or 0),
            candidate_expression=parsed.get("candidate_expression") or task.expression,
            candidate_factor_id=parsed.get("candidate_factor_id") or task.factor_id,
            worker_claimed_metrics=parsed.get("worker_claimed_metrics") or {},
            math_derivation=parsed.get("math_derivation"),
            stdout_tail=(stdout or "")[-2000:],
            stderr_tail=(stderr or "")[-2000:],
            sandbox_dir=str(sandbox),
            timed_out=False,
            error=None if proc.returncode == 0 else f"Subprocess exited with code {proc.returncode}",
        )

    def cancel(self, spec: ClusterWorkerSpec, worker_run_id: str) -> bool:
        with self._lock:
            proc = self._active.get(worker_run_id)
        if proc is None:
            return False
        _kill_process_group(proc)
        return True


class HerdrHcAdapter:
    """Controlled ``herdr-core (hc)`` dispatch adapter with read-only brief and timeout reclamation."""

    def __init__(
        self,
        *,
        hc_binary: str | None = None,
        runner: Callable[[list[str], Path, dict[str, str], int], tuple[int, str, str]] | None = None,
    ) -> None:
        self._hc_binary = hc_binary
        self._runner = runner
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen[str]] = {}

    def _resolve_hc(self) -> str | None:
        if self._hc_binary:
            return self._hc_binary
        override = os.environ.get("QUANT_HC_BINARY", "").strip()
        if override:
            return override
        return shutil.which("hc")

    def health_check(self, spec: ClusterWorkerSpec) -> dict[str, Any]:
        hc_bin = self._resolve_hc()
        online = bool(spec.enabled and hc_bin)
        return {
            "status": "online" if online else "offline",
            "adapter": ClusterAdapterType.HERDR_HC.value,
            "cluster_id": spec.cluster_id,
            "hc_available": bool(hc_bin),
        }

    def build_command(
        self,
        spec: ClusterWorkerSpec,
        task: WorkerTaskEnvelope,
        brief_path: Path,
        output_path: Path,
    ) -> list[str]:
        entrypoint = validate_command_template(spec, task.command_template)
        hc_bin = self._resolve_hc() or "hc"
        return [
            hc_bin,
            "run",
            "--cwd",
            str(brief_path.parent),
            "--",
            entrypoint,
            "--agent",
            spec.agent_cli,
            "--study-id",
            task.study_id,
            "--brief",
            str(brief_path),
            "--output",
            str(output_path),
            "--read-only-lake",
        ]

    def launch(
        self,
        spec: ClusterWorkerSpec,
        task: WorkerTaskEnvelope,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutputBundle:
        validate_command_template(spec, task.command_template)
        hc_bin = self._resolve_hc()
        if not hc_bin and self._runner is None:
            raise RuntimeError(f"herdr-core (hc) binary unavailable for cluster {spec.cluster_id!r}")

        sandbox, brief_path, output_path = prepare_sandbox_dir(spec, task.study_id, task)
        env = build_sanitized_env()
        argv = self.build_command(spec, task, brief_path, output_path)
        worker_run_id = f"hc-{uuid.uuid4().hex[:10]}"
        timeout = max(1, min(int(task.timeout_sec), int(spec.max_timeout_sec)))

        if on_event:
            on_event(
                {
                    "stage": "DEVELOPING",
                    "event_type": "progress",
                    "progress_pct": 25,
                    "message": f"Dispatched read-only brief to herdr_hc ({spec.cluster_id})",
                }
            )

        if self._runner is not None:
            try:
                rc, stdout, stderr = self._runner(argv, sandbox, env, timeout)
            except subprocess.TimeoutExpired:
                return WorkerOutputBundle(
                    worker_run_id=worker_run_id,
                    cluster_id=spec.cluster_id,
                    adapter=ClusterAdapterType.HERDR_HC.value,
                    succeeded=False,
                    exit_code=-9,
                    sandbox_dir=str(sandbox),
                    timed_out=True,
                    error=f"TIMEOUT: herdr_hc worker killed after {timeout}s",
                )
        else:
            proc = subprocess.Popen(
                argv,
                shell=False,
                cwd=str(sandbox),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            with self._lock:
                self._active[worker_run_id] = proc
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                rc = int(proc.returncode or 0)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc)
                return WorkerOutputBundle(
                    worker_run_id=worker_run_id,
                    cluster_id=spec.cluster_id,
                    adapter=ClusterAdapterType.HERDR_HC.value,
                    succeeded=False,
                    exit_code=-9,
                    sandbox_dir=str(sandbox),
                    timed_out=True,
                    error=f"TIMEOUT: herdr_hc worker killed after {timeout}s",
                )
            finally:
                with self._lock:
                    self._active.pop(worker_run_id, None)

        parsed = _parse_candidate_output(output_path, stdout or "")
        return WorkerOutputBundle(
            worker_run_id=worker_run_id,
            cluster_id=spec.cluster_id,
            adapter=ClusterAdapterType.HERDR_HC.value,
            succeeded=(rc == 0),
            exit_code=rc,
            candidate_expression=parsed.get("candidate_expression") or task.expression,
            candidate_factor_id=parsed.get("candidate_factor_id") or task.factor_id,
            worker_claimed_metrics=parsed.get("worker_claimed_metrics") or {},
            math_derivation=parsed.get("math_derivation"),
            stdout_tail=(stdout or "")[-2000:],
            stderr_tail=(stderr or "")[-2000:],
            sandbox_dir=str(sandbox),
            timed_out=False,
            error=None if rc == 0 else f"herdr_hc exited with code {rc}",
        )

    def cancel(self, spec: ClusterWorkerSpec, worker_run_id: str) -> bool:
        with self._lock:
            proc = self._active.get(worker_run_id)
        if proc is None:
            return False
        _kill_process_group(proc)
        return True


class SshRunnerAdapter:
    """Strict allowlisted SSH runner (``shell=False``, zero free-text shell interpolation)."""

    def __init__(
        self,
        *,
        ssh_binary: str | None = None,
        runner: Callable[[list[str], str, dict[str, str], int], tuple[int, str, str]] | None = None,
    ) -> None:
        self._ssh_binary = ssh_binary
        self._runner = runner
        self._lock = threading.Lock()
        self._active: dict[str, subprocess.Popen[str]] = {}

    def _resolve_ssh(self) -> str | None:
        return self._ssh_binary or shutil.which("ssh")

    def health_check(self, spec: ClusterWorkerSpec) -> dict[str, Any]:
        ssh_bin = self._resolve_ssh()
        online = bool(spec.enabled and ssh_bin and not spec.host.startswith("remote-research-"))
        return {
            "status": "online" if online else "offline",
            "adapter": ClusterAdapterType.SSH_RUNNER.value,
            "cluster_id": spec.cluster_id,
            "ssh_available": bool(ssh_bin),
        }

    def build_command(self, spec: ClusterWorkerSpec, task: WorkerTaskEnvelope) -> list[str]:
        """Construct a strictly parameterized ``ssh`` argv with ZERO user free-text interpolation."""
        remote_entrypoint = validate_command_template(spec, task.command_template)
        host = validate_host(spec.host)
        study_id = validate_study_id(task.study_id)
        snap_safe = validate_study_id(task.snapshot_id)
        ssh_bin = self._resolve_ssh() or "ssh"
        return [
            ssh_bin,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            "--",
            host,
            remote_entrypoint,
            "--study-id",
            study_id,
            "--snapshot-id",
            snap_safe,
            "--read-only-lake",
        ]

    def launch(
        self,
        spec: ClusterWorkerSpec,
        task: WorkerTaskEnvelope,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerOutputBundle:
        argv = self.build_command(spec, task)
        if not self._resolve_ssh() and self._runner is None:
            raise RuntimeError(f"ssh binary unavailable for cluster {spec.cluster_id!r}")

        sandbox, brief_path, output_path = prepare_sandbox_dir(spec, task.study_id, task)
        env = build_sanitized_env()
        stdin_payload = brief_path.read_text(encoding="utf-8")
        worker_run_id = f"ssh-{uuid.uuid4().hex[:10]}"
        timeout = max(1, min(int(task.timeout_sec), int(spec.max_timeout_sec)))

        if on_event:
            on_event(
                {
                    "stage": "DEVELOPING",
                    "event_type": "progress",
                    "progress_pct": 25,
                    "message": f"Dispatched allowlisted ssh_runner template '{task.command_template}' to {spec.cluster_id}",
                }
            )

        if self._runner is not None:
            try:
                rc, stdout, stderr = self._runner(argv, stdin_payload, env, timeout)
            except subprocess.TimeoutExpired:
                return WorkerOutputBundle(
                    worker_run_id=worker_run_id,
                    cluster_id=spec.cluster_id,
                    adapter=ClusterAdapterType.SSH_RUNNER.value,
                    succeeded=False,
                    exit_code=-9,
                    sandbox_dir=str(sandbox),
                    timed_out=True,
                    error=f"TIMEOUT: ssh_runner killed after {timeout}s",
                )
        else:
            proc = subprocess.Popen(
                argv,
                shell=False,
                cwd=str(sandbox),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            with self._lock:
                self._active[worker_run_id] = proc
            try:
                stdout, stderr = proc.communicate(input=stdin_payload, timeout=timeout)
                rc = int(proc.returncode or 0)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc)
                return WorkerOutputBundle(
                    worker_run_id=worker_run_id,
                    cluster_id=spec.cluster_id,
                    adapter=ClusterAdapterType.SSH_RUNNER.value,
                    succeeded=False,
                    exit_code=-9,
                    sandbox_dir=str(sandbox),
                    timed_out=True,
                    error=f"TIMEOUT: ssh_runner killed after {timeout}s",
                )
            finally:
                with self._lock:
                    self._active.pop(worker_run_id, None)

        parsed = _parse_candidate_output(output_path, stdout or "")
        return WorkerOutputBundle(
            worker_run_id=worker_run_id,
            cluster_id=spec.cluster_id,
            adapter=ClusterAdapterType.SSH_RUNNER.value,
            succeeded=(rc == 0),
            exit_code=rc,
            candidate_expression=parsed.get("candidate_expression") or task.expression,
            candidate_factor_id=parsed.get("candidate_factor_id") or task.factor_id,
            worker_claimed_metrics=parsed.get("worker_claimed_metrics") or {},
            math_derivation=parsed.get("math_derivation"),
            stdout_tail=(stdout or "")[-2000:],
            stderr_tail=(stderr or "")[-2000:],
            sandbox_dir=str(sandbox),
            timed_out=False,
            error=None if rc == 0 else f"ssh_runner exited with code {rc}",
        )

    def cancel(self, spec: ClusterWorkerSpec, worker_run_id: str) -> bool:
        with self._lock:
            proc = self._active.get(worker_run_id)
        if proc is None:
            return False
        _kill_process_group(proc)
        return True


def get_adapter(adapter_type: ClusterAdapterType | str) -> ClusterAdapter:
    kind = adapter_type if isinstance(adapter_type, ClusterAdapterType) else ClusterAdapterType(str(adapter_type))
    if kind == ClusterAdapterType.LOCAL_SUBPROCESS:
        return LocalSubprocessAdapter()
    if kind == ClusterAdapterType.HERDR_HC:
        return HerdrHcAdapter()
    if kind == ClusterAdapterType.SSH_RUNNER:
        return SshRunnerAdapter()
    raise ValueError(f"Unsupported adapter type: {adapter_type!r}")


__all__ = [
    "COMMAND_TEMPLATE_REGISTRY",
    "AdapterSecurityError",
    "ClusterAdapter",
    "HerdrHcAdapter",
    "LocalSubprocessAdapter",
    "SshRunnerAdapter",
    "TemplateNotAllowedError",
    "WorkerOutputBundle",
    "WorkerTaskEnvelope",
    "build_sanitized_env",
    "get_adapter",
    "prepare_sandbox_dir",
    "validate_command_template",
    "validate_host",
    "validate_study_id",
]
