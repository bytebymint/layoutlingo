"""Start and stop the fixed, D-drive local AI runtime safely."""

import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from app.services.fast_translation_api import fast_translation_activity, fast_translation_status
from app.services.local_llm_api import local_llm_activity, local_llm_status


_DEFAULT_ROOT = r'D:\DocIntel-LocalAI'
_manager_lock = threading.RLock()
_launcher_process = None
_last_action = None
_last_action_at = None
_last_error = None
_health_cache = {'checked_at': 0.0, 'aya': None, 'fast': None}


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _root() -> str:
    return os.path.abspath(os.environ.get('LOCAL_LLM_ROOT', _DEFAULT_ROOT))


def _start_script() -> str:
    return os.path.join(_root(), 'start-local-ai.ps1')


def _stop_script() -> str:
    return os.path.join(_root(), 'stop-local-ai.ps1')


def _pid_file() -> str:
    return os.path.join(_root(), 'config', 'llama-server.pid')


def _read_pid():
    try:
        with open(_pid_file(), 'r', encoding='ascii') as pid_file:
            return int(pid_file.read().strip())
    except (OSError, TypeError, ValueError):
        return None


def _engine_snapshot() -> tuple[dict, dict]:
    """Reuse short-lived health data while keeping live work telemetry current."""
    now = time.monotonic()
    with _manager_lock:
        cached_aya = _health_cache.get('aya')
        cached_fast = _health_cache.get('fast')
        fresh = (
            cached_aya is not None
            and cached_fast is not None
            and now - _health_cache['checked_at'] < 3.0
        )
    if fresh:
        aya = dict(cached_aya)
        fast = dict(cached_fast)
        aya['activity'] = local_llm_activity()
        fast['activity'] = fast_translation_activity()
        return aya, fast

    aya = local_llm_status(timeout_seconds=0.8)
    fast = fast_translation_status()
    with _manager_lock:
        _health_cache.update({'checked_at': now, 'aya': dict(aya), 'fast': dict(fast)})
    return aya, fast


def _invalidate_health_cache():
    with _manager_lock:
        _health_cache.update({'checked_at': 0.0, 'aya': None, 'fast': None})


def local_engine_control_status() -> dict:
    """Return operational state without exposing terminal-oriented details."""
    global _launcher_process, _last_error
    aya, fast = _engine_snapshot()
    with _manager_lock:
        launcher_active = bool(
            _launcher_process is not None and _launcher_process.poll() is None
        )
        if _launcher_process is not None and not launcher_active:
            if _launcher_process.returncode not in {None, 0}:
                _last_error = 'The local quality reviewer did not finish starting.'
            _launcher_process = None
        state = 'ready' if aya.get('available') else ('starting' if launcher_active else 'off')
        return {
            'state': state,
            'enabled': bool(aya.get('available')),
            'starting': launcher_active,
            'pid': _read_pid() if aya.get('available') else None,
            'last_action': _last_action,
            'last_action_at': _last_action_at,
            'last_error': _last_error,
            'aya': aya,
            'fast': fast,
        }


def start_local_engines() -> dict:
    """Launch the fixed PowerShell bootstrap asynchronously."""
    global _launcher_process, _last_action, _last_action_at, _last_error
    current = local_llm_status(timeout_seconds=0.8)
    if current.get('available'):
        return local_engine_control_status()

    start_script = _start_script()
    if not os.path.isfile(start_script):
        raise RuntimeError('Local AI is not installed yet. Use Set up local AI on the Quality Dashboard.')

    with _manager_lock:
        if _launcher_process is not None and _launcher_process.poll() is None:
            return local_engine_control_status()
        creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        _launcher_process = subprocess.Popen(
            [
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-File',
                start_script,
            ],
            cwd=_root(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        _last_action = 'start'
        _last_action_at = _iso_now()
        _last_error = None
    _invalidate_health_cache()
    return local_engine_control_status()


def stop_local_engines() -> dict:
    """Stop only the verified D-drive llama.cpp process."""
    global _launcher_process, _last_action, _last_action_at, _last_error
    stop_script = _stop_script()
    if not os.path.isfile(stop_script):
        raise RuntimeError('The D-drive local AI stop file is missing.')

    with _manager_lock:
        if _launcher_process is not None and _launcher_process.poll() is None:
            _launcher_process.terminate()
            _launcher_process = None

    creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    result = subprocess.run(
        [
            'powershell.exe',
            '-NoProfile',
            '-NonInteractive',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            stop_script,
        ],
        cwd=_root(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=creation_flags,
    )
    if result.returncode != 0:
        with _manager_lock:
            _last_error = 'The local quality reviewer could not be stopped cleanly.'
        raise RuntimeError(_last_error)

    with _manager_lock:
        _last_action = 'stop'
        _last_action_at = _iso_now()
        _last_error = None
    _invalidate_health_cache()
    return local_engine_control_status()
