"""Durable database-backed translation worker.

Run one or more instances with ``python translation_worker.py`` when
``TRANSLATION_WORKER_MODE=external``. Atomic database leases prevent two
workers from processing the same job.
"""

import argparse
import logging
import signal
import time
from datetime import datetime

from sqlalchemy import or_

from app import create_app, db
from app.models.document import DocumentTranslation
from app.routes.api import process_translation_async


logger = logging.getLogger(__name__)
_shutdown_requested = False


def _request_shutdown(_signum, _frame):
    global _shutdown_requested
    _shutdown_requested = True


def _next_job_id(app_instance):
    with app_instance.app_context():
        now = datetime.utcnow()
        row = db.session.query(DocumentTranslation.id).filter(
            DocumentTranslation.cancel_requested.is_(False),
            or_(
                DocumentTranslation.status == 'Pending',
                (DocumentTranslation.status == 'Processing') & (
                    DocumentTranslation.lease_expires_at.is_(None)
                    | (DocumentTranslation.lease_expires_at <= now)
                ),
            ),
        ).order_by(DocumentTranslation.created_at.asc()).first()
        return row[0] if row else None


def run_worker(once=False):
    app = create_app()
    poll_seconds = max(0.25, float(app.config.get('TRANSLATION_POLL_SECONDS', 2)))
    logger.info('Translation worker started (poll interval %.2fs).', poll_seconds)

    while not _shutdown_requested:
        job_id = _next_job_id(app)
        if job_id is None:
            if once:
                return
            time.sleep(poll_seconds)
            continue

        process_translation_async(app, job_id)
        if once:
            return


def main():
    parser = argparse.ArgumentParser(description='Run the durable translation worker.')
    parser.add_argument('--once', action='store_true', help='Process at most one available job.')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)
    run_worker(once=args.once)


if __name__ == '__main__':
    main()
