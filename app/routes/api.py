import os
import re
import uuid
import logging
import threading
from datetime import datetime
from flask import Blueprint, abort, request, jsonify, current_app, send_from_directory, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename
from app.models.document import (
    ChatMessage,
    Document,
    DocumentChunk,
    DocumentComparison,
    DocumentTranslation,
    TranslationGlossaryTerm,
    TranslationReviewIssue,
)
from app.services.ocr_service import process_document_ocr
from app.services.document_classifier import classify_document
from app.services.information_extractor import extract_document_info
from app.services.embedding_service import chunk_text, generate_embedding
from app.services.chat_service import answer_document_query
from app.services.background_worker import run_async, run_translation_async
from app import db

logger = logging.getLogger(__name__)
api_bp = Blueprint('api', __name__)


@api_bp.before_request
def require_api_login():
    """Keep document data private outside the isolated test application."""
    if current_app.testing:
        return None
    if not current_user.is_authenticated:
        return jsonify({'status': 'error', 'message': 'Sign in is required.'}), 401
    return None


def _owned_document_or_404(document_id: int) -> Document:
    document = Document.query.get_or_404(document_id)
    if not current_app.testing and document.user_id != current_user.id:
        abort(404)
    return document


def _owned_translation_or_404(translation_id: int) -> DocumentTranslation:
    job = DocumentTranslation.query.get_or_404(translation_id)
    _owned_document_or_404(job.document_id)
    return job


def _request_user_id() -> int:
    """Keep legacy tests isolated while production always uses the signed-in user."""
    return current_user.id if current_user.is_authenticated else 1

# FreeModel is used as the AI backend through freemodel_api.py


def _freemodel_api_keys(config):
    """Return configured provider keys in primary-to-backup order."""
    configured = config.get('FREEMODEL_API_KEYS')
    if configured:
        return configured
    return tuple(dict.fromkeys(filter(None, (
        (config.get('FREEMODEL_API_KEY') or '').strip(),
        (config.get('FREEMODEL_API_KEY_2') or '').strip(),
        (config.get('FREEMODEL_API_KEY_3') or '').strip(),
    ))))


def _translation_progress_percent(job: DocumentTranslation) -> int:
    """Return phase-aware progress for the translation UI without schema churn."""
    status = job.status or 'Pending'
    total = max(0, job.total_pages or 0)
    current = max(0, job.current_page or 0)
    message = (job.status_message or '').lower()

    if status == 'Completed':
        return 100
    if status in {'Failed', 'Cancelled', 'NeedsReview'}:
        if total:
            return max(1, min(99, round((current / total) * 100)))
        return 0
    if status == 'Pending':
        return 1
    if total <= 0:
        return 2

    page_match = re.search(r'page\s+(\d+)\s+of\s+(\d+)', message)
    phase_page = int(page_match.group(1)) if page_match else current
    phase_total = int(page_match.group(2)) if page_match else total
    phase_ratio = min(1.0, max(0.0, phase_page / max(1, phase_total)))

    if 'opening pdf' in message or 'preparing layout' in message:
        return 2
    if 'analyzing document layout' in message:
        return max(3, min(12, round(3 + phase_ratio * 9)))
    if 'choosing translation profile' in message:
        return 13
    if 'building translation brief' in message:
        return 17
    if 'extracting literary paragraphs' in message:
        return max(14, min(19, round(14 + phase_ratio * 5)))
    if 'building ai story bible' in message:
        return 20
    if 'translating page' in message:
        completed_pages = max(0, (phase_page - 1))
        batch_match = re.search(r'batch\s+(\d+)\s+of\s+(\d+)', message)
        within_page = 0.0
        if batch_match:
            batch_current = int(batch_match.group(1))
            batch_total = max(1, int(batch_match.group(2)))
            within_page = min(1.0, max(0.0, batch_current / batch_total))
            if 'drafting' in message:
                within_page = min(1.0, max(0.0, (batch_current - 1) / batch_total))
        document_ratio = min(
            1.0,
            max(0.0, (completed_pages + within_page) / max(1, phase_total)),
        )
        return max(14, min(92, round(14 + document_ratio * 78)))
    if 'ai-translated and edited' in message:
        return max(21, min(82, round(21 + phase_ratio * 61)))
    if 'running automated book qa' in message:
        return 84
    recovery_match = re.search(r'recovering literary review\s+(\d+)\s+of\s+(\d+)', message)
    if recovery_match:
        recovery_current = int(recovery_match.group(1))
        recovery_total = max(1, int(recovery_match.group(2)))
        recovery_ratio = min(1.0, max(0.0, recovery_current / recovery_total))
        return max(91, min(97, round(91 + recovery_ratio * 6)))
    if 'preparing targeted literary recovery' in message:
        return 90
    if 'rendered translated page' in message:
        return max(85, min(96, round(85 + phase_ratio * 11)))
    if 'translated page' in message:
        return max(14, min(92, round(14 + phase_ratio * 78)))
    if 'optimizing and saving' in message:
        return 98
    return max(2, min(99, round((current / total) * 100)))


def _translation_progress_stage(job: DocumentTranslation) -> dict:
    """Return stable UI stage metadata derived from the durable status message."""
    status = job.status or 'Pending'
    message = (job.status_message or '').lower()
    if status == 'Completed':
        return {'key': 'complete', 'label': 'Translation complete'}
    if status == 'Failed':
        return {'key': 'failed', 'label': 'Translation stopped'}
    if status == 'NeedsReview':
        return {'key': 'review', 'label': 'Your quality decision is needed'}
    if status == 'Cancelled':
        return {'key': 'cancelled', 'label': 'Translation cancelled'}
    if status == 'Pending':
        return {'key': 'queued', 'label': 'Queued for local engine'}
    stages = (
        ('analyzing document layout', 'layout', 'Reading document layout'),
        ('choosing translation profile', 'routing', 'Choosing translation profile'),
        ('building translation brief', 'planning', 'Building terminology plan'),
        ('building ai story bible', 'planning', 'Building story and style memory'),
        ('extracting literary paragraphs', 'layout', 'Extracting literary structure'),
        ('translating page', 'translation', 'Local AI translating and reviewing'),
        ('ai-translated and edited', 'translation', 'Translating and editing'),
        ('preparing targeted literary recovery', 'quality', 'Preparing targeted literary recovery'),
        ('recovering literary review', 'quality', 'Recovering a failed literary review'),
        ('checking completed book', 'quality', 'Recovering failed literary reviews'),
        ('running automated book qa', 'quality', 'Running publication checks'),
        ('running publication qa', 'quality', 'Running publication checks'),
        ('rendered translated page', 'rendering', 'Rendering translated pages'),
        ('translated page', 'rendering', 'Rendering translated pages'),
        ('optimizing and saving', 'saving', 'Optimizing final PDF'),
        ('opening pdf', 'preparing', 'Preparing translation workspace'),
    )
    for needle, key, label in stages:
        if needle in message:
            return {'key': key, 'label': label}
    return {'key': 'working', 'label': 'Translation in progress'}


def _actionable_review_issues(issues: list[dict]) -> list[dict]:
    return [
        issue for issue in issues
        if str(issue.get('severity') or '').lower() in {'error', 'critical'}
        and str(issue.get('source_excerpt') or '').strip()
        and str(issue.get('status') or 'open').lower() == 'open'
    ]


