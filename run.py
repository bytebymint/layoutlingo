import os
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


def _restart_in_project_venv():
    """Ensure `python run.py` uses the runtime that contains offline translation."""
    project_root = Path(__file__).resolve().parent
    venv_python = project_root / '.venv' / 'Scripts' / 'python.exe'
    if os.name != 'nt' or not venv_python.is_file():
        return
    try:
        already_using_venv = Path(sys.executable).resolve() == venv_python.resolve()
    except OSError:
        already_using_venv = False
    if already_using_venv or os.environ.get('DOCINTEL_VENV_REEXEC') == '1':
        return
    environment = os.environ.copy()
    environment['DOCINTEL_VENV_REEXEC'] = '1'
    # subprocess handles Windows quoting for the spaces in this project path.
    result = subprocess.run(
        [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        env=environment,
    )
    raise SystemExit(result.returncode)


_restart_in_project_venv()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)


def _backup_sqlite_database():
    """Create a consistent startup snapshot before migrations or workers run."""
    load_dotenv()
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///database/app.db')
    if not database_url.startswith('sqlite:///'):
        return None

    raw_path = database_url[len('sqlite:///'):]
    if raw_path in {'', ':memory:'}:
        return None
    source_path = Path(raw_path)
    if not source_path.is_absolute():
        source_path = Path(__file__).resolve().parent / source_path
    if not source_path.is_file():
        return None

    with sqlite3.connect(f'file:{source_path}?mode=ro', uri=True) as source:
        has_documents = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'"
        ).fetchone()
        if not has_documents:
            logging.warning('Skipping SQLite startup backup because the schema is missing.')
            return None

        backup_dir = source_path.parent / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination_path = backup_dir / (
            f'{source_path.stem}-{datetime.now():%Y%m%d-%H%M%S}.db'
        )
        with sqlite3.connect(destination_path) as destination:
            source.backup(destination)

    backups = sorted(
        backup_dir.glob(f'{source_path.stem}-*.db'),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale_backup in backups[10:]:
        stale_backup.unlink(missing_ok=True)
    logging.info('Created SQLite startup backup at %s.', destination_path)
    return destination_path


_backup_sqlite_database()

from app import create_app

# Create the application instance
app = create_app()

if app.config.get('TRANSLATION_WORKER_MODE', 'inline') == 'inline':
    from app.routes.api import process_translation_async
    from app.services.background_worker import resume_translation_jobs

    resume_translation_jobs(app, process_translation_async)

if __name__ == '__main__':
    # Get port from environment or default to 5000
    debug = os.environ.get('FLASK_DEBUG', '0').strip().lower() in {'1', 'true', 'yes'}
    port = int(os.environ.get('PORT', 5000))
    # Localhost is the safe default. LAN exposure must be an explicit choice.
    host = os.environ.get('HOST', '127.0.0.1').strip() or '127.0.0.1'
    app.run(host=host, port=port, debug=debug)
