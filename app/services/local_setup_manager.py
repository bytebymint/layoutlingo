"""Manage the explicit first-run local AI installation without exposing a terminal."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path


_DEFAULT_ROOT = r'C:\LayoutLingo-LocalAI'
_ESTIMATED_DOWNLOAD_BYTES = 8 * 1024 ** 3
_RECOMMENDED_FREE_BYTES = 16 * 1024 ** 3
_lock = threading.RLock()
_process: subprocess.Popen | None = None
_state = {
    'state': 'idle', 'phase': 'Not installed', 'message': 'Local AI has not been installed yet.',
    'root': _DEFAULT_ROOT, 'progress_percent': 0, 'started_at': None,
    'completed_at': None, 'error': None, 'log_tail': [],
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_root(value: str | None) -> str:
    root = os.path.abspath(os.path.expanduser((value or _DEFAULT_ROOT).strip()))
    drive, _ = os.path.splitdrive(root)
    return root


def _disk_details(root: str) -> dict:
    drive, _ = os.path.splitdrive(root)
    probe = f'{drive}{os.sep}' if drive else root
    try:
        usage = shutil.disk_usage(probe)
        return {'free_bytes': usage.free, 'total_bytes': usage.total}
    except OSError:
        return {'free_bytes': None, 'total_bytes': None}


def _installation_snapshot(root: str) -> dict:
    aya_model = os.path.join(root, 'models', 'aya-expanse-8b-Q4_K_M.gguf')
    fast_root = os.path.join(root, 'models', 'nllb-200-distilled-600m-ct2-int8')
    fast_ready = all(os.path.isfile(os.path.join(fast_root, name)) for name in (
        'model.bin', 'config.json', 'tokenizer.json',
    ))
    return {
        'aya_ready': os.path.isfile(aya_model),
        'fast_ready': fast_ready,
        'launcher_ready': os.path.isfile(os.path.join(root, 'start-local-ai.ps1')),
    }


def _phase_for(root: str) -> tuple[str, int, str]:
    installed = _installation_snapshot(root)
    if not installed['aya_ready']:
        return 'Downloading quality reviewer', 28, 'Downloading the local Aya quality reviewer.'
    if not installed['launcher_ready']:
        return 'Preparing local launcher', 55, 'Creating the safe local start and stop controls.'
    if not installed['fast_ready']:
        return 'Preparing fast translator', 76, 'Downloading and converting NLLB for fast offline drafts.'
    return 'Validating installation', 94, 'Checking the local models and runtime files.'


def _append_log(line: str) -> None:
    line = line.strip()
    if not line:
        return
    with _lock:
        _state['log_tail'] = [*_state['log_tail'], line][-8:]


def _apply_runtime_root(root: str) -> None:
    os.environ['LOCAL_LLM_ROOT'] = root
    os.environ['FAST_TRANSLATION_MODEL_PATH'] = os.path.join(
        root, 'models', 'nllb-200-distilled-600m-ct2-int8'
    )


def _watch_installation(root: str, process: subprocess.Popen) -> None:
    for raw_line in iter(process.stdout.readline, ''):
        _append_log(raw_line)
        phase, percent, message = _phase_for(root)
        with _lock:
            _state.update({'phase': phase, 'progress_percent': percent, 'message': message})
    result = process.wait()
    installed = _installation_snapshot(root)
    with _lock:
        if result == 0 and all(installed.values()):
            _state.update({
                'state': 'complete', 'phase': 'Installation complete',
                'progress_percent': 100,
                'message': 'Your private translation tools are installed and ready to start.',
                'completed_at': _iso_now(), 'error': None,
            })
            _apply_runtime_root(root)
        else:
            _state.update({
                'state': 'failed', 'phase': 'Installation needs attention',
                'message': 'The local AI installation did not finish.',
                'error': 'Check the installation details below, then try again.',
                'completed_at': _iso_now(),
            })


def setup_status(root: str | None = None) -> dict:
    selected_root = _normalise_root(root or _state.get('root'))
    disk = _disk_details(selected_root)
    installed = _installation_snapshot(selected_root)
    with _lock:
        snapshot = dict(_state)
        snapshot['log_tail'] = list(_state['log_tail'])
    if snapshot['state'] in {'idle', 'complete'} and not all(installed.values()):
        snapshot.update({
            'state': 'ready_to_install', 'phase': 'Setup required',
            'message': 'Choose a drive and install the private translation tools.',
            'progress_percent': 0,
        })
    snapshot.update({
        'root': selected_root, 'disk': disk, 'installed': installed,
        'estimated_download_bytes': _ESTIMATED_DOWNLOAD_BYTES,
        'recommended_free_bytes': _RECOMMENDED_FREE_BYTES,
    })
    return snapshot


def start_setup(root: str, *, license_accepted: bool, python_executable: str) -> dict:
    if not license_accepted:
        raise ValueError('Accept the Aya and NLLB model terms before downloading local AI.')
    selected_root = _normalise_root(root)
    disk = _disk_details(selected_root)
    if disk['free_bytes'] is not None and disk['free_bytes'] < _RECOMMENDED_FREE_BYTES:
        raise ValueError('The selected drive needs at least 16 GB free for models and working space.')
    with _lock:
        if _process is not None and _process.poll() is None:
            return setup_status(selected_root)
        project_root = Path(__file__).resolve().parents[2]
        installer = project_root / 'scripts' / 'local_ai' / 'install-local-ai.ps1'
        if not installer.is_file():
            raise RuntimeError('The bundled local AI installer is missing.')
        creation_flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        _state.update({
            'state': 'installing', 'phase': 'Preparing installation',
            'message': 'Preparing a private local AI installation.', 'root': selected_root,
            'progress_percent': 4, 'started_at': _iso_now(), 'completed_at': None,
            'error': None, 'log_tail': [],
        })
        _process = subprocess.Popen(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(installer), '-Root', selected_root],
            cwd=str(project_root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', creationflags=creation_flags,
        )
        threading.Thread(target=_watch_installation, args=(selected_root, _process), daemon=True).start()
    return setup_status(selected_root)