def _resolve_nontranslatable_review_noise(job: DocumentTranslation) -> bool:
    """Clear legacy QA issues created for punctuation-only layout markers."""
    if job.status != 'NeedsReview' or not job.checkpoint_path \
            or not os.path.isfile(job.checkpoint_path):
        return False

    from datetime import datetime
    from app.services.translation_service import _is_non_translatable_value

    open_issues = job.review_issues.filter_by(status='open').all()
    resolved = 0
    for issue in open_issues:
        if issue.category == 'untranslated_text' and _is_non_translatable_value(
            issue.source_excerpt or ''
        ):
            issue.status = 'resolved'
            resolved += 1
    if not resolved:
        return False

    remaining = _actionable_review_issues([
        issue.to_dict() for issue in open_issues if issue.status == 'open'
    ])
    if not remaining:
        job.status = 'Pending'
        job.status_message = (
            'Layout-only review noise cleared. Translation queued to resume...'
        )
        job.error_message = None
        job.cancel_requested = False
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_heartbeat_at = datetime.utcnow()
    else:
        job.status_message = (
            f'Your decision is needed for {len(remaining)} failed '
            f'passage{"" if len(remaining) == 1 else "s"}.'
        )
    db.session.commit()
    logger.info(
        '[REVIEW] Cleared %s punctuation-only issue(s) from translation %s.',
        resolved,
        job.id,
    )
    return not remaining


def _translation_lease_expired(job: DocumentTranslation, now) -> bool:
    return bool(
        job.status == 'Processing'
        and job.lease_expires_at
        and job.lease_expires_at <= now
    )


def _release_expired_translation_lease(job: DocumentTranslation, now) -> bool:
    if not _translation_lease_expired(job, now):
        return False
    job.status = 'Pending'
    job.status_message = 'Previous worker stopped; translation queued to resume...'
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_heartbeat_at = now
    db.session.commit()
    return True


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


def valid_file_signature(upload):
    """Verify that the file header matches its accepted extension."""
    extension = upload.filename.rsplit('.', 1)[1].lower()
    header = upload.stream.read(12)
    upload.stream.seek(0)
    signatures = {
        'pdf': (b'%PDF-',),
        'png': (b'\x89PNG\r\n\x1a\n',),
        'jpg': (b'\xff\xd8\xff',),
        'jpeg': (b'\xff\xd8\xff',),
    }
    return any(header.startswith(signature) for signature in signatures.get(extension, ()))


def process_document_async(app_instance, doc_id):
    """
    Background worker: runs OCR, classification, extraction, and embedding.
    The overall deadline is configurable and must allow provider-key failover.
    """
    import time

    with app_instance.app_context():
        gemini_api_key = app_instance.config.get('GEMINI_API_KEY', '')
        freemodel_api_key = _freemodel_api_keys(app_instance.config)
        doc = Document.query.get(doc_id)
        if not doc:
            logger.error(f"Async processing: Document ID {doc_id} not found.")
            return

        start_time = time.monotonic()
        timeout_seconds = max(
            60,
            int(app_instance.config.get('DOCUMENT_PROCESSING_TIMEOUT_SECONDS', 600)),
        )

        try:
            logger.info(f"[START] Processing document ID {doc_id} — {doc.original_filename}")
            doc.status = 'Processing'
            db.session.commit()

            logger.info("Using FreeModel API for document processing")

            # Timeout helper
            def check_timeout():
                elapsed = time.monotonic() - start_time
                if elapsed > timeout_seconds:
                    raise TimeoutError(f"Processing exceeded {timeout_seconds}s time limit.")

            # 1. OCR / Text Extraction
            check_timeout()
            ocr_text, confidence = process_document_ocr(
                doc.file_path, doc.original_filename, api_key=gemini_api_key
            )
            doc.ocr_text = ocr_text
            doc.confidence_score = confidence
            db.session.commit()
            logger.info(f"OCR complete for doc {doc_id}. Characters extracted: {len(ocr_text or '')}")

            if not ocr_text or len(ocr_text.strip()) == 0:
                raise ValueError("No text could be extracted from this document.")

            # 2. Document Classification
            check_timeout()
            doc_type = classify_document(ocr_text, api_key=freemodel_api_key)
            doc.doc_type = doc_type
            db.session.commit()
            logger.info(f"Classification complete for doc {doc_id}: {doc_type}")

            # 3. Metadata / Information Extraction
            check_timeout()
            extracted_data = extract_document_info(
                ocr_text, doc_type, api_key=freemodel_api_key
            )
            doc.parsed_extracted_data = extracted_data
            db.session.commit()
            logger.info(f"Extraction complete for doc {doc_id}.")

            # 4. Text Chunking & Embeddings
            check_timeout()
            chunks = chunk_text(ocr_text)
            for idx, chunk_content in enumerate(chunks):
                check_timeout()
                emb_json = generate_embedding(chunk_content, api_key=gemini_api_key)
                chunk_record = DocumentChunk(
                    document_id=doc.id,
                    chunk_index=idx,
                    text_content=chunk_content,
                    embedding=emb_json
                )
                db.session.add(chunk_record)

            doc.status = 'Completed'
            db.session.commit()
            elapsed = round(time.monotonic() - start_time, 2)
            logger.info(f"[DONE] Document ID {doc_id} processed successfully in {elapsed}s.")

        except Exception as e:
            db.session.rollback()
            elapsed = round(time.monotonic() - start_time, 2)
            logger.exception(f"[FAIL] Document ID {doc_id} failed after {elapsed}s: {str(e)}")
            try:
                doc.status = 'Failed'
                db.session.commit()
            except Exception:
                db.session.rollback()


def _claim_translation_job(translation_id, worker_id, lease_seconds):
    """Atomically lease a pending or abandoned job to one worker."""
    from datetime import datetime, timedelta
    from sqlalchemy import and_, or_

    now = datetime.utcnow()
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    updated = DocumentTranslation.query.filter(
        DocumentTranslation.id == translation_id,
        DocumentTranslation.cancel_requested.is_(False),
        or_(
            DocumentTranslation.status == 'Pending',
            and_(
                DocumentTranslation.status == 'Processing',
                or_(
                    DocumentTranslation.lease_expires_at.is_(None),
                    DocumentTranslation.lease_expires_at <= now,
                ),
            ),
        ),
    ).update({
        DocumentTranslation.status: 'Processing',
        DocumentTranslation.status_message: 'Preparing translation...',
        DocumentTranslation.lease_owner: worker_id,
        DocumentTranslation.lease_expires_at: lease_expires_at,
        DocumentTranslation.last_heartbeat_at: now,
        DocumentTranslation.started_at: now,
        DocumentTranslation.attempt_count: db.func.coalesce(
            DocumentTranslation.attempt_count, 0
        ) + 1,
        DocumentTranslation.error_message: None,
    }, synchronize_session=False)
    db.session.commit()
    if not updated:
        return None
    return db.session.get(DocumentTranslation, translation_id)


def _renew_translation_lease(translation_id, worker_id, lease_seconds) -> bool:
    """Extend an active worker lease without changing user-visible progress."""
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    updated = DocumentTranslation.query.filter(
        DocumentTranslation.id == translation_id,
        DocumentTranslation.status == 'Processing',
        DocumentTranslation.cancel_requested.is_(False),
        DocumentTranslation.lease_owner == worker_id,
    ).update({
        DocumentTranslation.last_heartbeat_at: now,
        DocumentTranslation.lease_expires_at: now + timedelta(seconds=lease_seconds),
    }, synchronize_session=False)
    db.session.commit()
    return bool(updated)


