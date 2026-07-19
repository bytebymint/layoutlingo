import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app, db
from app.models.document import (
    Document,
    DocumentTranslation,
    TranslationGlossaryTerm,
    TranslationReviewIssue,
)
from app.routes.api import (
    _claim_translation_job,
    _renew_translation_lease,
    _translation_progress_percent,
    _translation_progress_stage,
    process_translation_async,
)
from app.services.background_worker import resume_translation_jobs
from app.services import translation_service


class DurableTranslationJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=os.path.dirname(__file__))
        config = type('TranslationTestConfig', (), {
            'TESTING': True,
            'SECRET_KEY': 'translation-test-secret',
            'SQLALCHEMY_DATABASE_URI': 'sqlite://',
            'SQLALCHEMY_TRACK_MODIFICATIONS': False,
            'SQLALCHEMY_ENGINE_OPTIONS': {},
            'UPLOAD_FOLDER': self.temp_dir.name,
            'MAX_CONTENT_LENGTH': 10 * 1024 * 1024,
            'ALLOWED_EXTENSIONS': {'pdf'},
            'GEMINI_API_KEY': '',
            'FREEMODEL_API_KEY': 'test-provider-key',
            'TRANSLATION_WORKER_MODE': 'external',
            'TRANSLATION_LEASE_SECONDS': 300,
            'TRANSLATION_POLL_SECONDS': 0.25,
        })
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.temp_dir.cleanup()

    def _create_document_and_job(self):
        document = Document(
            filename='book.pdf',
            original_filename='book.pdf',
            file_path=os.path.join(self.temp_dir.name, 'book.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='Book text',
        )
        db.session.add(document)
        db.session.flush()
        job = DocumentTranslation(
            document_id=document.id,
            source_language='en',
            target_language='ar',
            status='Pending',
        )
        db.session.add(job)
        db.session.commit()
        return document, job

    def test_database_lease_prevents_duplicate_workers_and_recovers_expiry(self):
        _document, job = self._create_document_and_job()

        first_claim = _claim_translation_job(job.id, 'worker-one', 300)
        self.assertIsNotNone(first_claim)
        self.assertEqual(first_claim.attempt_count, 1)
        self.assertIsNone(_claim_translation_job(job.id, 'worker-two', 300))

        first_claim.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
        recovered_claim = _claim_translation_job(job.id, 'worker-two', 300)
        self.assertIsNotNone(recovered_claim)
        self.assertEqual(recovered_claim.lease_owner, 'worker-two')
        self.assertEqual(recovered_claim.attempt_count, 2)

    def test_worker_heartbeat_extends_lease_during_long_model_calls(self):
        _document, job = self._create_document_and_job()
        claimed = _claim_translation_job(job.id, 'slow-local-worker', 60)
        original_expiry = claimed.lease_expires_at

        claimed.lease_expires_at = datetime.utcnow() + timedelta(seconds=1)
        db.session.commit()
        self.assertTrue(_renew_translation_lease(job.id, 'slow-local-worker', 300))

        db.session.refresh(claimed)
        self.assertGreater(claimed.lease_expires_at, original_expiry)
        self.assertFalse(_renew_translation_lease(job.id, 'wrong-worker', 300))

    def test_cancel_endpoint_releases_job_lease(self):
        _document, job = self._create_document_and_job()
        _claim_translation_job(job.id, 'worker-one', 300)

        response = self.client.post(f'/api/translation/{job.id}/cancel')

        self.assertEqual(response.status_code, 200)
        db.session.refresh(job)
        self.assertEqual(job.status, 'Cancelled')
        self.assertTrue(job.cancel_requested)
        self.assertIsNone(job.lease_owner)
        self.assertIsNone(job.lease_expires_at)

    def test_external_worker_mode_only_persists_the_queued_job(self):
        document = Document(
            filename='book.pdf',
            original_filename='book.pdf',
            file_path=os.path.join(self.temp_dir.name, 'book.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='Book text',
        )
        db.session.add(document)
        db.session.commit()

        with patch('app.routes.api.run_translation_async') as dispatch:
            response = self.client.post('/api/translate', json={
                'document_id': document.id,
                'source_language': 'en',
                'target_language': 'fa',
            })

        self.assertEqual(response.status_code, 202)
        dispatch.assert_not_called()
        queued = DocumentTranslation.query.one()
        self.assertEqual(queued.status, 'Pending')
        self.assertEqual(queued.translation_mode, 'auto')

    def test_translation_profile_is_validated_and_persisted(self):
        document = Document(
            filename='story.pdf',
            original_filename='story.pdf',
            file_path=os.path.join(self.temp_dir.name, 'story.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='Story text',
        )
        db.session.add(document)
        db.session.commit()

        invalid = self.client.post('/api/translate', json={
            'document_id': document.id,
            'source_language': 'en',
            'target_language': 'fa',
            'translation_mode': 'manual-review',
        })
        self.assertEqual(invalid.status_code, 400)

        response = self.client.post('/api/translate', json={
            'document_id': document.id,
            'source_language': 'en',
            'target_language': 'fa',
            'translation_mode': 'literary',
        })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['translation_mode'], 'literary')
        self.assertEqual(DocumentTranslation.query.one().translation_mode, 'literary')

    def test_offline_translation_requires_a_healthy_local_engine(self):
        document = Document(
            filename='private.pdf',
            original_filename='private.pdf',
            file_path=os.path.join(self.temp_dir.name, 'private.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='Private text',
        )
        db.session.add(document)
        db.session.commit()

        with patch(
            'app.services.local_llm_api.local_llm_status',
            return_value={'available': False, 'error': 'not running'},
        ):
            response = self.client.post('/api/translate', json={
                'document_id': document.id,
                'source_language': 'en',
                'target_language': 'fa',
                'provider_mode': 'offline',
            })

        self.assertEqual(response.status_code, 503)
        self.assertEqual(DocumentTranslation.query.count(), 0)

    def test_offline_translation_persists_the_local_model_identity(self):
        document = Document(
            filename='private.pdf',
            original_filename='private.pdf',
            file_path=os.path.join(self.temp_dir.name, 'private.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='Private text',
        )
        db.session.add(document)
        db.session.commit()

        with patch(
            'app.services.local_llm_api.local_llm_status',
            return_value={'available': True, 'model': 'qwen3-local-test'},
        ):
            response = self.client.post('/api/translate', json={
                'document_id': document.id,
                'source_language': 'en',
                'target_language': 'fa',
                'provider_mode': 'offline',
            })

        self.assertEqual(response.status_code, 202)
        data = response.get_json()
        self.assertEqual(data['provider_mode'], 'offline')
        self.assertEqual(data['provider_model'], 'qwen3-local-test')
        queued = DocumentTranslation.query.one()
        self.assertEqual(queued.provider_mode, 'offline')
        self.assertEqual(queued.provider_model, 'qwen3-local-test')

    def test_inline_restart_requeues_pending_database_jobs(self):
        _document, job = self._create_document_and_job()

        with patch('app.services.background_worker.run_translation_async') as dispatch:
            resumed = resume_translation_jobs(self.app, object())

        self.assertEqual(resumed, 1)
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[2], job.id)

    def test_failed_translation_reuses_its_existing_checkpoint(self):
        document = Document(
            filename='story.pdf',
            original_filename='story.pdf',
            file_path=os.path.join(self.temp_dir.name, 'story.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='Story text',
        )
        db.session.add(document)
        db.session.flush()
        checkpoint_path = os.path.join(self.temp_dir.name, 'story.checkpoint.json')
        with open(checkpoint_path, 'w', encoding='utf-8') as checkpoint:
            checkpoint.write('{"translations": {}, "metadata": {}}')
        job = DocumentTranslation(
            document_id=document.id,
            source_language='en',
            target_language='fa',
            translation_mode='literary',
            status='Failed',
            status_message='Provider quota exhausted.',
            error_message='HTTP 402',
            checkpoint_path=checkpoint_path,
            current_page=131,
            total_pages=233,
        )
        db.session.add(job)
        db.session.commit()

        response = self.client.post('/api/translate', json={
            'document_id': document.id,
            'source_language': 'en',
            'target_language': 'fa',
            'translation_mode': 'literary',
        })

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['translation_id'], job.id)
        self.assertEqual(response.get_json()['current_page'], 131)
        self.assertEqual(DocumentTranslation.query.count(), 1)
        db.session.refresh(job)
        self.assertEqual(job.status, 'Pending')
        self.assertIsNone(job.error_message)
        self.assertEqual(job.checkpoint_path, checkpoint_path)

    def test_status_endpoint_requeues_expired_inline_processing_job(self):
        self.app.config['TRANSLATION_WORKER_MODE'] = 'inline'
        _document, job = self._create_document_and_job()
        job.status = 'Processing'
        job.status_message = 'Analyzing document layout...'
        job.total_pages = 233
        job.current_page = 0
        job.lease_owner = 'dead-worker'
        job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        with patch('app.routes.api.run_translation_async') as dispatch:
            response = self.client.get(f'/api/translation/{job.id}/status')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['status'], 'Pending')
        self.assertGreaterEqual(data['progress_percent'], 1)
        dispatch.assert_called_once()
        db.session.refresh(job)
        self.assertIsNone(job.lease_owner)
        self.assertEqual(job.status_message, 'Previous worker stopped; translation queued to resume...')

    def test_translation_status_reports_phase_aware_progress(self):
        _document, job = self._create_document_and_job()
        job.status = 'Processing'
        job.current_page = 0
        job.total_pages = 200
        job.status_message = 'Analyzing document layout page 100 of 200...'
        self.assertEqual(_translation_progress_percent(job), 8)

        job.status_message = 'AI-translated and edited 50 of 200 pages.'
        self.assertGreater(_translation_progress_percent(job), 20)

        job.total_pages = 2
        job.status_message = (
            'Translating page 1 of 2: Quality-checked batch 2 of 4...'
        )
        self.assertGreater(_translation_progress_percent(job), 14)
        self.assertEqual(_translation_progress_stage(job)['key'], 'translation')

        job.current_page = 20
        job.total_pages = 20
        job.status_message = 'Recovering literary review 2 of 4 on page 20...'
        self.assertEqual(_translation_progress_stage(job)['key'], 'quality')
        self.assertGreaterEqual(_translation_progress_percent(job), 93)
        self.assertLess(_translation_progress_percent(job), 99)

        job.status = 'NeedsReview'
        self.assertEqual(_translation_progress_stage(job)['key'], 'review')
        self.assertGreater(_translation_progress_percent(job), 0)

    def test_failed_qa_correction_updates_checkpoint_and_queues_resume(self):
        document, job = self._create_document_and_job()
        source = 'The garden is quiet.'
        target = 'باغ آرام و ساکت است.'
        checkpoint_path = os.path.join(self.temp_dir.name, 'review.checkpoint.json')
        with open(checkpoint_path, 'w', encoding='utf-8') as checkpoint:
            json.dump({'translations': {}, 'metadata': {'translation_mode': 'document'}}, checkpoint)
        job.status = 'NeedsReview'
        job.checkpoint_path = checkpoint_path
        job.current_page = 1
        job.total_pages = 2
        issue = TranslationReviewIssue(
            translation_id=job.id,
            category='target_language',
            severity='error',
            message='The passage remained in the input language.',
            page_number=1,
            source_excerpt=source,
            target_excerpt='The garden is quiet.',
            status='open',
        )
        db.session.add(issue)
        db.session.commit()

        response = self.client.post(f'/api/translation/{job.id}/review', json={
            'corrections': [{
                'issue_ids': [issue.id],
                'target_text': target,
            }],
        })

        self.assertEqual(response.status_code, 202)
        db.session.refresh(job)
        db.session.refresh(issue)
        self.assertEqual(job.status, 'Pending')
        self.assertEqual(issue.status, 'resolved')
        cache = translation_service._load_translation_checkpoint(checkpoint_path)
        self.assertEqual(cache[translation_service._human_review_cache_key(source)], target)

    def test_worker_pauses_a_structured_qa_failure_for_human_review(self):
        _document, job = self._create_document_and_job()
        source = 'This passage remained in English.'

        def fail_with_review(*_args, **kwargs):
            checkpoint_path = kwargs['checkpoint_path']
            with open(checkpoint_path, 'w', encoding='utf-8') as checkpoint:
                json.dump({'translations': {}, 'metadata': {}}, checkpoint)
            raise translation_service.TranslationItemQualityError(
                'Item 1 failed target-language QA (English leakage).',
                source,
                source,
                0,
            )

        with patch(
            'app.services.translation_service.translate_and_render_pdf',
            side_effect=fail_with_review,
        ):
            process_translation_async(self.app, job.id)

        db.session.refresh(job)
        issue = TranslationReviewIssue.query.filter_by(translation_id=job.id).one()
        self.assertEqual(job.status, 'NeedsReview')
        self.assertIsNone(job.error_message)
        self.assertEqual(issue.source_excerpt, source)
        self.assertEqual(issue.status, 'open')

    def test_status_auto_resolves_punctuation_only_review_loop(self):
        _document, job = self._create_document_and_job()
        checkpoint_path = os.path.join(self.temp_dir.name, 'layout.checkpoint.json')
        with open(checkpoint_path, 'w', encoding='utf-8') as checkpoint:
            json.dump({'translations': {}, 'metadata': {}}, checkpoint)
        job.status = 'NeedsReview'
        job.checkpoint_path = checkpoint_path
        issue = TranslationReviewIssue(
            translation_id=job.id,
            category='untranslated_text',
            severity='error',
            message='Source and target text are unchanged.',
            page_number=4,
            source_excerpt='_______________________',
            target_excerpt='_______________________',
            status='open',
        )
        db.session.add(issue)
        db.session.commit()

        response = self.client.get(f'/api/translation/{job.id}/status')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['status'], 'Pending')
        db.session.refresh(issue)
        self.assertEqual(issue.status, 'resolved')

    def test_local_engine_control_routes_use_safe_manager(self):
        manager_status = {
            'state': 'starting',
            'enabled': False,
            'starting': True,
            'last_action': 'start',
            'last_action_at': '2026-07-18T10:00:00+00:00',
            'last_error': None,
            'aya': {'available': False},
            'fast': {'available': True},
        }
        with patch(
            'app.services.local_engine_manager.start_local_engines',
            return_value=manager_status,
        ) as start:
            response = self.client.post('/api/translation/local-engine/start')

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json()['state'], 'starting')
        start.assert_called_once_with()

    def test_operations_status_explains_review_work_in_plain_language(self):
        _document, job = self._create_document_and_job()
        job.status = 'NeedsReview'
        job.status_message = 'Your decision is needed for 1 failed passage.'
        db.session.commit()
        engine = {
            'state': 'off', 'enabled': False, 'starting': False,
            'last_action': None, 'last_action_at': None, 'last_error': None,
            'aya': {'available': False}, 'fast': {'available': True},
        }
        with patch(
            'app.services.local_engine_manager.local_engine_control_status',
            return_value=engine,
        ):
            response = self.client.get('/api/translation/operations/status')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['waiting_for_review_count'], 1)
        self.assertEqual(data['operations'][0]['owner'], 'Quality desk')
        self.assertEqual(data['operations'][0]['stage']['key'], 'review')

    def test_operations_status_includes_live_model_activity_for_its_job(self):
        _document, job = self._create_document_and_job()
        job.status = 'Processing'
        job.current_page = 4
        job.total_pages = 12
        job.status_message = 'Translating page 5 of 12: batch 1 of 2 - Drafting with deterministic QA.'
        db.session.commit()
        engine = {
            'state': 'ready', 'enabled': True, 'starting': False,
            'last_action': None, 'last_action_at': None, 'last_error': None,
            'fast': {
                'available': True,
                'model': 'nllb-local',
                'activity': {
                    'state': 'working', 'phase': 'drafting',
                    'action': 'Translating 3 text segments',
                    'elapsed_seconds': 9, 'current_segments': 3,
                    'context': {'translation_id': job.id, 'document_name': 'sample.pdf'},
                },
            },
            'aya': {'available': True, 'activity': {'state': 'ready'}},
        }
        with patch(
            'app.services.local_engine_manager.local_engine_control_status',
            return_value=engine,
        ):
            response = self.client.get('/api/translation/operations/status')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['live_engines'][0]['engine'], 'fast')
        self.assertEqual(data['operations'][0]['live_engines'][0]['elapsed_seconds'], 9)

    def test_professional_translation_settings_and_glossary_are_persisted(self):
        document = Document(
            user_id=1,
            filename='contract.pdf',
            original_filename='contract.pdf',
            file_path=os.path.join(self.temp_dir.name, 'contract.pdf'),
            file_type='pdf',
            status='Completed',
            ocr_text='This Agreement may be terminated.',
        )
        db.session.add(document)
        db.session.commit()

        response = self.client.post('/api/translate', json={
            'document_id': document.id,
            'source_language': 'en',
            'target_language': 'es',
            'translation_mode': 'document',
            'domain': 'legal',
            'quality_level': 'professional',
            'enable_back_translation': True,
            'glossary_entries': [{
                'source_term': 'termination',
                'target_term': 'rescisión',
                'authority': 'locked',
            }],
        })

        self.assertEqual(response.status_code, 202)
        job = DocumentTranslation.query.one()
        self.assertEqual(job.domain, 'legal')
        self.assertEqual(job.quality_level, 'professional')
        self.assertTrue(job.enable_back_translation)
        self.assertIsNotNone(job.knowledge_signature)
        term = TranslationGlossaryTerm.query.one()
        self.assertEqual(term.authority, 'locked')
        self.assertEqual(term.target_term, 'rescisión')


if __name__ == '__main__':
    unittest.main()
