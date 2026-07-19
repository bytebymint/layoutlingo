import concurrent.futures
import logging
import os

# Configure a module‑level ThreadPoolExecutor for background tasks.
# Adjust max_workers based on anticipated load; 4 is a reasonable default.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get('BACKGROUND_WORKERS', '4'))),
    thread_name_prefix='document-worker',
)
_translation_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(1, int(os.environ.get('TRANSLATION_WORKERS', '2'))),
    thread_name_prefix='translation-worker',
)


def run_async(func, *args, **kwargs):
    """Submit a callable to the thread pool and return the Future.

    The caller can attach callbacks or inspect ``future.result()`` later.
    Any exception raised inside ``func`` will be captured by the Future.
    """
    try:
        future = _executor.submit(func, *args, **kwargs)
        return future
    except Exception as e:
        logging.exception("Failed to submit async task: %s", e)
        raise


def run_translation_async(func, *args, **kwargs):
    """Submit long translations without starving document-processing workers."""
    try:
        return _translation_executor.submit(func, *args, **kwargs)
    except Exception as exc:
        logging.exception("Failed to submit translation task: %s", exc)
        raise


def resume_translation_jobs(app_instance, process_func):
    """Requeue pending and expired translation leases after an inline-worker restart."""
    from datetime import datetime
    from sqlalchemy import or_
    from app.models.document import DocumentTranslation

    with app_instance.app_context():
        now = datetime.utcnow()
        job_ids = [
            job_id
            for (job_id,) in app_instance.extensions['sqlalchemy'].session.query(
                DocumentTranslation.id
            ).filter(
                DocumentTranslation.cancel_requested.is_(False),
                or_(
                    DocumentTranslation.status == 'Pending',
                    (DocumentTranslation.status == 'Processing') & (
                        (DocumentTranslation.lease_expires_at.is_(None))
                        | (DocumentTranslation.lease_expires_at <= now)
                    ),
                ),
            ).all()
        ]

    for job_id in job_ids:
        run_translation_async(process_func, app_instance, job_id)
    return len(job_ids)