def process_translation_async(app_instance, translation_id):
    """Lease and process one durable PDF translation job."""
    from datetime import datetime, timedelta
    from app.services.translation_service import (
        TranslationCancelled,
        TranslationItemQualityError,
        create_translation_team_context,
        translate_and_render_pdf,
    )
    from app.services.translation_memory_service import (
        knowledge_signature,
        load_translation_resources,
        persist_discovered_entities,
        persist_translation_memory,
        replace_review_issues,
        save_glossary_entries,
    )

    worker_id = f'{os.getpid()}-{uuid.uuid4().hex}'
    lease_seconds = max(60, int(app_instance.config.get('TRANSLATION_LEASE_SECONDS', 300)))

    with app_instance.app_context():
        job = _claim_translation_job(translation_id, worker_id, lease_seconds)
        if not job:
            logger.info('Translation %s is already leased, cancelled, or terminal.', translation_id)
            return

        doc = db.session.get(Document, job.document_id)
        if not doc:
            job.status = 'Failed'
            job.error_message = f'Document ID {job.document_id} not found.'
            job.lease_owner = None
            job.lease_expires_at = None
            db.session.commit()
            return

        ai_api_key = _freemodel_api_keys(app_instance.config)
        translations_dir = os.path.join(app_instance.config['UPLOAD_FOLDER'], 'translations')
        os.makedirs(translations_dir, exist_ok=True)
        base_name = os.path.splitext(secure_filename(doc.original_filename))[0] or f'document_{doc.id}'
        output_filename = f'{base_name}_translated_{job.target_language}_{job.id}.pdf'
        output_path = os.path.join(translations_dir, output_filename)
        checkpoint_path = f'{output_path}.checkpoint.json'
        job.checkpoint_path = checkpoint_path
        db.session.commit()

        heartbeat_stop = threading.Event()
        heartbeat_interval = max(10.0, min(60.0, lease_seconds / 3))

        def heartbeat_worker():
            while not heartbeat_stop.wait(heartbeat_interval):
                with app_instance.app_context():
                    try:
                        if not _renew_translation_lease(
                            translation_id,
                            worker_id,
                            lease_seconds,
                        ):
                            return
                    except Exception as exc:
                        db.session.rollback()
                        logger.warning(
                            'Failed to renew translation %s lease: %s',
                            translation_id,
                            exc,
                        )
                    finally:
                        db.session.remove()

        heartbeat_thread = threading.Thread(
            target=heartbeat_worker,
            name=f'translation-heartbeat-{translation_id}',
            daemon=True,
        )
        heartbeat_thread.start()

        def progress_cb(current, total, message):
            try:
                db.session.rollback()
                current_job = db.session.get(DocumentTranslation, translation_id)
                if current_job and current_job.lease_owner == worker_id:
                    now = datetime.utcnow()
                    current_job.current_page = current
                    current_job.total_pages = total
                    current_job.status_message = message
                    current_job.last_heartbeat_at = now
                    current_job.lease_expires_at = now + timedelta(seconds=lease_seconds)
                    db.session.commit()
            except Exception as exc:
                db.session.rollback()
                logger.warning('Failed to update translation progress: %s', exc)

        def cancel_cb():
            db.session.rollback()
            current_job = db.session.get(DocumentTranslation, translation_id)
            return bool(
                not current_job
                or current_job.cancel_requested
                or current_job.lease_owner != worker_id
            )

        try:
            logger.info('[START] Translation %s for document %s', translation_id, doc.id)
            quality_result = {}
            resources = load_translation_resources(
                doc.user_id,
                job.source_language,
                job.target_language,
                job.domain or 'auto',
            )
            team_context = create_translation_team_context(
                job.source_language,
                job.target_language,
                resources=resources,
                domain=job.domain or 'auto',
                quality_level=job.quality_level or 'professional',
                enable_back_translation=bool(job.enable_back_translation),
                provider_mode=job.provider_mode or 'online',
                provider_model=job.provider_model,
                translation_id=job.id,
                document_name=doc.original_filename,
            )
            translated_text = translate_and_render_pdf(
                doc.file_path,
                job.source_language,
                job.target_language,
                output_path,
                api_key=ai_api_key,
                progress_callback=progress_cb,
                cancel_callback=cancel_cb,
                checkpoint_path=checkpoint_path,
                translation_mode=job.translation_mode or 'auto',
                quality_callback=quality_result.update,
                team_context=team_context,
            )

            db.session.rollback()
            completed_job = db.session.get(DocumentTranslation, translation_id)
            if completed_job and completed_job.lease_owner == worker_id:
                persist_translation_memory(
                    doc.user_id,
                    job.source_language,
                    job.target_language,
                    team_context.detected_domain,
                    team_context.export_segments(),
                    quality_result.get('score'),
                )
                persist_discovered_entities(
                    doc.user_id,
                    job.source_language,
                    job.target_language,
                    team_context.detected_domain,
                    team_context.brief.get('entities') or [],
                )
                save_glossary_entries(
                    doc.user_id,
                    job.source_language,
                    job.target_language,
                    team_context.detected_domain,
                    [{
                        'source_term': item.get('source', ''),
                        'target_term': item.get('target', ''),
                        'authority': item.get('authority', 'preferred'),
                        'notes': item.get('notes', ''),
                    } for item in team_context.brief.get('terms') or []],
                )
                replace_review_issues(
                    completed_job.id,
                    quality_result.get('issues') or [],
                )
                completed_job.knowledge_signature = knowledge_signature(
                    load_translation_resources(
                        doc.user_id,
                        job.source_language,
                        job.target_language,
                        team_context.detected_domain,
                    )
                )
                completed_job.translated_text = translated_text
                completed_job.translated_pdf_filename = output_filename
                completed_job.translated_pdf_path = output_path
                completed_job.status = 'Completed'
                completed_job.detected_mode = quality_result.get('mode')
                completed_job.quality_score = quality_result.get('score')
                completed_job.parsed_quality_report = quality_result
                completed_job.status_message = (
                    f"AI translation and QA completed ({quality_result.get('score', 0):.1f}/100)."
                    if quality_result
                    else 'Translation completed successfully.'
                )
                completed_job.completed_at = datetime.utcnow()
                completed_job.lease_owner = None
                completed_job.lease_expires_at = None
                completed_job.last_heartbeat_at = datetime.utcnow()
                completed_job.checkpoint_path = None
                db.session.commit()
            logger.info('[DONE] Translation %s completed.', translation_id)
        except TranslationCancelled as exc:
            db.session.rollback()
            cancelled_job = db.session.get(DocumentTranslation, translation_id)
            if cancelled_job and cancelled_job.lease_owner == worker_id:
                cancelled_job.status = 'Cancelled'
                cancelled_job.status_message = str(exc)
                cancelled_job.lease_owner = None
                cancelled_job.lease_expires_at = None
                db.session.commit()
            logger.info('[CANCELLED] Translation %s.', translation_id)
        except Exception as exc:
            rejected_report = getattr(exc, 'quality_report', None)
            recoverable_review = isinstance(exc, TranslationItemQualityError) or (
                isinstance(rejected_report, dict)
                and bool(_actionable_review_issues(rejected_report.get('issues') or []))
            )
            if recoverable_review:
                logger.warning(
                    '[REVIEW] Translation %s paused for a quality decision: %s',
                    translation_id,
                    exc,
                )
            else:
                logger.exception('[FAIL] Translation %s failed: %s', translation_id, exc)
            db.session.rollback()
            failed_job = db.session.get(DocumentTranslation, translation_id)
            if failed_job and failed_job.lease_owner == worker_id:
                review_payload = []
                if isinstance(rejected_report, dict):
                    failed_job.quality_score = rejected_report.get('score')
                    failed_job.parsed_quality_report = rejected_report
                    failed_job.detected_mode = rejected_report.get('mode')
                    review_payload = rejected_report.get('issues') or []
                elif isinstance(exc, TranslationItemQualityError):
                    review_payload = [exc.to_review_issue(
                        page_number=failed_job.current_page or None,
                    )]
                    failed_job.parsed_quality_report = {
                        'publication_ready': False,
                        'human_review_required': True,
                        'issues': review_payload,
                    }
                if review_payload:
                    replace_review_issues(failed_job.id, review_payload)
                checkpoint_saved = bool(
                    checkpoint_path and os.path.isfile(checkpoint_path)
                )
                actionable = _actionable_review_issues(review_payload)
                if checkpoint_saved and actionable:
                    failed_job.status = 'NeedsReview'
                    failed_job.status_message = (
                        f'Your decision is needed for {len(actionable)} failed '
                        f'passage{"" if len(actionable) == 1 else "s"}.'
                    )
                    failed_job.error_message = None
                else:
                    failed_job.status = 'Failed'
                    resume_note = (
                        ' Progress was checkpointed; start the same translation again to resume.'
                        if checkpoint_saved
                        else ''
                    )
                    failed_job.status_message = f'Translation failed: {exc}{resume_note}'
                    failed_job.error_message = f'{exc}{resume_note}'
                failed_job.lease_owner = None
                failed_job.lease_expires_at = None
                db.session.commit()
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=5)


