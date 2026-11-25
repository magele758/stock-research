"""Lazy wiring of quant_core.lake / engines.

Imports are deferred so the service can start (and report a clear, actionable
error) before quant-lake / quant-engines have landed or before `make seed`.
Nothing here ever fabricates market data.
"""

from __future__ import annotations

import importlib
import os
import threading
from pathlib import Path
from types import ModuleType
from typing import Any

SEED_HINT = "No dataset snapshot found. Run `make seed` (scripts/seed_golden_dataset.py) to build the PIT lake."

ENGINE_MODULES = {
    "factors": "quant_core.factors.engine",
    "validation": "quant_core.validation.cpcv",
    "risk": "quant_core.risk.barra_opt",
    "backtest": "quant_core.backtest.engine",
    "shadow": "quant_core.shadow.monitor",
}


class ServiceUnavailable(Exception):
    """Raised when the lake / an engine / a snapshot is not available (HTTP 503)."""

    def __init__(self, message: str, *, code: str = "DATA_STALE", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


class NotFound(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidArgument(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def repo_root() -> Path:
    env = os.environ.get("QUANT_REPO_ROOT")
    if env:
        return Path(env)
    # services/quant-api/src/quant_api/deps.py -> repo root is parents[4]
    return Path(__file__).resolve().parents[4]


def data_root() -> Path:
    env = os.environ.get("QUANT_DATA_ROOT")
    return Path(env) if env else repo_root() / "data" / "quant-lake"


def state_dir() -> Path:
    env = os.environ.get("QUANT_STATE_DIR")
    return Path(env) if env else data_root().parent / "state"


_lock = threading.Lock()
_lake: Any = None
_lake_overrides: dict[str, Any] = {}
_engine_overrides: dict[str, Any] = {}


def set_overrides(*, lake: Any = None, engines: dict[str, Any] | None = None) -> None:
    """Test hook: inject a lake and/or engine modules."""
    global _lake
    with _lock:
        _lake = lake
        _lake_overrides.clear()
        _engine_overrides.clear()
        if engines:
            _engine_overrides.update(engines)


def reset() -> None:
    set_overrides(lake=None, engines=None)


def get_lake() -> Any:
    global _lake
    with _lock:
        if _lake is not None:
            return _lake
        try:
            mod = importlib.import_module("quant_core.lake")
        except ImportError as exc:
            raise ServiceUnavailable(
                f"quant_core.lake is not importable yet ({exc}). quant-lake worker output is required.",
                code="DATA_STALE",
            ) from exc
        cls = getattr(mod, "QuantLake", None)
        if cls is None:
            raise ServiceUnavailable("quant_core.lake does not export QuantLake.", code="DATA_STALE")
        root = data_root()
        try:
            _lake = cls(root)
        except Exception as exc:
            raise ServiceUnavailable(f"QuantLake init failed at {root}: {exc}. {SEED_HINT}") from exc
        return _lake


def engine(name: str) -> ModuleType | Any:
    if name in _engine_overrides:
        return _engine_overrides[name]
    path = ENGINE_MODULES[name]
    try:
        return importlib.import_module(path)
    except ImportError as exc:
        raise ServiceUnavailable(
            f"engine module {path} is not importable yet ({exc}). quant-engines worker output is required.",
            code="DATA_STALE",
        ) from exc


def latest_snapshot_or_none() -> Any:
    lake = get_lake()
    try:
        return lake.get_latest_snapshot()
    except Exception:  # noqa: BLE001
        return None


def require_snapshot(snapshot_id: str | None = None) -> Any:
    """Return the requested (or latest) DatasetSnapshot, or raise with the `make seed` hint."""
    lake = get_lake()
    try:
        if snapshot_id:
            for s in lake.list_snapshots():
                if s.snapshot_id == snapshot_id:
                    return s
            raise NotFound(f"snapshot {snapshot_id} not found")
        snap = lake.get_latest_snapshot()
    except (NotFound, ServiceUnavailable):
        raise
    except Exception as exc:
        raise ServiceUnavailable(f"{SEED_HINT} ({type(exc).__name__}: {exc})") from exc
    if snap is None:
        raise ServiceUnavailable(SEED_HINT)
    return snap
