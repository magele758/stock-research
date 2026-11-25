"""Persistent state store + async job manager.

* `StateStore` – SQLite (WAL) registry shared by the API process and the MCP
  process: async jobs, computed result payloads (backtests / hypotheses) and
  MCP tool call statistics.
* `JobManager` – ThreadPoolExecutor wrapper with
  queued -> running -> completed/failed tracking, `idempotency_key` dedup and
  SSE progress streaming.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_core.schemas import AsyncJobRecord

from . import deps

TERMINAL = {"completed", "failed", "cancelled"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  job_type TEXT NOT NULL,
  idempotency_key TEXT,
  status TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  progress_pct INTEGER NOT NULL DEFAULT 0,
  summary TEXT,
  artifact_uri TEXT,
  error TEXT,
  worker_pid INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_idem ON jobs(job_type, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS results (
  kind TEXT NOT NULL,
  result_id TEXT NOT NULL,
  snapshot_id TEXT,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (kind, result_id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
  tool TEXT PRIMARY KEY,
  calls INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  total_ms REAL NOT NULL DEFAULT 0,
  last_ms REAL,
  last_status TEXT,
  last_at TEXT
);
"""


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _now() -> datetime:
    return datetime.now(UTC)


class StateStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- jobs ---------------------------------------------------------------
    def insert_job(self, rec: AsyncJobRecord, idempotency_key: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs(job_id,job_type,idempotency_key,status,snapshot_id,progress_pct,"
                "summary,artifact_uri,error,worker_pid,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.job_id, rec.job_type, idempotency_key, rec.status, rec.snapshot_id,
                    rec.progress_pct, json.dumps(rec.summary) if rec.summary else None,
                    rec.artifact_uri, rec.error, os.getpid(), rec.created_at.isoformat(), rec.updated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def update_job(self, job_id: str, **fields: Any) -> None:
        if "summary" in fields:
            fields["summary"] = json.dumps(fields["summary"], default=str) if fields["summary"] is not None else None
        fields["updated_at"] = _now().isoformat()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE job_id=?", (*fields.values(), job_id))
            self._conn.commit()

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> AsyncJobRecord:
        return AsyncJobRecord(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            snapshot_id=row["snapshot_id"],
            progress_pct=row["progress_pct"],
            summary=json.loads(row["summary"]) if row["summary"] else None,
            artifact_uri=row["artifact_uri"],
            error=row["error"],
        )

    def get_job(self, job_id: str) -> AsyncJobRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._row_to_job(row) if row else None

    def find_job_by_key(self, job_type: str, key: str) -> AsyncJobRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE job_type=? AND idempotency_key=?", (job_type, key)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
            self._conn.commit()

    def fail_orphans(self) -> int:
        """Jobs left queued/running by a dead worker process can never finish: mark failed."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT job_id, worker_pid FROM jobs WHERE status IN ('queued','running')"
            ).fetchall()
            dead = [r["job_id"] for r in rows if not _pid_alive(r["worker_pid"])]
            for jid in dead:
                self._conn.execute(
                    "UPDATE jobs SET status='failed', error='worker process exited before completion', "
                    "updated_at=? WHERE job_id=?",
                    (_now().isoformat(), jid),
                )
            self._conn.commit()
            return len(dead)

    # -- results ------------------------------------------------------------
    def put_result(self, kind: str, result_id: str, payload: dict[str, Any], snapshot_id: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO results(kind,result_id,snapshot_id,payload,created_at) VALUES (?,?,?,?,?)",
                (kind, result_id, snapshot_id, json.dumps(payload, default=str), _now().isoformat()),
            )
            self._conn.commit()

    def get_result(self, kind: str, result_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM results WHERE kind=? AND result_id=?", (kind, result_id)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_results(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM results WHERE kind=? ORDER BY created_at DESC", (kind,)
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # -- MCP tool stats -----------------------------------------------------
    def record_tool_call(self, tool: str, elapsed_ms: float, status: str) -> None:
        err = 1 if status == "error" else 0
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_calls(tool,calls,errors,total_ms,last_ms,last_status,last_at) "
                "VALUES (?,1,?,?,?,?,?) ON CONFLICT(tool) DO UPDATE SET calls=calls+1, errors=errors+?, "
                "total_ms=total_ms+?, last_ms=?, last_status=?, last_at=?",
                (tool, err, elapsed_ms, elapsed_ms, status, _now().isoformat(),
                 err, elapsed_ms, elapsed_ms, status, _now().isoformat()),
            )
            self._conn.commit()

    def tool_stats(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tool_calls").fetchall()
        return {r["tool"]: dict(r) for r in rows}


_store: StateStore | None = None
_store_lock = threading.Lock()


def get_store() -> StateStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = StateStore(deps.state_dir() / "quant_api_state.db")
            _store.fail_orphans()
        return _store


def set_store(store: StateStore | None) -> None:
    global _store
    with _store_lock:
        _store = store


ProgressFn = Callable[[int], None]
JobFn = Callable[[ProgressFn], dict[str, Any]]


class JobManager:
    def __init__(self, store: StateStore | None = None, max_workers: int = 2):
        self._store = store
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="quant-job")

    @property
    def store(self) -> StateStore:
        return self._store or get_store()

    def submit(
        self,
        job_type: str,
        snapshot_id: str,
        fn: JobFn,
        *,
        idempotency_key: str | None = None,
    ) -> AsyncJobRecord:
        """Enqueue `fn(progress)`; `fn` returns {"summary": dict, "artifact_uri": str|None}."""
        store = self.store
        if idempotency_key:
            existing = store.find_job_by_key(job_type, idempotency_key)
            if existing and existing.status == "failed":
                store.delete_job(existing.job_id)  # failed runs may be retried under the same key
            elif existing:
                return existing
        now = _now()
        rec = AsyncJobRecord(
            job_id=f"job_{uuid.uuid4().hex[:12]}",
            job_type=job_type,
            status="queued",
            created_at=now,
            updated_at=now,
            snapshot_id=snapshot_id,
            progress_pct=0,
        )
        try:
            store.insert_job(rec, idempotency_key)
        except sqlite3.IntegrityError:  # lost a race on idempotency key
            existing = store.find_job_by_key(job_type, idempotency_key or "")
            if existing:
                return existing
            raise
        self._mirror(rec.job_id)
        self._pool.submit(self._run, rec.job_id, fn)
        return rec

    def _run(self, job_id: str, fn: JobFn) -> None:
        store = self.store
        store.update_job(job_id, status="running", progress_pct=5)

        def progress(pct: int) -> None:
            store.update_job(job_id, progress_pct=max(0, min(99, int(pct))))

        try:
            out = fn(progress) or {}
            store.update_job(
                job_id,
                status="completed",
                progress_pct=100,
                summary=out.get("summary"),
                artifact_uri=out.get("artifact_uri"),
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"
            store.update_job(job_id, status="failed", error=msg[:1000])
            traceback.print_exc()
        self._mirror(job_id)

    def get(self, job_id: str) -> AsyncJobRecord | None:
        return self.store.get_job(job_id)

    def _mirror(self, job_id: str) -> None:
        """Best-effort copy of the job record into the QuantLake registry (if it exposes one)."""
        try:
            rec = self.store.get_job(job_id)
            lake = deps.get_lake()
            if rec and hasattr(lake, "save_job"):
                lake.save_job(rec)
        except Exception:  # noqa: BLE001
            pass

    def wait(self, job_id: str, timeout: float = 30.0, poll: float = 0.05) -> AsyncJobRecord | None:
        end = time.monotonic() + timeout
        rec = self.get(job_id)
        while rec and rec.status not in TERMINAL and time.monotonic() < end:
            time.sleep(poll)
            rec = self.get(job_id)
        return rec

    def sse_events(self, job_id: str, poll: float = 0.25, max_seconds: float = 900.0) -> Iterator[str]:
        """Yield SSE frames: `progress` on change, then a terminal `completed|failed` frame."""
        end = time.monotonic() + max_seconds
        last: tuple[str, int] | None = None
        while time.monotonic() < end:
            rec = self.get(job_id)
            if rec is None:
                yield _sse("error", {"job_id": job_id, "error": "job not found"})
                return
            cur = (rec.status, rec.progress_pct)
            if cur != last:
                yield _sse("progress", json.loads(rec.model_dump_json()))
                last = cur
            if rec.status in TERMINAL:
                yield _sse(rec.status, json.loads(rec.model_dump_json()))
                return
            time.sleep(poll)
        yield _sse("timeout", {"job_id": job_id})

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager


def set_manager(mgr: JobManager | None) -> None:
    global _manager
    with _manager_lock:
        _manager = mgr