@api_bp.route('/upload', methods=['POST'])
def upload_document():
    """Upload a document and queue it for async processing."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in the request'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected for uploading'}), 400

    if not allowed_file(file.filename):
        allowed = ", ".join(current_app.config['ALLOWED_EXTENSIONS'])
        return jsonify({'error': f'Unsupported file extension. Allowed: {allowed}'}), 400
    if not valid_file_signature(file):
        return jsonify({'error': 'File content does not match its extension.'}), 400

    try:
        orig_filename = file.filename
        ext = os.path.splitext(orig_filename)[1].lower()

        # Save file securely using UUID to prevent collisions
        secure_name = f"{uuid.uuid4()}{ext}"
        upload_dir = current_app.config['UPLOAD_FOLDER']
        file_path = os.path.join(upload_dir, secure_name)

        file.save(file_path)
        file_size = os.path.getsize(file_path)

        doc = Document(
            user_id=_request_user_id(),
            filename=secure_name,
            original_filename=orig_filename,
            file_path=file_path,
            file_type=ext.replace('.', ''),
            status='Pending',
            storage_size=file_size
        )
        db.session.add(doc)
        db.session.commit()

        # Submit to thread pool — non-blocking
        app_instance = current_app._get_current_object()
        run_async(process_document_async, app_instance, doc.id)

        return jsonify({
            'message': 'File uploaded successfully. Processing has started.',
            'document': doc.to_dict()
        }), 201

    except Exception as e:
        logger.exception(f"Upload failed: {str(e)}")
        return jsonify({'error': f'Upload failed: {str(e)}'}), 500


@api_bp.route('/document/<int:doc_id>/status', methods=['GET'])
def get_document_status(doc_id):
    """Poll the processing status of a document."""
    doc = _owned_document_or_404(doc_id)
    return jsonify({
        'id': doc.id,
        'status': doc.status,
        'doc_type': doc.doc_type,
        'confidence_score': doc.confidence_score,
        'extracted_data': doc.parsed_extracted_data,
        'original_filename': doc.original_filename,
    })


@api_bp.route('/document/<int:doc_id>/chat', methods=['POST'])
def chat_with_document(doc_id):
    """Send a question to a completed document via RAG chat."""
    doc = _owned_document_or_404(doc_id)

    if doc.status != 'Completed':
        return jsonify({'error': f'Document is {doc.status}. Please wait until processing completes.'}), 400

    data = request.get_json() or {}
    query = data.get('message', '').strip()
    if not query:
        return jsonify({'error': 'Message cannot be empty.'}), 400

    try:
        ai_api_key = _freemodel_api_keys(current_app.config)
        user_msg = ChatMessage(
            document_id=doc.id,
            user_id=None,
            sender='user',
            message=query
        )
        db.session.add(user_msg)
        db.session.commit()

        answer = answer_document_query(doc.id, query, api_key=ai_api_key)

        ai_msg = ChatMessage(
            document_id=doc.id,
            user_id=None,
            sender='ai',
            message=answer
        )
        db.session.add(ai_msg)
        db.session.commit()

        return jsonify({'answer': answer, 'message_id': ai_msg.id})

    except Exception as e:
        logger.exception(f"Chat failed for document {doc_id}: {str(e)}")
        return jsonify({'error': f'Chat failed: {str(e)}'}), 500

# New endpoint for autonomous document analysis
@api_bp.route('/document/<int:doc_id>/analyze', methods=['POST'])
def analyze_document_route(doc_id):
    """Generate a structured analysis report for a single document."""
    from app.services.analysis_service import analyze_document
    doc = _owned_document_or_404(doc_id)
    if not doc.ocr_text:
        return jsonify({'status': 'error', 'message': 'Document OCR text is empty.'}), 400
    try:
        report, err = analyze_document(doc_id, top_k=None)  # Retrieve all chunks
        if err:
            return jsonify({'status': 'error', 'message': err}), 500
        return jsonify({'status': 'success', 'analysis': report})
    except Exception as e:
        logger.exception(f"Analysis failed for document {doc_id}: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@api_bp.route('/translate', methods=['POST'])
def translate_document_route():
    from datetime import datetime
    from app.services.translation_service import (
        TRANSLATION_DOMAINS,
        get_language_label,
    )
    from app.services.translation_memory_service import (
        knowledge_signature,
        load_translation_resources,
        save_glossary_entries,
    )
    from app.services.fast_translation_api import fast_translation_status
    from app.services.local_llm_api import local_llm_status

    data = request.get_json() or {}
    document_id = data.get('document_id')
    source_language = (data.get('source_language') or '').strip()
    target_language = (data.get('target_language') or '').strip()
    translation_mode = (data.get('translation_mode') or 'auto').strip().lower()
    provider_mode = (data.get('provider_mode') or 'online').strip().lower()
    domain = (data.get('domain') or 'auto').strip().lower()
    quality_level = (data.get('quality_level') or 'professional').strip().lower()
    raw_back_translation = data.get('enable_back_translation', True)
    enable_back_translation = (
        raw_back_translation
        if isinstance(raw_back_translation, bool)
        else str(raw_back_translation).strip().lower() not in {'0', 'false', 'no', 'off'}
    )
    glossary_entries = data.get('glossary_entries') or []

    if not document_id:
        return jsonify({'status': 'error', 'message': 'Missing document_id in request payload.'}), 400
    if not source_language or not target_language:
        return jsonify({'status': 'error', 'message': 'Both source_language and target_language are required.'}), 400
    if source_language.lower() == target_language.lower():
        return jsonify({'status': 'error', 'message': 'Input language and target language must be different.'}), 400
    if translation_mode not in {'auto', 'document', 'literary'}:
        return jsonify({'status': 'error', 'message': 'Invalid translation_mode.'}), 400
    if provider_mode not in {'online', 'offline', 'offline_fast', 'offline_quality'}:
        return jsonify({'status': 'error', 'message': 'Invalid provider_mode.'}), 400
    if domain not in TRANSLATION_DOMAINS:
        return jsonify({'status': 'error', 'message': 'Invalid translation domain.'}), 400
    if quality_level not in {'draft', 'professional'}:
        return jsonify({'status': 'error', 'message': 'Invalid quality_level.'}), 400
    if not isinstance(glossary_entries, list) or len(glossary_entries) > 200:
        return jsonify({
            'status': 'error',
            'message': 'glossary_entries must be a list containing at most 200 terms.',
        }), 400

    if provider_mode != 'online':
        aya_status = local_llm_status()
        fast_status = fast_translation_status()
        needs_fast = provider_mode in {'offline_fast', 'offline_quality'}
        needs_aya = provider_mode in {'offline', 'offline_quality'}
        unavailable = (
            (needs_fast and not fast_status.get('available'))
            or (needs_aya and not aya_status.get('available'))
        )
        if unavailable:
            return jsonify({
                'status': 'error',
                'message': (
                    'The selected offline translation engine is not ready. '
                    'Open the Quality Dashboard and choose Enable local AI.'
                ),
                'local_engine': {'aya': aya_status, 'fast': fast_status},
            }), 503
        if provider_mode == 'offline_fast':
            provider_model = fast_status.get('model')
        elif provider_mode == 'offline_quality':
            provider_model = f"{fast_status.get('model')} + {aya_status.get('model')}"
        else:
            provider_model = aya_status.get('model') or current_app.config['LOCAL_LLM_MODEL']
    else:
        provider_model = current_app.config.get('FREEMODEL_MODEL', 'openai-t0')

    doc = _owned_document_or_404(document_id)
    if doc.status != 'Completed':
        return jsonify({'status': 'error', 'message': f'Document is {doc.status}. Please wait until processing completes.'}), 400
    if not doc.ocr_text:
        return jsonify({'status': 'error', 'message': 'Document OCR text is empty.'}), 400

    save_glossary_entries(
        doc.user_id,
        source_language,
        target_language,
        domain,
        glossary_entries,
    )
    resources = load_translation_resources(
        doc.user_id,
        source_language,
        target_language,
        domain,
    )
    resource_signature = knowledge_signature(resources)

    existing = DocumentTranslation.query.filter_by(
        document_id=doc.id,
        source_language=source_language,
        target_language=target_language,
        translation_mode=translation_mode,
        provider_mode=provider_mode,
        provider_model=provider_model,
        domain=domain,
        quality_level=quality_level,
        enable_back_translation=enable_back_translation,
        knowledge_signature=resource_signature,
    ).order_by(DocumentTranslation.created_at.desc()).first()
    if not existing and domain == 'auto':
        auto_candidates = DocumentTranslation.query.filter_by(
            document_id=doc.id,
            source_language=source_language,
            target_language=target_language,
            translation_mode=translation_mode,
            provider_mode=provider_mode,
            provider_model=provider_model,
            domain='auto',
            quality_level=quality_level,
            enable_back_translation=enable_back_translation,
        ).order_by(DocumentTranslation.created_at.desc()).limit(5).all()
        for candidate in auto_candidates:
            detected_domain = candidate.parsed_quality_report.get('domain')
            if not detected_domain or not candidate.knowledge_signature:
                continue
            detected_resources = load_translation_resources(
                doc.user_id,
                source_language,
                target_language,
                detected_domain,
            )
            if knowledge_signature(detected_resources) == candidate.knowledge_signature:
                existing = candidate
                break
    if not existing and not any(resources.values()):
        legacy_query = DocumentTranslation.query.filter_by(
            document_id=doc.id,
            source_language=source_language,
            target_language=target_language,
            translation_mode=translation_mode,
            provider_mode=provider_mode,
            domain=domain,
            quality_level=quality_level,
            enable_back_translation=enable_back_translation,
            knowledge_signature=None,
        )
        if provider_mode == 'offline':
            legacy_query = legacy_query.filter_by(provider_model=provider_model)
        else:
            legacy_query = legacy_query.filter(
                (DocumentTranslation.provider_model == provider_model)
                | DocumentTranslation.provider_model.is_(None)
            )
        existing = legacy_query.order_by(DocumentTranslation.created_at.desc()).first()
        if existing:
            existing.knowledge_signature = resource_signature
            existing.provider_model = provider_model
            db.session.commit()
    if existing and existing.status in {'Pending', 'Processing'}:
        now = datetime.utcnow()
        worker_mode = current_app.config.get('TRANSLATION_WORKER_MODE', 'inline')
        released_stale_lease = _release_expired_translation_lease(existing, now)
        should_dispatch = (
            worker_mode == 'inline'
            and (existing.status == 'Pending' or released_stale_lease)
        )
        if should_dispatch:
            app_instance = current_app._get_current_object()
            run_translation_async(process_translation_async, app_instance, existing.id)
        return jsonify({
            'status': existing.status,
            'message': (
                'Translation was re-queued to resume.'
                if released_stale_lease
                else 'This translation is already queued or processing.'
            ),
            'translation_id': existing.id,
            'document_name': doc.original_filename,
            'source_language_label': get_language_label(source_language),
            'target_language_label': get_language_label(target_language),
            'translation_mode': existing.translation_mode or 'auto',
            'provider_mode': existing.provider_mode or 'online',
            'provider_model': existing.provider_model,
            'domain': existing.domain or 'auto',
            'quality_level': existing.quality_level or 'professional',
            'enable_back_translation': bool(existing.enable_back_translation),
            'current_page': existing.current_page or 0,
            'total_pages': existing.total_pages or 0,
            'progress_percent': _translation_progress_percent(existing),
            'status_message': existing.status_message or '',
            'created_at': existing.created_at.isoformat() if existing.created_at else None,
        }), 202

    if existing and existing.status == 'Completed' and existing.translated_pdf_path and os.path.exists(existing.translated_pdf_path):
        return jsonify({
            'status': 'Completed',
            'message': 'A translated PDF for this language pair already exists.',
            'translation_id': existing.id,
            'document_name': doc.original_filename,
            'source_language_label': get_language_label(source_language),
            'target_language_label': get_language_label(target_language),
            'translation_mode': existing.translation_mode or 'auto',
            'provider_mode': existing.provider_mode or 'online',
            'provider_model': existing.provider_model,
            'domain': existing.domain or 'auto',
            'quality_level': existing.quality_level or 'professional',
            'enable_back_translation': bool(existing.enable_back_translation),
            'current_page': existing.current_page or 0,
            'total_pages': existing.total_pages or 0,
            'progress_percent': _translation_progress_percent(existing),
            'status_message': existing.status_message or '',
            'created_at': existing.created_at.isoformat() if existing.created_at else None,
            'download_url': url_for('api.download_translation', translation_id=existing.id),
        })

    if existing and existing.status == 'Failed' and existing.checkpoint_path \
            and os.path.isfile(existing.checkpoint_path):
        existing.status = 'Pending'
        existing.status_message = 'Translation queued to resume from its checkpoint...'
        existing.error_message = None
        existing.cancel_requested = False
        existing.completed_at = None
        existing.lease_owner = None
        existing.lease_expires_at = None
        db.session.commit()

        app_instance = current_app._get_current_object()
        if current_app.config.get('TRANSLATION_WORKER_MODE', 'inline') == 'inline':
            run_translation_async(process_translation_async, app_instance, existing.id)
        return jsonify({
            'status': 'Pending',
            'message': 'Translation will resume from its saved checkpoint.',
            'translation_id': existing.id,
            'document_name': doc.original_filename,
            'source_language_label': get_language_label(source_language),
            'target_language_label': get_language_label(target_language),
            'translation_mode': existing.translation_mode or 'auto',
            'provider_mode': existing.provider_mode or 'online',
            'provider_model': existing.provider_model,
            'domain': existing.domain or 'auto',
            'quality_level': existing.quality_level or 'professional',
            'enable_back_translation': bool(existing.enable_back_translation),
            'current_page': existing.current_page or 0,
            'total_pages': existing.total_pages or 0,
            'progress_percent': _translation_progress_percent(existing),
            'status_message': existing.status_message,
            'created_at': existing.created_at.isoformat() if existing.created_at else None,
        }), 202

    job = DocumentTranslation(
        document_id=doc.id,
        source_language=source_language,
        target_language=target_language,
        translation_mode=translation_mode,
        provider_mode=provider_mode,
        provider_model=provider_model,
        domain=domain,
        quality_level=quality_level,
        enable_back_translation=enable_back_translation,
        knowledge_signature=resource_signature,
        status='Pending',
        status_message='Translation queued...',
        current_page=0,
        total_pages=0,
        cancel_requested=False,
    )
    db.session.add(job)
    db.session.commit()

    # Queue background thread
    app_instance = current_app._get_current_object()
    if current_app.config.get('TRANSLATION_WORKER_MODE', 'inline') == 'inline':
        run_translation_async(process_translation_async, app_instance, job.id)

    return jsonify({
        'status': 'Pending',
        'message': 'Translation started in background.',
        'translation_id': job.id,
        'document_name': doc.original_filename,
        'source_language_label': get_language_label(source_language),
        'target_language_label': get_language_label(target_language),
        'translation_mode': translation_mode,
        'provider_mode': provider_mode,
        'provider_model': provider_model,
        'domain': domain,
        'quality_level': quality_level,
        'enable_back_translation': enable_back_translation,
        'current_page': 0,
        'total_pages': 0,
        'progress_percent': _translation_progress_percent(job),
        'status_message': 'Queued...',
        'created_at': job.created_at.isoformat() if job.created_at else None,
    }), 202


@api_bp.route('/translation/local-engine/status', methods=['GET'])
def get_local_translation_engine_status():
    from app.services.local_engine_manager import local_engine_control_status

    control = local_engine_control_status()
    aya = control['aya']
    fast = control['fast']
    return jsonify({
        'available': bool(aya.get('available') and fast.get('available')),
        'model': f"{fast.get('model', 'NLLB')} + {aya.get('model', 'Aya')}",
        'aya': aya,
        'fast': fast,
        'state': control['state'],
        'enabled': control['enabled'],
        'starting': control['starting'],
        'last_action': control['last_action'],
        'last_action_at': control['last_action_at'],
        'last_error': control['last_error'],
        'privacy': 'localhost-only',
    })


@api_bp.route('/translation/local-engine/start', methods=['POST'])
def start_local_translation_engines():
    from app.services.local_engine_manager import start_local_engines

    try:
        status = start_local_engines()
        return jsonify({
            **status,
            'message': (
                'Your local AI team is ready.'
                if status.get('enabled')
                else 'Your local quality reviewer is starting on Drive D.'
            ),
        }), 200 if status.get('enabled') else 202
    except (OSError, RuntimeError) as exc:
        logger.exception('Could not start the local translation engines: %s', exc)
        return jsonify({
            'status': 'error',
            'message': str(exc),
        }), 500


@api_bp.route('/translation/local-engine/stop', methods=['POST'])
def stop_local_translation_engines():
    from app.services.local_engine_manager import stop_local_engines

    active_query = DocumentTranslation.query.filter_by(status='Processing')
    if not current_app.testing:
        active_query = active_query.join(Document).filter(
            Document.user_id == _request_user_id()
        )
    active_jobs = active_query.count()
    if active_jobs:
        return jsonify({
            'status': 'busy',
            'message': (
                f'{active_jobs} translation is still working. Cancel or finish it '
                'before turning off local AI.'
            ),
        }), 409
    try:
        status = stop_local_engines()
        return jsonify({
            **status,
            'message': 'The local quality reviewer is off. Fast translation remains ready on demand.',
        })
    except (OSError, RuntimeError, TimeoutError) as exc:
        logger.exception('Could not stop the local translation engines: %s', exc)
        return jsonify({
            'status': 'error',
            'message': str(exc),
        }), 500


def _operation_owner(job: DocumentTranslation, stage: dict) -> tuple[str, str]:
    key = stage.get('key')
    if job.status == 'NeedsReview':
        return 'Quality desk', 'Waiting for your decision on failed QA'
    if key in {'planning', 'quality'}:
        return 'Quality reviewer', 'Checking meaning, terminology, and consistency'
    if key == 'translation':
        if job.provider_mode in {'offline_fast', 'offline_quality'}:
            return 'Fast translator', 'Creating and checking the translation draft'
        return 'Quality reviewer', 'Translating and reviewing the current passage'
    if key in {'layout', 'rendering', 'saving'}:
        return 'Document builder', 'Protecting the layout and preparing the PDF'
    return 'Translation coordinator', 'Preparing the next safe step'


def _live_engine_updates(engine: dict) -> list[dict]:
    """Return the privacy-safe work telemetry that the dashboard can update live."""
    updates = []
    for key, label in (('fast', 'Fast translator'), ('aya', 'Quality reviewer')):
        engine_status = engine.get(key) or {}
        activity = engine_status.get('activity') or {}
        if activity.get('state') != 'working':
            continue
        context = activity.get('context') or {}
        updates.append({
            'engine': key,
            'label': label,
            'action': activity.get('action') or 'Working locally',
            'phase': activity.get('phase') or 'working',
            'elapsed_seconds': activity.get('elapsed_seconds'),
            'segment_count': activity.get('current_segments'),
            'language_pair': activity.get('language_pair') or context.get('language_pair'),
            'translation_id': context.get('translation_id'),
            'document_name': context.get('document_name'),
            'model': activity.get('model') or engine_status.get('model'),
            'prompt_characters': activity.get('prompt_characters'),
            'max_output_tokens': activity.get('max_output_tokens'),
        })
    return updates


@api_bp.route('/translation/operations/status', methods=['GET'])
def get_translation_operations_status():
    from app.services.local_engine_manager import local_engine_control_status

    engine = local_engine_control_status()
    live_engines = _live_engine_updates(engine)
    jobs_query = DocumentTranslation.query.filter(
        DocumentTranslation.status.in_({'Pending', 'Processing', 'NeedsReview'})
    )
    if not current_app.testing:
        jobs_query = jobs_query.join(Document).filter(
            Document.user_id == _request_user_id()
        )
    jobs = jobs_query.order_by(DocumentTranslation.created_at.asc()).limit(12).all()
    processing_jobs = [job for job in jobs if job.status == 'Processing']
    operations = []
    for job in jobs:
        stage = _translation_progress_stage(job)
        owner, action = _operation_owner(job, stage)
        matching_engines = [
            item for item in live_engines
            if item.get('translation_id') == job.id
        ]
        if not matching_engines and job.status == 'Processing' and len(processing_jobs) == 1:
            matching_engines = live_engines
        operations.append({
            'translation_id': job.id,
            'document_name': job.document.original_filename if job.document else 'Deleted document',
            'status': job.status,
            'stage': stage,
            'owner': owner,
            'action': action,
            'status_message': job.status_message or action,
            'progress_percent': _translation_progress_percent(job),
            'current_page': job.current_page or 0,
            'total_pages': job.total_pages or 0,
            'provider_mode': job.provider_mode or 'online',
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'live_engines': matching_engines,
        })
    return jsonify({
        'engine': engine,
        'operations': operations,
        'live_engines': live_engines,
        'active_count': sum(job.status == 'Processing' for job in jobs),
        'waiting_for_review_count': sum(job.status == 'NeedsReview' for job in jobs),
        'refreshed_at': datetime.utcnow().isoformat() + 'Z',
    })


@api_bp.route('/translation/<int:translation_id>/status', methods=['GET'])
def get_translation_status(translation_id):
    from datetime import datetime
    from app.services.translation_service import get_language_label
    job = _owned_translation_or_404(translation_id)
    if _release_expired_translation_lease(job, datetime.utcnow()):
        if current_app.config.get('TRANSLATION_WORKER_MODE', 'inline') == 'inline':
            app_instance = current_app._get_current_object()
            run_translation_async(process_translation_async, app_instance, job.id)
        db.session.refresh(job)
    if _resolve_nontranslatable_review_noise(job):
        if current_app.config.get('TRANSLATION_WORKER_MODE', 'inline') == 'inline':
            app_instance = current_app._get_current_object()
            run_translation_async(process_translation_async, app_instance, job.id)
        db.session.refresh(job)
    doc = _owned_document_or_404(job.document_id)
    doc_name = doc.original_filename if doc else 'Unknown Document'
    now = datetime.utcnow()
    elapsed_seconds = None
    if job.started_at:
        if job.status == 'Completed' and job.completed_at:
            end_time = job.completed_at
        elif job.status in {'NeedsReview', 'Failed', 'Cancelled'} and job.last_heartbeat_at:
            end_time = job.last_heartbeat_at
        else:
            end_time = now
        elapsed_seconds = max(0, int((end_time - job.started_at).total_seconds()))
    heartbeat_age_seconds = (
        max(0, int((now - job.last_heartbeat_at).total_seconds()))
        if job.last_heartbeat_at else None
    )
    
    return jsonify({
        'status': job.status,
        'translation_id': job.id,
        'document_name': doc_name,
        'source_language': job.source_language,
        'target_language': job.target_language,
        'source_language_label': get_language_label(job.source_language),
        'target_language_label': get_language_label(job.target_language),
        'current_page': job.current_page or 0,
        'total_pages': job.total_pages or 0,
        'progress_percent': _translation_progress_percent(job),
        'progress_stage': _translation_progress_stage(job),
        'status_message': job.status_message or '',
        'elapsed_seconds': elapsed_seconds,
        'heartbeat_age_seconds': heartbeat_age_seconds,
        'created_at': job.created_at.isoformat() if job.created_at else None,
        'completed_at': job.completed_at.isoformat() if job.completed_at else None,
        'started_at': job.started_at.isoformat() if job.started_at else None,
        'attempt_count': job.attempt_count or 0,
        'cancel_requested': bool(job.cancel_requested),
        'download_url': url_for('api.download_translation', translation_id=job.id) if job.status == 'Completed' else None,
        'error_message': job.error_message,
        'translation_mode': job.translation_mode or 'auto',
        'provider_mode': job.provider_mode or 'online',
        'provider_model': job.provider_model,
        'domain': job.domain or 'auto',
        'quality_level': job.quality_level or 'professional',
        'enable_back_translation': bool(job.enable_back_translation),
        'detected_mode': job.detected_mode,
        'quality_score': job.quality_score,
        'quality_report': job.parsed_quality_report,
        'review_issues': [
            issue.to_dict()
            for issue in job.review_issues.order_by(
                TranslationReviewIssue.id.asc(),
            ).all()
        ],
    })


@api_bp.route('/translation/glossary', methods=['GET', 'POST'])
def translation_glossary():
    from app.services.translation_service import TRANSLATION_DOMAINS
    from app.services.translation_memory_service import save_glossary_entries

    if request.method == 'POST':
        data = request.get_json() or {}
        source_language = str(data.get('source_language') or '').strip()
        target_language = str(data.get('target_language') or '').strip()
        domain = str(data.get('domain') or 'general').strip().lower()
        entries = data.get('entries') or []
        if not source_language or not target_language:
            return jsonify({'status': 'error', 'message': 'Language pair is required.'}), 400
        if domain not in TRANSLATION_DOMAINS - {'auto'}:
            return jsonify({'status': 'error', 'message': 'Invalid domain.'}), 400
        if not isinstance(entries, list) or len(entries) > 500:
            return jsonify({'status': 'error', 'message': 'Invalid glossary entries.'}), 400
        saved = save_glossary_entries(
            _request_user_id(),
            source_language,
            target_language,
            domain,
            entries,
        )
        return jsonify({'status': 'success', 'saved': saved})

    source_language = str(request.args.get('source_language') or '').strip()
    target_language = str(request.args.get('target_language') or '').strip()
    domain = str(request.args.get('domain') or '').strip().lower()
    query = TranslationGlossaryTerm.query.filter_by(user_id=_request_user_id(), active=True)
    if source_language:
        query = query.filter_by(source_language=source_language)
    if target_language:
        query = query.filter_by(target_language=target_language)
    if domain:
        query = query.filter_by(domain=domain)
    return jsonify({
        'status': 'success',
        'entries': [item.to_dict() for item in query.order_by(
            TranslationGlossaryTerm.source_term.asc()
        ).all()],
    })


@api_bp.route('/translation/glossary/<int:term_id>', methods=['DELETE'])
def delete_translation_glossary_term(term_id):
    term = TranslationGlossaryTerm.query.filter_by(
        id=term_id, user_id=_request_user_id()
    ).first_or_404()
    term.active = False
    db.session.commit()
    return jsonify({'status': 'success', 'deleted': term.id})


@api_bp.route('/translation/<int:translation_id>/cancel', methods=['POST'])
def cancel_translation(translation_id):
    job = _owned_translation_or_404(translation_id)
    if job.status in {'Completed', 'Failed', 'Cancelled'}:
        return jsonify({
            'status': job.status,
            'message': f'Translation is already {job.status.lower()}.',
        }), 409

    job.cancel_requested = True
    job.status = 'Cancelled'
    job.status_message = 'Translation cancelled by user.'
    job.lease_owner = None
    job.lease_expires_at = None
    db.session.commit()
    return jsonify({
        'status': 'Cancelled',
        'translation_id': job.id,
        'message': job.status_message,
    })


@api_bp.route('/translation/<int:translation_id>/review', methods=['POST'])
def resolve_translation_review(translation_id):
    from datetime import datetime
    from app.services.translation_memory_service import persist_translation_memory
    from app.services.translation_service import (
        TranslationError,
        apply_human_review_corrections,
        validate_human_translation,
    )

    job = _owned_translation_or_404(translation_id)
    if job.status != 'NeedsReview':
        return jsonify({
            'status': 'error',
            'message': 'This translation is not waiting for a quality decision.',
        }), 409
    if not job.checkpoint_path or not os.path.isfile(job.checkpoint_path):
        return jsonify({
            'status': 'error',
            'message': 'The translation checkpoint is missing, so it cannot resume safely.',
        }), 409

    data = request.get_json(silent=True) or {}
    corrections = data.get('corrections')
    if not isinstance(corrections, list) or not corrections or len(corrections) > 50:
        return jsonify({
            'status': 'error',
            'message': 'Provide one approved translation for each failed passage.',
        }), 400

    requested_ids = []
    normalized = []
    for correction in corrections:
        if not isinstance(correction, dict):
            return jsonify({'status': 'error', 'message': 'Invalid correction.'}), 400
        issue_ids = correction.get('issue_ids') or []
        target_text = str(correction.get('target_text') or '').strip()
        if not isinstance(issue_ids, list) or not issue_ids or not target_text:
            return jsonify({
                'status': 'error',
                'message': 'Every failed passage needs an approved translation.',
            }), 400
        try:
            issue_ids = list(dict.fromkeys(int(value) for value in issue_ids))
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'Invalid review item.'}), 400
        requested_ids.extend(issue_ids)
        normalized.append({'issue_ids': issue_ids, 'target_text': target_text})

    if len(requested_ids) != len(set(requested_ids)):
        return jsonify({
            'status': 'error',
            'message': 'A failed passage was submitted more than once.',
        }), 400

    issues = TranslationReviewIssue.query.filter(
        TranslationReviewIssue.translation_id == job.id,
        TranslationReviewIssue.id.in_(requested_ids),
        TranslationReviewIssue.status == 'open',
    ).all()
    issue_map = {issue.id: issue for issue in issues}
    if set(issue_map) != set(requested_ids):
        return jsonify({
            'status': 'error',
            'message': 'One or more review items are no longer open.',
        }), 409

    checkpoint_corrections = []
    memory_segments = []
    try:
        for correction in normalized:
            grouped = [issue_map[issue_id] for issue_id in correction['issue_ids']]
            if any(
                issue.severity.lower() not in {'error', 'critical'}
                or not (issue.source_excerpt or '').strip()
                for issue in grouped
            ):
                return jsonify({
                    'status': 'error',
                    'message': 'Only failed QA passages can be corrected here.',
                }), 400
            source_texts = {
                (issue.source_excerpt or '').strip() for issue in grouped
            }
            if len(source_texts) != 1:
                return jsonify({
                    'status': 'error',
                    'message': 'Grouped review items must refer to the same passage.',
                }), 400
            source_text = source_texts.pop()
            target_text = correction['target_text']
            validate_human_translation(source_text, target_text, job.target_language)
            checkpoint_corrections.append({
                'source_text': source_text,
                'target_text': target_text,
            })
            memory_segments.append({
                'source_text': source_text,
                'target_text': target_text,
            })
            for issue in grouped:
                issue.target_excerpt = target_text[:1000]
                issue.status = 'resolved'
    except TranslationError as exc:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': f'This correction still fails a safety check: {exc}',
        }), 400

    apply_human_review_corrections(job.checkpoint_path, checkpoint_corrections)
    persist_translation_memory(
        job.document.user_id if job.document else 1,
        job.source_language,
        job.target_language,
        job.domain or 'general',
        memory_segments,
        100.0,
    )
    db.session.flush()
    open_issues = TranslationReviewIssue.query.filter(
        TranslationReviewIssue.translation_id == job.id,
        TranslationReviewIssue.status == 'open',
    ).all()
    remaining = len(_actionable_review_issues([
        issue.to_dict() for issue in open_issues
    ]))
    if remaining:
        job.status_message = (
            f'Your decision is still needed for {remaining} failed '
            f'passage{"" if remaining == 1 else "s"}.'
        )
        db.session.commit()
        return jsonify({
            'status': 'NeedsReview',
            'translation_id': job.id,
            'remaining': remaining,
            'message': job.status_message,
        })

    job.status = 'Pending'
    job.status_message = 'Corrections approved. Translation queued to resume...'
    job.error_message = None
    job.cancel_requested = False
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_heartbeat_at = datetime.utcnow()
    db.session.commit()

    if current_app.config.get('TRANSLATION_WORKER_MODE', 'inline') == 'inline':
        app_instance = current_app._get_current_object()
        run_translation_async(process_translation_async, app_instance, job.id)
    return jsonify({
        'status': 'Pending',
        'translation_id': job.id,
        'remaining': 0,
        'message': job.status_message,
    }), 202


@api_bp.route('/translation/<int:translation_id>/download', methods=['GET'])
def download_translation(translation_id):
    job = _owned_translation_or_404(translation_id)
    if job.status != 'Completed' or not job.translated_pdf_path or not os.path.exists(job.translated_pdf_path):
        return jsonify({'status': 'error', 'message': 'Translated PDF is not ready yet.'}), 400

    directory = os.path.dirname(job.translated_pdf_path)
    filename = os.path.basename(job.translated_pdf_path)
    return send_from_directory(directory, filename, as_attachment=True, download_name=filename)



@api_bp.route('/document/<int:doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    """Delete a document and its associated data permanently."""
    doc = _owned_document_or_404(doc_id)
    try:
        if os.path.exists(doc.file_path):
            os.remove(doc.file_path)

        for job in list(doc.translation_jobs):
            if job.translated_pdf_path and os.path.exists(job.translated_pdf_path):
                os.remove(job.translated_pdf_path)
            if job.checkpoint_path and os.path.exists(job.checkpoint_path):
                os.remove(job.checkpoint_path)

        db.session.delete(doc)
        db.session.commit()
        return jsonify({'message': 'Document deleted successfully.'})

    except Exception as e:
        db.session.rollback()
        logger.exception(f"Deletion failed for document {doc_id}: {str(e)}")
        return jsonify({'error': f'Deletion failed: {str(e)}'}), 500


@api_bp.route('/documents', methods=['GET'])
def list_documents():
    """Return all documents as JSON (for JS polling)."""
    docs = Document.query.filter_by(user_id=_request_user_id()).order_by(
        Document.created_at.desc()
    ).all()
    return jsonify({'documents': [d.to_dict() for d in docs]})


@api_bp.route('/documents/compare', methods=['POST'])
def compare_documents_route():
    """Compare two documents semantically."""
    data = request.get_json() or {}
    doc1_id = data.get('document_id_1')
    doc2_id = data.get('document_id_2')

    if not doc1_id or not doc2_id:
        return jsonify({'status': 'error', 'message': 'Missing document_id_1 or document_id_2 in request payload.'}), 400

    try:
        _owned_document_or_404(doc1_id)
        _owned_document_or_404(doc2_id)
        # Check database for existing comparison
        existing = DocumentComparison.query.filter_by(
            document_one_id=doc1_id,
            document_two_id=doc2_id
        ).first()

        if existing:
            logger.info(f"Returning cached comparison between doc {doc1_id} and doc {doc2_id}")
            return jsonify({
                'status': 'success',
                'comparison': existing.parsed_result,
                'comparison_id': existing.id,
                'cached': True
            })

        # Run comparison pipeline
        from app.services.document_comparison_service import compare_documents
        ai_api_key = _freemodel_api_keys(current_app.config)

        result, err = compare_documents(doc1_id, doc2_id, api_key=ai_api_key)
        if err:
            return jsonify({'status': 'error', 'message': err}), 500

        # Save to database
        comp = DocumentComparison(
            document_one_id=doc1_id,
            document_two_id=doc2_id
        )
        comp.parsed_result = result
        db.session.add(comp)
        db.session.commit()

        return jsonify({
            'status': 'success',
            'comparison': result,
            'comparison_id': comp.id,
            'cached': False
        })

    except Exception as e:
        logger.exception("API exception during comparison")
        return jsonify({'status': 'error', 'message': f'Internal Server Error: {str(e)}'}), 500


@api_bp.route('/comparisons', methods=['GET'])
def list_comparisons():
    """Get history of comparisons."""
    comps = DocumentComparison.query.order_by(DocumentComparison.created_at.desc()).all()
    results = []
    for c in comps:
        doc1 = Document.query.get(c.document_one_id)
        doc2 = Document.query.get(c.document_two_id)
        if not doc1 or not doc2:
            continue
        if not current_app.testing and (
            doc1.user_id != current_user.id or doc2.user_id != current_user.id
        ):
            continue
        results.append({
            'id': c.id,
            'document_one_id': c.document_one_id,
            'document_two_id': c.document_two_id,
            'document_one_name': doc1.original_filename if doc1 else 'Deleted Document',
            'document_two_name': doc2.original_filename if doc2 else 'Deleted Document',
            'result': c.parsed_result,
            'created_at': c.created_at.isoformat()
        })
    return jsonify({'comparisons': results})


@api_bp.route('/comparison/<int:comp_id>', methods=['DELETE'])
def delete_comparison(comp_id):
    """Delete a comparison permanently."""
    comp = DocumentComparison.query.get_or_404(comp_id)
    _owned_document_or_404(comp.document_one_id)
    _owned_document_or_404(comp.document_two_id)
    try:
        db.session.delete(comp)
        db.session.commit()
        return jsonify({'message': 'Comparison deleted successfully.'})
    except Exception as e:
        db.session.rollback()
        logger.exception(f"Deletion failed for comparison {comp_id}: {str(e)}")
        return jsonify({'error': f'Deletion failed: {str(e)}'}), 500
