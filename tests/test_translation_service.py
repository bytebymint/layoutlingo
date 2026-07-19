import os
import re
import tempfile
import threading
import unittest
from unittest.mock import patch

import fitz

from app.services import translation_service


class LongDocumentTranslationTests(unittest.TestCase):
    def _create_pdf(self, path, pages):
        document = fitz.open()
        for page_number in range(1, pages + 1):
            page = document.new_page(width=612, height=792)
            page.insert_text(
                fitz.Point(72, 90),
                f'Page {page_number} sample text.',
                fontsize=11,
            )
        document.save(path)
        document.close()

    @staticmethod
    def _fake_translation(**kwargs):
        prompt = kwargs['user_prompt']
        items = re.findall(r'^(\d+)\. (.+)$', prompt, flags=re.MULTILINE)
        translated = []
        for item_number, text in items:
            page_number = re.search(r'\[\[KEEP_\d{3}\]\]', text).group(0)
            translated.append(
                f'{item_number}. Pagina traducida {page_number}.'
            )
        return '\n'.join(translated)

    @staticmethod
    def _fake_rtl_translation(translated_sample):
        def respond(**kwargs):
            markers = re.findall(r'\[\[KEEP_\d{3}\]\]', kwargs['user_prompt'])
            text = translated_sample.replace('ABC-123', markers[0]).replace('123.45', markers[1])
            return f'1. {text}'
        return respond

    def test_translates_55_page_pdf_and_reports_every_page(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'ebook.pdf')
            output_path = os.path.join(directory, 'ebook-es.pdf')
            self._create_pdf(source_path, 55)
            progress = []

            with patch.object(
                translation_service,
                'call_freemodel_chat',
                side_effect=self._fake_translation,
            ):
                translated_text = translation_service.translate_and_render_pdf(
                    source_path,
                    'en',
                    'es',
                    output_path,
                    progress_callback=lambda current, total, message: progress.append(
                        (current, total, message)
                    ),
                )

            self.assertTrue(os.path.exists(output_path))
            with fitz.open(output_path) as translated_pdf:
                self.assertEqual(len(translated_pdf), 55)
                self.assertIn('Pagina traducida 55', translated_pdf[54].get_text())

            self.assertIn('Pagina traducida 55', translated_text)
            completed_pages = {
                current
                for current, total, message in progress
                if total == 55 and message.startswith('Translated page')
            }
            self.assertEqual(completed_pages, set(range(1, 56)))
            self.assertFalse(any(name.endswith('.tmp.pdf') for name in os.listdir(directory)))

    def test_offline_literary_pages_checkpoint_sequentially(self):
        page_groups = [
            [{'text': f'Literary page {page_number}.'}]
            for page_number in range(1, 4)
        ]
        context = translation_service.create_translation_team_context(
            'en',
            'fa',
            provider_mode='offline_fast',
            quality_level='draft',
            enable_back_translation=False,
        )
        cache = {}
        progress = []
        translation_threads = []
        checkpoint_sizes = []
        original_save = translation_service._save_translation_checkpoint

        def fake_translate(texts, _source, _target, _api_key, local_cache, **kwargs):
            translation_threads.append(threading.get_ident())
            namespace = kwargs['cache_namespace']
            translated = [f'\u0635\u0641\u062d\u0647 {index + 1}' for index, _text in enumerate(texts)]
            for index, (source, target) in enumerate(zip(texts, translated)):
                local_cache[f'{namespace}:{index}:{source}'] = target
            return translated

        def save_and_record(path, translations, metadata):
            checkpoint_sizes.append(len(translations))
            return original_save(path, translations, metadata)

        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            checkpoint_path = os.path.join(directory, 'literary.checkpoint.json')
            with patch.object(
                translation_service,
                '_translate_batch',
                side_effect=fake_translate,
            ), patch.object(
                translation_service,
                '_save_translation_checkpoint',
                side_effect=save_and_record,
            ):
                translated_pages, _book_bible = translation_service._translate_literary_pages(
                    page_groups,
                    'en',
                    'fa',
                    api_key=None,
                    cache=cache,
                    checkpoint_path=checkpoint_path,
                    checkpoint_metadata={},
                    progress_reporter=lambda current, message: progress.append(
                        (current, message)
                    ),
                    team_context=context,
                )

            with open(checkpoint_path, encoding='utf-8') as checkpoint_file:
                payload = __import__('json').load(checkpoint_file)

        self.assertEqual(len(translated_pages), 3)
        self.assertEqual(checkpoint_sizes[-3:], [1, 2, 3])
        self.assertEqual(len(payload['translations']), 3)
        self.assertEqual(translation_threads, [threading.get_ident()] * 3)
        self.assertEqual(
            [current for current, message in progress if message.startswith('AI-translated')],
            [1, 2, 3],
        )

    def test_ltr_fit_uses_a_compact_safe_scale_for_longer_translations(self):
        """A longer LTR target should not fail solely because its source box was concise."""
        font = fitz.Font('helv')
        rect = fitz.Rect(0, 0, 90, 45)
        text = 'A detailed translated sentence needs more room than the source text. ' * 5

        with self.assertRaises(translation_service.TranslationLayoutError):
            translation_service._fit_text_to_rect(text, font, rect, 12, minimum_scale=0.55)

        lines, size, line_height = translation_service._fit_text_to_rect(
            text,
            font,
            rect,
            12,
            minimum_scale=0.32,
        )

        self.assertGreater(len(lines), 1)
        self.assertGreaterEqual(size, 3.2)
        self.assertLessEqual(len(lines) * line_height, rect.height + (size * 0.25))

    def test_rtl_languages_use_embedded_complex_script_fonts(self):
        samples = {
            'ar': 'مرحبا بالعالم. Invoice ABC-123 المبلغ ١٢٣٫٤٥ ريال.',
            'fa': 'سلام دنیا. Invoice ABC-123 شماره №۱۲۳ → مبلغ ۱۲۳٬۴۵۶ تومان است.',
            'he': 'שלום עולם. Invoice ABC-123 הסכום הוא 123.45 ש״ח.',
            'ur': 'سلام دنیا۔ Invoice ABC-123 رقم ۱۲۳٬۴۵۶ روپے ہے۔',
        }

        samples = {
            'ar': '\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645. Invoice ABC-123 \u0627\u0644\u0645\u0628\u0644\u063a 123.45 \u0631\u064a\u0627\u0644.',
            'fa': '\u0633\u0644\u0627\u0645 \u062f\u0646\u06cc\u0627. Invoice ABC-123 \u0634\u0645\u0627\u0631\u0647 \u2116123.45 \u2192 \u0645\u0628\u0644\u063a \u062a\u0648\u0645\u0627\u0646 \u0627\u0633\u062a.',
            'he': '\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd. Invoice ABC-123 \u05d4\u05e1\u05db\u05d5\u05dd \u05d4\u05d5\u05d0 123.45 \u05e9\"\u05d7.',
            'ur': '\u0633\u0644\u0627\u0645 \u062f\u0646\u06cc\u0627\u06d4 Invoice ABC-123 \u0631\u0642\u0645 123.45 \u0631\u0648\u067e\u06d2 \u06c1\u06d2\u06d4',
        }

        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'rtl-source.pdf')
            source_document = fitz.open()
            source_page = source_document.new_page(width=612, height=792)
            source_page.insert_text(fitz.Point(72, 90), 'Invoice ABC-123 123.45', fontsize=11)
            source_document.save(source_path)
            source_document.close()

            for language_code, translated_sample in samples.items():
                with self.subTest(language=language_code):
                    output_path = os.path.join(directory, f'rtl-{language_code}.pdf')
                    with patch.object(
                        translation_service,
                        'call_freemodel_chat',
                        side_effect=self._fake_rtl_translation(translated_sample),
                    ):
                        translated_text = translation_service.translate_and_render_pdf(
                            source_path,
                            'en',
                            language_code,
                            output_path,
                        )

                    self.assertEqual(translated_text, translated_sample)
                    with fitz.open(output_path) as translated_pdf:
                        page = translated_pdf[0]
                        extracted = page.get_text()
                        embedded_fonts = ' '.join(
                            str(field)
                            for font in page.get_fonts(full=True)
                            for field in font
                        )
                        self.assertIn('Noto', embedded_fonts)
                        self.assertIn('Invoice', extracted)
                        self.assertNotIn('?', extracted)
                        if language_code == 'fa':
                            self.assertIn('№', extracted)
                            self.assertIn('→', extracted)

    def test_provider_failure_does_not_publish_partial_pdf(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'ebook.pdf')
            output_path = os.path.join(directory, 'ebook-es.pdf')
            self._create_pdf(source_path, 2)

            with (
                patch.object(translation_service, 'call_freemodel_chat', return_value=None),
                patch.object(translation_service, '_API_RETRIES', 1),
                patch.object(translation_service, '_RETRY_BASE_SECONDS', 0),
            ):
                with self.assertRaises(translation_service.TranslationError):
                    translation_service.translate_and_render_pdf(
                        source_path,
                        'en',
                        'es',
                        output_path,
                    )

            self.assertFalse(os.path.exists(output_path))
            self.assertFalse(any(name.endswith('.tmp.pdf') for name in os.listdir(directory)))

    def test_invoice_facts_cannot_be_changed_by_a_provider_response(self):
        source = 'Invoice ZLX-2026-001: AED 799 due 9 July 2026.'
        protected, placeholders = translation_service._protect_factual_tokens(source)

        with self.assertRaises(translation_service.TranslationError):
            translation_service._restore_protected_tokens(
                source,
                f'\u0641\u0627\u06a9\u062a\u0648\u0631 {protected} AED 840',
                placeholders,
            )

    def test_fact_integrity_accepts_persian_and_arabic_indic_digits(self):
        source = 'Copywriting: 4 revision rounds, due in 2026.'

        translation_service._validate_fact_integrity(
            source,
            'کپی‌رایتینگ: ۴ دور ویرایش، سال ۲۰۲۶.',
        )
        translation_service._validate_fact_integrity(
            source,
            'الكتابة: ٤ جولات، الموعد ٢٠٢٦.',
        )

    def test_fact_integrity_still_rejects_a_different_localized_number(self):
        with self.assertRaisesRegex(
            translation_service.TranslationError,
            'protected business facts',
        ):
            translation_service._validate_fact_integrity(
                'Copywriting: 4 revision rounds.',
                'کپی‌رایتینگ: ۵ دور ویرایش.',
            )

    def test_publication_qa_ignores_punctuation_only_layout_markers(self):
        report = translation_service._automated_quality_report(
            [[{'text': '#'}, {'text': '_______________________'}]],
            [['#', '_______________________']],
            'fa',
            literary=False,
        )

        self.assertTrue(report['publication_ready'])
        self.assertEqual(report['issues'], [])

    def test_local_marker_recovery_reinserts_a_missing_invoice_fact(self):
        source = '30% off second month'
        _protected, placeholders = translation_service._protect_factual_tokens(source)

        recovered, used_recovery = translation_service._restore_local_tokens_with_recovery(
            source,
            '\u062a\u062e\u0641\u06cc\u0641 \u0645\u0627\u0647 \u062f\u0648\u0645',
            placeholders,
            'fa',
        )

        self.assertTrue(used_recovery)
        self.assertEqual(recovered, '\u062a\u062e\u0641\u06cc\u0641 \u0645\u0627\u0647 \u062f\u0648\u0645 30%')

    def test_local_marker_recovery_collapses_a_duplicate_protected_token(self):
        source = 'Chapter 40 ends on page 233.'
        _protected, placeholders = translation_service._protect_factual_tokens(source)

        restored, recovered = translation_service._restore_local_tokens_with_recovery(
            source,
            (
                '\u0641\u0635\u0644 [[KEEP_000]] \u062f\u0631 \u0635\u0641\u062d\u0647 [[KEEP_001]] '
                '[[KEEP_001]] \u062a\u0645\u0627\u0645 \u0645\u06cc\u200c\u0634\u0648\u062f.'
            ),
            placeholders,
            'fa',
        )

        self.assertTrue(recovered)
        self.assertEqual(translation_service._factual_tokens(restored), ['40', '233'])
        self.assertNotIn('KEEP_', restored)

    def test_local_marker_recovery_rejects_output_without_target_language_text(self):
        source = '30% off second month'
        _protected, placeholders = translation_service._protect_factual_tokens(source)

        with self.assertRaisesRegex(translation_service.TranslationError, 'target-language'):
            translation_service._restore_local_tokens_with_recovery(
                source,
                'Second month discount',
                placeholders,
                'fa',
            )

    def test_offline_batch_recovers_missing_marker_without_a_retry(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', provider_mode='offline', quality_level='draft'
        )
        with patch.object(
            translation_service,
            'call_local_chat',
            return_value='1. \u062a\u062e\u0641\u06cc\u0641 \u0645\u0627\u0647 \u062f\u0648\u0645',
        ) as provider:
            translated = translation_service._call_batch_api(
                ['30% off second month'],
                'en', 'fa', api_key='local-key', team_context=context,
            )

        self.assertEqual(translated, {0: '\u062a\u062e\u0641\u06cc\u0641 \u0645\u0627\u0647 \u062f\u0648\u0645 30%'})
        self.assertEqual(provider.call_count, 1)

    def test_offline_fast_uses_nllb_without_calling_aya(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', provider_mode='offline_fast', quality_level='draft'
        )
        with patch.object(
            translation_service,
            'translate_fast_batch',
            return_value=['\u0645\u062a\u0646 \u062a\u0631\u062c\u0645\u0647 \u0634\u062f\u0647'],
        ) as fast_engine, patch.object(
            translation_service,
            'call_local_chat',
        ) as aya_engine:
            translated = translation_service._call_batch_api(
                ['Source sentence'],
                'en',
                'fa',
                api_key='unused',
                team_context=context,
            )

        self.assertEqual(translated, {0: '\u0645\u062a\u0646 \u062a\u0631\u062c\u0645\u0647 \u0634\u062f\u0647'})
        fast_engine.assert_called_once()
        aya_engine.assert_not_called()

    def test_offline_fast_repairs_source_language_output_with_aya(self):
        """A bad short NLLB segment should be repaired, not fail the whole job."""
        context = translation_service.create_translation_team_context(
            'en', 'fa', provider_mode='offline_fast', quality_level='draft'
        )

        def reviewer_reply(**kwargs):
            marker = re.search(r'\[\[KEEP_\d{3}\]\]', kwargs['user_prompt']).group(0)
            return f'1. \u062a\u062e\u0641\u06cc\u0641 \u0645\u0627\u0647 \u062f\u0648\u0645 {marker}'

        with patch.object(
            translation_service,
            'translate_fast_batch',
            return_value=['Second month discount'],
        ) as fast_engine, patch.object(
            translation_service,
            'call_local_chat',
            side_effect=reviewer_reply,
        ) as aya_engine:
            translated = translation_service._call_batch_api(
                ['30% off second month'],
                'en',
                'fa',
                api_key='unused',
                team_context=context,
            )

        self.assertEqual(translated, {0: '\u062a\u062e\u0641\u06cc\u0641 \u0645\u0627\u0647 \u062f\u0648\u0645 30%'})
        fast_engine.assert_called_once()
        aya_engine.assert_called_once()
        self.assertEqual(context.metrics['fast_draft_repaired_by_reviewer_blocks'], 1)

    def test_offline_fast_repairs_a_final_language_qa_failure_with_aya(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', provider_mode='offline_fast', quality_level='draft'
        )
        repaired = '\u067e\u0631\u062f\u0627\u062e\u062a \u0641\u0631\u062f\u0627 \u0627\u0646\u062c\u0627\u0645 \u0645\u06cc\u200c\u0634\u0648\u062f.'

        with patch.object(
            translation_service,
            'translate_fast_batch',
            return_value=['Payment is due tomorrow.'],
        ), patch.object(
            translation_service,
            '_repair_fast_segment_with_local_reviewer',
            return_value=repaired,
        ) as reviewer:
            translated = translation_service._translate_chunk_resilient(
                ['Payment is due tomorrow.'],
                'en',
                'fa',
                api_key='unused',
                team_context=context,
            )

        self.assertEqual(translated, {0: repaired})
        reviewer.assert_called_once()
        self.assertEqual(context.metrics['fast_draft_qa_repaired_by_reviewer_blocks'], 1)

    def test_resilient_batch_can_isolate_every_item_for_automatic_repair(self):
        source_texts = [
            f'Source passage {chr(ord("a") + index)}.'
            for index in range(20)
        ]

        def provider(texts, *_args, **_kwargs):
            if len(texts) > 1:
                raise translation_service.TranslationError('Batch needs isolation.')
            return {0: 'Texto traducido.'}

        with patch.object(
            translation_service,
            '_call_batch_api',
            side_effect=provider,
        ):
            translated = translation_service._translate_chunk_resilient(
                source_texts,
                'en',
                'es',
                api_key='unused',
            )

        self.assertGreaterEqual(translation_service._MAX_BATCH_SPLIT_DEPTH, 5)
        self.assertEqual(len(translated), 20)
        self.assertEqual(set(translated.values()), {'Texto traducido.'})

    def test_fast_recovery_removes_an_invented_invoice_value(self):
        restored, recovered = translation_service._restore_fast_tokens_with_sanitization(
            'Total due: AED 840',
            '\u0645\u0628\u0644\u063a \u0642\u0627\u0628\u0644 \u067e\u0631\u062f\u0627\u062e\u062a AED 100',
            {},
            'fa',
        )

        self.assertTrue(recovered)
        self.assertEqual(restored, '\u0645\u0628\u0644\u063a \u0642\u0627\u0628\u0644 \u067e\u0631\u062f\u0627\u062e\u062a AED 840')

    def test_fast_recovery_accepts_number_word_rendered_as_persian_digits(self):
        source = 'I carried a forty-pound backpack.'
        translated = '\u0645\u0646 \u06cc\u06a9 \u06a9\u0648\u0644\u0647\u200c\u067e\u0634\u062a\u06cc \u06f4\u06f0 \u067e\u0648\u0646\u062f\u06cc \u062d\u0645\u0644 \u06a9\u0631\u062f\u0645.'

        restored, recovered = translation_service._restore_fast_tokens_with_sanitization(
            source,
            translated,
            {},
            'fa',
        )

        self.assertFalse(recovered)
        self.assertEqual(restored, translated)

    def test_fast_recovery_accepts_literary_quantifier_as_persian_digits(self):
        source = 'I surprised both guards and checked twice.'
        translated = (
            '\u0645\u0646 \u0647\u0631 \u06f2 \u0646\u06af\u0647\u0628\u0627\u0646 \u0631\u0627 \u063a\u0627\u0641\u0644\u06af\u06cc\u0631 \u06a9\u0631\u062f\u0645 '
            '\u0648 \u06f2 \u0628\u0627\u0631 \u0628\u0631\u0631\u0633\u06cc \u06a9\u0631\u062f\u0645.'
        )

        restored, recovered = translation_service._restore_fast_tokens_with_sanitization(
            source,
            translated,
            {},
            'fa',
        )

        self.assertFalse(recovered)
        self.assertEqual(restored, translated)

    def test_fast_recovery_sanitizes_an_unrecognized_localized_number(self):
        restored, recovered = translation_service._restore_fast_tokens_with_sanitization(
            'The guards surrounded me.',
            '\u06f2 \u0646\u06af\u0647\u0628\u0627\u0646 \u0645\u0631\u0627 \u0645\u062d\u0627\u0635\u0631\u0647 \u06a9\u0631\u062f\u0646\u062f.',
            {},
            'fa',
        )

        self.assertTrue(recovered)
        self.assertEqual(translation_service._factual_tokens(restored), [])
        self.assertIn('\u0646\u06af\u0647\u0628\u0627\u0646', restored)

    def test_fast_translation_routes_malformed_marker_artifacts_to_reviewer(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', provider_mode='offline_fast', quality_level='draft'
        )
        repaired = '\u0627\u06cc\u0646 \u0645\u062a\u0646 \u062a\u0631\u062c\u0645\u0647 \u0634\u062f\u0647 \u0627\u0633\u062a.'

        with patch.object(
            translation_service,
            'translate_fast_batch',
            return_value=['[[SKEEP:SKEEP_Book Step_Step_Step_Step'],
        ), patch.object(
            translation_service,
            '_repair_fast_segment_with_local_reviewer',
            return_value=repaired,
        ) as reviewer:
            translated = translation_service._call_batch_api(
                ['This text is translated.'],
                'en',
                'fa',
                api_key='unused',
                team_context=context,
            )

        self.assertEqual(translated, {0: repaired})
        reviewer.assert_called_once()

    def test_number_word_equivalence_does_not_allow_a_different_number(self):
        with self.assertRaisesRegex(
            translation_service.TranslationError,
            r"added \['41'\]",
        ):
            translation_service._validate_fact_integrity(
                'I carried a forty-pound backpack.',
                '\u0645\u0646 \u06cc\u06a9 \u06a9\u0648\u0644\u0647\u200c\u067e\u0634\u062a\u06cc \u06f4\u06f1 \u067e\u0648\u0646\u062f\u06cc \u062d\u0645\u0644 \u06a9\u0631\u062f\u0645.',
            )

    def test_fast_recovery_rebuilds_a_malformed_address_marker(self):
        source = 'Unit 601-606, 6th Floor'
        _protected, placeholders = translation_service._protect_factual_tokens(source)
        restored, recovered = translation_service._restore_fast_tokens_with_sanitization(
            source,
            '\u0648\u0627\u062d\u062f [[KEEP_BROKEN',
            placeholders,
            'fa',
        )

        self.assertTrue(recovered)
        self.assertEqual(restored, '\u0648\u0627\u062d\u062f 601 606 6th')

    def test_locked_glossary_term_is_enforced_for_cached_local_translation(self):
        source = 'One-time design, development & setup'
        approved_target = (
            '\u0637\u0631\u0627\u062d\u06cc\u060c \u062a\u0648\u0633\u0639\u0647 \u0648 '
            '\u062a\u0646\u0638\u06cc\u0645\u0627\u062a \u06cc\u06a9\u200c\u0628\u0627\u0631 \u0645\u0635\u0631\u0641'
        )
        context = translation_service.create_translation_team_context(
            'en',
            'fa',
            provider_mode='offline',
            resources={'glossary': [{
                'source_term': source,
                'target_term': approved_target,
                'authority': 'locked',
            }]},
        )
        translated = translation_service._translate_batch(
            [source],
            'en',
            'fa',
            api_key='unused',
            cache={source: '\u0637\u0631\u0627\u062d\u06cc \u0648 \u062a\u0648\u0633\u0639\u0647'},
            team_context=context,
        )

        self.assertEqual(translated, [approved_target])
        self.assertEqual(context.metrics['locked_glossary_enforcements'], 1)

    def test_spaced_uppercase_company_brand_is_protected(self):
        protected, placeholders = translation_service._protect_factual_tokens('ZELUR Y X')
        self.assertEqual(protected, '[[KEEP_000]]')
        self.assertEqual(placeholders['[[KEEP_000]]'], 'ZELUR Y X')

    def test_address_ordinals_are_protected_as_facts(self):
        protected, placeholders = translation_service._protect_factual_tokens(
            'Unit 601-606, 6th Floor'
        )
        self.assertEqual(
            protected,
            'Unit [[KEEP_000]]-[[KEEP_001]], [[KEEP_002]] Floor',
        )
        self.assertEqual(list(placeholders.values()), ['601', '606', '6th'])

    def test_local_marker_recovery_accepts_missing_outer_brackets(self):
        source = 'Total due: AED 840'
        _protected, placeholders = translation_service._protect_factual_tokens(source)

        for damaged in ('KEEP_000', '[KEEP_000]', '[[KEEP_000]]'):
            restored, recovered = translation_service._restore_local_tokens_with_recovery(
                source,
                f'\u0645\u0628\u0644\u063a \u0642\u0627\u0628\u0644 \u067e\u0631\u062f\u0627\u062e\u062a: {damaged}',
                placeholders,
                'fa',
            )
            self.assertEqual(restored, '\u0645\u0628\u0644\u063a \u0642\u0627\u0628\u0644 \u067e\u0631\u062f\u0627\u062e\u062a: AED 840')
            self.assertEqual(recovered, damaged != '[[KEEP_000]]')

    def test_final_pdf_validation_rejects_leaked_protected_marker(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            output_path = os.path.join(directory, 'leaked-marker.pdf')
            document = fitz.open()
            page = document.new_page()
            page.insert_text(fitz.Point(72, 90), 'Translated [KEEP_000] text')
            document.save(output_path)
            document.close()

            with self.assertRaisesRegex(
                translation_service.TranslationError,
                'unresolved internal protected token',
            ):
                translation_service._validate_translated_pdf(output_path, 1)

    def test_final_pdf_validation_rejects_malformed_marker_artifacts(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            output_path = os.path.join(directory, 'malformed-marker.pdf')
            document = fitz.open()
            page = document.new_page()
            page.insert_text(
                fitz.Point(72, 90),
                '[[SKEEP:SKEEP_Book Step_Step_Step_Step',
            )
            document.save(output_path)
            document.close()

            with self.assertRaisesRegex(
                translation_service.TranslationError,
                'unresolved internal protected token',
            ):
                translation_service._validate_translated_pdf(output_path, 1)

    def test_fact_validation_rejects_a_stale_marker_from_translation_memory(self):
        with self.assertRaisesRegex(
            translation_service.TranslationError,
            'unresolved internal protected token',
        ):
            translation_service._validate_fact_integrity(
                'The approved total is AED 840.',
                '\u0645\u0628\u0644\u063a \u0646\u0647\u0627\u06cc\u06cc KEEP_000 \u0627\u0633\u062a.',
            )

    def test_fact_only_invoice_cells_bypass_the_language_model(self):
        facts = [
            'AED 24,100',
            '16 May 2026',
            'ZELUR Y X',
            'info@zeluryx.com',
            'orthodontic.ae',
        ]
        with patch.object(translation_service, 'call_freemodel_chat') as provider:
            translated = translation_service._translate_batch(
                facts,
                'en',
                'fa',
                api_key='unused',
                cache={},
            )

        self.assertEqual(translated, facts)
        provider.assert_not_called()

    def test_rtl_factual_values_are_isolated_left_to_right(self):
        html = translation_service._rtl_text_html(
            '\u0645\u0628\u0644\u063a AED 799 \u062f\u0631 9 July 2026',
            'fa',
        )
        self.assertEqual(html.count('rtl-protected-fact'), 2)
        self.assertIn('dir="ltr"', html)

    def test_rtl_layout_expands_instead_of_failing_on_tight_regions(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'tight-rtl-source.pdf')
            output_path = os.path.join(directory, 'tight-rtl-fa.pdf')
            document = fitz.open()
            page = document.new_page(width=260, height=180)
            page.insert_text(fitz.Point(34, 54), 'Total', fontsize=11)
            document.save(source_path)
            document.close()

            long_farsi = (
                '\u0627\u06cc\u0646 \u0645\u062a\u0646 \u0641\u0627\u0631\u0633\u06cc '
                '\u0637\u0648\u0644\u0627\u0646\u06cc \u0627\u0633\u062a \u0648 '
                '\u0628\u0627\u06cc\u062f \u0628\u062f\u0648\u0646 \u062e\u0637\u0627 '
                '\u062f\u0631 \u0635\u0641\u062d\u0647 \u0642\u0631\u0627\u0631 '
                '\u0628\u06af\u06cc\u0631\u062f.'
            )

            def provider(**_kwargs):
                return f'1. {long_farsi}'

            with patch.object(translation_service, 'call_freemodel_chat', side_effect=provider):
                translated_text = translation_service.translate_and_render_pdf(
                    source_path,
                    'en',
                    'fa',
                    output_path,
                )

            self.assertEqual(translated_text, long_farsi)
            with fitz.open(output_path) as translated_pdf:
                self.assertEqual(len(translated_pdf), 1)
                self.assertTrue(translated_pdf[0].get_text().strip())

    def test_table_cells_are_extracted_independently(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'invoice-cells.pdf')
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.insert_text(fitz.Point(50, 100), 'Custom website design', fontsize=9)
            page.insert_text(fitz.Point(360, 100), 'AED 18,000', fontsize=9)
            page.insert_text(fitz.Point(450, 100), 'Waived', fontsize=9)
            document.save(source_path)
            document.close()

            with fitz.open(source_path) as extracted_pdf:
                groups = translation_service._collect_text_groups(extracted_pdf[0])

            self.assertEqual([group['text'] for group in groups], [
                'Custom website design', 'AED 18,000', 'Waived',
            ])
            self.assertLess(groups[0]['bbox'][2], groups[1]['bbox'][0])
            self.assertLess(groups[1]['bbox'][2], groups[2]['bbox'][0])

    def test_cancelled_job_resumes_from_translation_checkpoint(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'checkpoint-source.pdf')
            output_path = os.path.join(directory, 'checkpoint-output.pdf')
            checkpoint_path = os.path.join(directory, 'translation.checkpoint.json')
            self._create_pdf(source_path, 3)
            cancel_state = {'requested': False}

            def progress(current, _total, _message):
                if current == 1:
                    cancel_state['requested'] = True

            with patch.object(
                translation_service,
                'call_freemodel_chat',
                side_effect=self._fake_translation,
            ) as first_provider:
                with self.assertRaises(translation_service.TranslationCancelled):
                    translation_service.translate_and_render_pdf(
                        source_path,
                        'en',
                        'es',
                        output_path,
                        progress_callback=progress,
                        cancel_callback=lambda: cancel_state['requested'],
                        checkpoint_path=checkpoint_path,
                    )

            self.assertEqual(first_provider.call_count, 1)
            self.assertTrue(os.path.exists(checkpoint_path))
            self.assertFalse(os.path.exists(output_path))

            with patch.object(
                translation_service,
                'call_freemodel_chat',
                side_effect=self._fake_translation,
            ) as resumed_provider:
                translated_text = translation_service.translate_and_render_pdf(
                    source_path,
                    'en',
                    'es',
                    output_path,
                    checkpoint_path=checkpoint_path,
                )

            self.assertEqual(resumed_provider.call_count, 2)
            self.assertTrue(os.path.exists(output_path))
            self.assertFalse(os.path.exists(checkpoint_path))

    def test_literary_mode_builds_bible_edits_prose_and_reports_ai_qa(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'story.pdf')
            output_path = os.path.join(directory, 'story-fa.pdf')
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            page.draw_rect(fitz.Rect(40, 40, 572, 140), color=(0.2, 0.4, 0.6))
            page.insert_text(fitz.Point(72, 90), 'Night was calm.', fontsize=11)
            document.save(source_path)
            document.close()

            def literary_provider(**kwargs):
                system_prompt = kwargs['system_prompt']
                if 'style sheet' in system_prompt:
                    return (
                        '{"genre":"fantasy","tone":"quiet","narrative_voice":"third person",'
                        '"style_rules":["natural Persian"],"characters":[],"places":[],"terms":[]}'
                    )
                if 'literary editor' in system_prompt:
                    return '1. \u0634\u0628 \u0622\u0631\u0627\u0645 \u0628\u0648\u062f.'
                return '1. \u0634\u0628 \u0622\u0631\u0627\u0645 \u0628\u0648\u062f.'

            quality = {}
            with (
                patch.object(
                    translation_service,
                    'call_freemodel_chat',
                    side_effect=literary_provider,
                ) as provider,
                patch.object(translation_service, '_PAGE_WORKERS', 1),
            ):
                translated_text = translation_service.translate_and_render_pdf(
                    source_path,
                    'en',
                    'fa',
                    output_path,
                    translation_mode='literary',
                    quality_callback=quality.update,
                )

            self.assertEqual(provider.call_count, 3)
            self.assertIn('\u0634\u0628 \u0622\u0631\u0627\u0645 \u0628\u0648\u062f', translated_text)
            self.assertEqual(quality['mode'], 'literary')
            self.assertTrue(quality['ai_editor_pass'])
            self.assertFalse(quality['human_review_required'])
            self.assertGreaterEqual(quality['target_script_ratio'], 0.95)
            with fitz.open(output_path) as translated_pdf:
                self.assertEqual(len(translated_pdf), 1)
                self.assertTrue(translated_pdf[0].get_drawings())

    def test_translates_and_ai_edits_a_150_page_farsi_story(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'long-story.pdf')
            output_path = os.path.join(directory, 'long-story-fa.pdf')
            checkpoint_path = os.path.join(directory, 'long-story.checkpoint.json')
            document = fitz.open()
            for _page_number in range(150):
                page = document.new_page(width=612, height=792)
                page.insert_text(fitz.Point(72, 90), 'The moon rose.', fontsize=11)
            document.save(source_path)
            document.close()

            def literary_provider(**kwargs):
                if 'style sheet' in kwargs['system_prompt']:
                    return (
                        '{"genre":"story","tone":"calm","narrative_voice":"third person",'
                        '"style_rules":[],"characters":[],"places":[],"terms":[]}'
                    )
                return '1. \u0645\u0627\u0647 \u0628\u0631\u0622\u0645\u062f.'

            quality = {}
            with (
                patch.object(
                    translation_service,
                    'call_freemodel_chat',
                    side_effect=literary_provider,
                ) as provider,
                patch.object(translation_service, '_PAGE_WORKERS', 4),
            ):
                translated_text = translation_service.translate_and_render_pdf(
                    source_path,
                    'en',
                    'fa',
                    output_path,
                    checkpoint_path=checkpoint_path,
                    translation_mode='auto',
                    quality_callback=quality.update,
                )

            self.assertEqual(provider.call_count, 301)
            self.assertIn('\u0645\u0627\u0647', translated_text)
            self.assertEqual(quality['blocks_checked'], 150)
            self.assertEqual(quality['unchanged_blocks'], 0)
            self.assertFalse(os.path.exists(checkpoint_path))
            with fitz.open(output_path) as translated_pdf:
                self.assertEqual(len(translated_pdf), 150)
                self.assertTrue(translated_pdf[149].get_text().strip())

    def test_literary_qa_rejects_substantial_english_leakage_in_farsi(self):
        with self.assertRaises(translation_service.TranslationError):
            translation_service._validate_target_language_batch(
                {0: 'This paragraph was not actually translated into Persian.'},
                'fa',
            )

    def test_target_script_qa_allows_farsi_with_required_latin_proper_name(self):
        translation_service._validate_target_language_batch(
            {0: '\u0645\u0628\u0644\u063a \u0634\u0631\u06a9\u062a Zeluryx \u062a\u0627\u06cc\u06cc\u062f \u0634\u062f.'},
            'fa',
        )

    def test_target_script_qa_allows_multi_word_location_name(self):
        translation_service._validate_target_language_batch(
            {0: '\u0645\u062d\u0644 \u0635\u062f\u0648\u0631 \u0633\u0646\u062f: Dubai, United Arab Emirates'},
            'fa',
        )

    def test_target_script_qa_allows_technical_product_names_in_farsi(self):
        translation_service._validate_target_language_batch(
            {0: (
                '\u067e\u0644\u062a\u0641\u0631\u0645 \u0628\u0627 Next.js \u0648 React '
                '\u0628\u0631\u0627\u06cc \u062a\u0648\u0633\u0639\u0647 \u0648\u0628\u200c\u0633\u0627\u06cc\u062a '
                '\u067e\u06cc\u0627\u062f\u0647\u200c\u0633\u0627\u0632\u06cc \u0645\u06cc\u200c\u0634\u0648\u062f.'
            )},
            'fa',
        )

    def test_target_script_qa_allows_latin_only_technical_stack_label(self):
        source = 'Next.js (React), Payload CMS, Cloudflare'
        translation_service._validate_target_language_batch(
            {0: source},
            'fa',
            [source],
        )

    def test_target_script_qa_allows_security_acronym_label(self):
        source = 'CDN, WAF, DDoS Protection'
        translation_service._validate_target_language_batch(
            {0: source},
            'fa',
            [source],
        )

    def test_target_script_qa_allows_title_case_product_label_from_source(self):
        source = 'Next.js CoreCare Platform'
        translation_service._validate_target_language_batch(
            {0: source},
            'fa',
            [source],
        )

    def test_target_script_qa_allows_parenthesized_compound_feature_name(self):
        source = (
            'WhatsApp Business click-to-chat: floating button site-wide plus '
            'inline links, with pre-filled messages.'
        )
        target = (
            'دکمه شناور واتساپ بیزینس '
            '(Click-to-Chat) در سراسر وب‌سایت '
            'به‌همراه پیوندهای داخلی و '
            'پیام‌های ازپیش‌تکمیل‌شده.'
        )

        translation_service._validate_target_language_batch(
            {0: target},
            'fa',
            [source],
        )

    def test_target_script_qa_allows_source_schema_identifiers(self):
        source = 'Schema markup: FAQPage, Review/AggregateRating, LocalBusiness'
        target = (
            'نشان‌گذاری اسکیما: FAQPage، '
            'Review/AggregateRating، LocalBusiness'
        )

        translation_service._validate_target_language_batch(
            {0: target},
            'fa',
            [source],
        )

    def test_target_script_qa_allows_english_words_inside_farsi(self):
        translation_service._validate_target_language_batch(
            {0: (
                'این بخش شامل alpha beta gamma delta '
                'است و توضیحات فارسی نیز دارد.'
            )},
            'fa',
            ['This section includes alpha beta gamma delta and Persian explanation.'],
        )

    def test_human_approved_text_may_contain_only_english_words(self):
        translation_service.validate_human_translation(
            'Schema markup: FAQPage and LocalBusiness.',
            'Keep FAQPage and LocalBusiness in English.',
            'fa',
        )

    def test_human_approved_checkpoint_bypasses_language_gate_and_publication_veto(self):
        source = 'Schema markup: FAQPage and LocalBusiness.'
        approved = 'Keep FAQPage and LocalBusiness in English.'
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='technical', provider_mode='offline_quality'
        )
        cache = {
            translation_service._human_review_cache_key(source): approved,
        }

        with patch.object(translation_service, '_translate_chunk_resilient') as provider:
            translated = translation_service._translate_batch(
                [source],
                'en',
                'fa',
                api_key='local-key',
                cache=cache,
                team_context=context,
            )

        provider.assert_not_called()
        self.assertEqual(translated, [approved])
        report = translation_service._automated_quality_report(
            [[{'text': source}]],
            [[approved]],
            'fa',
            literary=False,
            team_context=context,
        )
        self.assertTrue(report['publication_ready'])

    def test_parenthesized_feature_name_does_not_hide_english_prose(self):
        source = (
            'WhatsApp Business click-to-chat: floating button site-wide plus '
            'inline links, with pre-filled messages.'
        )
        with self.assertRaisesRegex(
            translation_service.TranslationItemQualityError,
            'English leakage',
        ):
            translation_service._validate_target_language_batch(
                {0: '(Click-to-Chat) floating button with pre-filled links remains untranslated.'},
                'fa',
                [source],
            )

    def test_target_script_qa_rejects_latin_only_english_prose(self):
        source = 'Next.js is used to build this website quickly'
        with self.assertRaisesRegex(
            translation_service.TranslationItemQualityError,
            'English leakage',
        ) as raised:
            translation_service._validate_target_language_batch(
                {0: source},
                'fa',
                [source],
            )
        self.assertEqual(raised.exception.source_text, source)
        self.assertEqual(raised.exception.item_index, 0)

    def test_human_review_override_survives_a_namespaced_literary_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint_path = os.path.join(directory, 'review.checkpoint.json')
            source = 'The garden is quiet.'
            target = 'باغ آرام است.'
            translation_service._save_translation_checkpoint(
                checkpoint_path,
                {f'book-page-2:0:{source}': 'The garden is quiet.'},
                {'translation_mode': 'literary'},
            )

            applied = translation_service.apply_human_review_corrections(
                checkpoint_path,
                [{'source_text': source, 'target_text': target}],
            )
            cache = translation_service._load_translation_checkpoint(checkpoint_path)

            self.assertEqual(applied, 1)
            self.assertEqual(cache[f'book-page-2:0:{source}'], target)
            self.assertEqual(cache[translation_service._human_review_cache_key(source)], target)

    def test_target_script_qa_rejects_english_sentence_around_technical_names(self):
        with self.assertRaisesRegex(translation_service.TranslationError, 'English leakage'):
            translation_service._validate_target_language_batch(
                {0: 'Next.js and React are used to build this entire untranslated sentence.'},
                'fa',
            )

    def test_target_script_qa_still_rejects_untranslated_english(self):
        with self.assertRaisesRegex(
            translation_service.TranslationError,
            'English leakage',
        ):
            translation_service._validate_target_language_batch(
                {0: 'Payment is due tomorrow.'},
                'fa',
            )

    def test_literary_cache_retranslates_farsi_with_english_leakage(self):
        cache = {'book-page-1:0:The moon rose.': 'ماه rose with a cold light.'}

        with patch.object(
            translation_service,
            '_translate_chunk_resilient',
            return_value={0: 'ماه با نوری سرد بالا آمد.'},
        ) as translator:
            translated = translation_service._translate_batch(
                ['The moon rose.'],
                'en',
                'fa',
                api_key='test-provider-key',
                cache=cache,
                literary_quality=True,
                cache_namespace='book-page-1',
            )

        self.assertEqual(translated, ['ماه با نوری سرد بالا آمد.'])
        self.assertEqual(cache['book-page-1:0:The moon rose.'], 'ماه با نوری سرد بالا آمد.')
        translator.assert_called_once()

    def test_partial_translation_batch_retries_only_missing_items(self):
        responses = [
            '1. uno\n2. dos',
            '1. uno\n2. dos',
            '1. tres',
        ]

        with (
            patch.object(translation_service, 'call_freemodel_chat', side_effect=responses) as provider,
            patch.object(translation_service, '_API_RETRIES', 2),
            patch.object(translation_service, '_RETRY_BASE_SECONDS', 0),
        ):
            translated = translation_service._call_batch_api(
                ['one', 'two', 'three'],
                'en',
                'es',
                api_key='test-provider-key',
            )

        self.assertEqual(translated, {0: 'uno', 1: 'dos', 2: 'tres'})
        self.assertEqual(provider.call_count, 3)

    def test_partial_literary_editor_batch_keeps_draft_for_missing_items(self):
        responses = [
            '1. Uno pulido\n2. Dos pulido',
            '1. Uno pulido\n2. Dos pulido',
            '1. Tres pulido',
        ]

        with (
            patch.object(translation_service, 'call_freemodel_chat', side_effect=responses) as provider,
            patch.object(translation_service, '_API_RETRIES', 2),
            patch.object(translation_service, '_RETRY_BASE_SECONDS', 0),
        ):
            polished = translation_service._polish_translation_chunk(
                ['one', 'two', 'three'],
                {0: 'uno', 1: 'dos', 2: 'tres'},
                'en',
                'es',
                api_key='test-provider-key',
                translation_context='',
            )

        self.assertEqual(
            polished,
            {0: 'Uno pulido', 1: 'Dos pulido', 2: 'tres'},
        )
        self.assertEqual(provider.call_count, 1)

    def test_literary_extraction_merges_visual_lines_into_a_prose_block(self):
        document = fitz.open()
        page = document.new_page(width=300, height=300)
        page.insert_textbox(
            fitz.Rect(40, 40, 180, 130),
            'The first sentence continues onto another visual line.',
            fontsize=11,
        )

        groups = translation_service._collect_text_groups(page, literary=True)

        self.assertEqual(len(groups), 1)
        self.assertNotIn('\n', groups[0]['text'])
        self.assertIn('continues onto', groups[0]['text'])
        document.close()

    def test_pdf_span_text_repairs_known_embedded_font_ligatures(self):
        def span(font, ratio, prefix='', suffix=''):
            size = 10.0
            chars = [
                {'c': character, 'bbox': (0, 0, 4, 10)}
                for character in prefix
            ]
            chars.append({'c': '\ufffd', 'bbox': (0, 0, ratio * size, 10)})
            chars.extend(
                {'c': character, 'bbox': (0, 0, 4, 10)}
                for character in suffix
            )
            return {'font': font, 'size': size, 'chars': chars}

        self.assertEqual(
            translation_service._pdf_span_text(
                span('EBGaramond-Regular', 0.518, suffix='ve')
            ),
            'five',
        )
        self.assertEqual(
            translation_service._pdf_span_text(
                span('EBGaramond-Regular', 0.575, prefix='di', suffix='erent')
            ),
            'different',
        )
        self.assertEqual(
            translation_service._pdf_span_text(
                span('EBGaramond-Regular', 0.506, suffix='atbed')
            ),
            'flatbed',
        )
        self.assertEqual(
            translation_service._pdf_span_text(
                span('EBGaramond-Regular', 0.761, prefix='gru', suffix='y')
            ),
            'gruffly',
        )
        self.assertEqual(
            translation_service._pdf_span_text(
                span('EBGaramond-Regular', 0.776, prefix='o', suffix='ce')
            ),
            'office',
        )

    def test_rtl_document_mode_translates_wrapped_text_as_one_block(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            source_path = os.path.join(directory, 'wrapped-document.pdf')
            output_path = os.path.join(directory, 'wrapped-document-fa.pdf')
            document = fitz.open()
            page = document.new_page(width=300, height=300)
            page.insert_textbox(
                fitz.Rect(40, 40, 180, 130),
                'This professional paragraph wraps across several visual lines '
                'but remains one translation and layout unit.',
                fontsize=11,
            )
            document.save(source_path)
            document.close()
            item_counts = []

            def provider(**kwargs):
                items = re.findall(r'^(\d+)\. (.+)$', kwargs['user_prompt'], re.MULTILINE)
                item_counts.append(len(items))
                return '\n'.join(
                    f'{number}. \u0627\u06cc\u0646 \u0645\u062a\u0646 \u062d\u0631\u0641\u0647\u200c\u0627\u06cc \u0628\u0647 \u0641\u0627\u0631\u0633\u06cc \u062a\u0631\u062c\u0645\u0647 \u0634\u062f\u0647 \u0627\u0633\u062a.'
                    for number, _text in items
                )

            with patch.object(
                translation_service,
                'call_freemodel_chat',
                side_effect=provider,
            ):
                translation_service.translate_and_render_pdf(
                    source_path,
                    'en',
                    'fa',
                    output_path,
                    translation_mode='document',
                )

            self.assertEqual(item_counts, [1])
            with fitz.open(output_path) as translated_pdf:
                self.assertEqual(len(translated_pdf), 1)
                self.assertTrue(translated_pdf[0].get_text().strip())

    def test_professional_team_uses_approved_translation_memory_without_provider(self):
        context = translation_service.create_translation_team_context(
            'en',
            'es',
            resources={
                'memory': [{
                    'source_text': 'Payment is due tomorrow.',
                    'target_text': 'El pago vence mañana.',
                    'quality_score': 99.0,
                }],
            },
        )

        with patch.object(translation_service, 'call_freemodel_chat') as provider:
            translated = translation_service._translate_batch(
                ['Payment is due tomorrow.'],
                'en',
                'es',
                api_key='test-provider-key',
                cache={},
                team_context=context,
            )

        self.assertEqual(translated, ['El pago vence mañana.'])
        self.assertEqual(context.metrics['translation_memory_hits'], 1)
        provider.assert_not_called()

    def test_professional_team_runs_editor_and_independent_semantic_reviewer(self):
        context = translation_service.create_translation_team_context(
            'en',
            'es',
            domain='technical',
        )

        with patch.object(
            translation_service,
            'call_freemodel_chat',
            return_value='1. El sistema está disponible.',
        ) as provider:
            translated = translation_service._translate_chunk_resilient(
                ['The system is available.'],
                'en',
                'es',
                api_key='test-provider-key',
                team_context=context,
            )

        self.assertEqual(translated, {0: 'El sistema está disponible.'})
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(context.metrics['editor_blocks'], 1)
        self.assertEqual(context.metrics['semantic_reviewer_blocks'], 1)

    def test_offline_editor_rejects_broken_fact_markers_and_keeps_valid_draft(self):
        context = translation_service.create_translation_team_context(
            'en',
            'fa',
            domain='financial',
            provider_mode='offline',
        )
        source = 'Total due: AED 840'
        draft = '\u0645\u0628\u0644\u063a \u0642\u0627\u0628\u0644 \u067e\u0631\u062f\u0627\u062e\u062a: AED 840'

        with patch.object(
            translation_service,
            'call_local_chat',
            return_value='1. \u0645\u0628\u0644\u063a \u0642\u0627\u0628\u0644 \u067e\u0631\u062f\u0627\u062e\u062a',
        ) as provider:
            revised = translation_service._professional_revision_pass(
                [source],
                {0: draft},
                'en',
                'fa',
                api_key='local-key',
                translation_context='',
                team_context=context,
                stage='semantic_reviewer',
            )

        self.assertEqual(revised, {0: draft})
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(context.metrics['semantic_reviewer_fallback_blocks'], 1)
        self.assertEqual(context.issues[0]['category'], 'qa_stage_fallback')

    def test_single_unnumbered_title_response_is_recovered(self):
        title = '\u0641\u0635\u0644 \u067e\u0646\u062c\u0645: \u062c\u0632\u06cc\u0631\u0647 \u062f\u0631 \u0645\u0647'

        self.assertEqual(
            translation_service._parse_numbered_response(title, 1),
            {0: title},
        )

    def test_offline_quality_reviews_short_literary_heading_individually(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='literary', provider_mode='offline_quality'
        )
        source = 'Chapter Five: The Island in Fog'
        approved = '\u0641\u0635\u0644 \u067e\u0646\u062c\u0645: \u062c\u0632\u06cc\u0631\u0647 \u062f\u0631 \u0645\u0647'

        with patch.object(
            translation_service,
            'call_local_chat',
            return_value=f'1. {approved}',
        ) as provider:
            translated = translation_service._targeted_fast_review(
                [source],
                {0: '\u0641\u0635\u0644 \u067e\u0646\u062c\u0645: \u062c\u0632\u06cc\u0631\u0647 \u062f\u0631 \u062a\u0646\u062f\u0628\u0627\u062f'},
                'en',
                'fa',
                api_key='local-key',
                translation_context='',
                team_context=context,
                literary=True,
            )

        self.assertEqual(translated, {0: approved})
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(context.metrics['mandatory_title_review_requested'], 1)
        self.assertEqual(context.metrics['mandatory_title_reviewed'], 1)

    def test_literary_gate_rejects_unreviewed_or_semantically_wrong_heading(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='literary', provider_mode='offline_quality'
        )
        source = 'Chapter Eight: The Room Beneath the Lighthouse'
        wrong = '\u0641\u0635\u0644 \u0647\u0634\u062a\u0645: \u0627\u062a\u0627\u0642 \u0632\u06cc\u0631 \u0645\u0634\u0639\u0644'

        with patch.object(
            translation_service,
            'call_local_chat',
            return_value=f'1. {wrong}',
        ):
            translation_service._targeted_fast_review(
                [source],
                {0: wrong},
                'en',
                'fa',
                api_key='local-key',
                translation_context='',
                team_context=context,
                literary=True,
            )

        with self.assertRaisesRegex(
            translation_service.TranslationQualityError,
            'Publication gate rejected',
        ):
            translation_service._automated_quality_report(
                [[{'text': source}]],
                [[wrong]],
                'fa',
                literary=True,
                team_context=context,
            )

    def test_final_literary_recovery_repairs_rejected_heading(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='literary', provider_mode='offline_quality'
        )
        source = 'Chapter Nine: The Last Tide'
        wrong = '\u0641\u0635\u0644 \u0646\u0647\u0645: \u0622\u062e\u0631\u06cc\u0646 \u0637\u0648\u0641\u0627\u0646'
        approved = '\u0641\u0635\u0644 \u0646\u0647\u0645: \u0622\u062e\u0631\u06cc\u0646 \u062c\u0632\u0631 \u0648 \u0645\u062f'
        context.add_issue(
            category='qa_stage_fallback_item',
            stage='semantic_reviewer',
            severity='warning',
            message='Reviewer omitted the item.',
            source_excerpt=source,
            target_excerpt=wrong,
        )

        with patch.object(
            translation_service,
            'call_local_chat',
            return_value=f'1. {approved}',
        ):
            recovered = translation_service._final_literary_recovery(
                [[{'text': source}]],
                [[wrong]],
                'en',
                'fa',
                api_key='local-key',
                book_bible={},
                team_context=context,
            )

        self.assertEqual(recovered, [[approved]])
        self.assertEqual(context.metrics['final_recovery_reviewer_blocks'], 1)
        self.assertTrue(context.issues[0]['resolved'])

    def test_fast_literary_review_skips_headings_that_pass_meaning_guards(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='literary', provider_mode='offline_quality'
        )
        source = 'Chapter Two: The Last Tide'
        draft = 'فصل دوم: آخرین جزر و مد'

        with patch.object(translation_service, 'call_local_chat') as provider:
            translated = translation_service._targeted_fast_review(
                [source], {0: draft}, 'en', 'fa', 'local-key', '', context,
                literary=True,
            )

        self.assertEqual(translated, {0: draft})
        provider.assert_not_called()
        self.assertEqual(context.metrics['deterministic_title_qa_blocks'], 1)

    def test_final_literary_recovery_only_revisits_recorded_failures(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='literary', provider_mode='offline_quality'
        )
        source = 'Chapter Nine: The Last Tide'
        wrong = 'فصل نهم: آخرین طوفان'

        with patch.object(translation_service, 'call_local_chat') as provider:
            recovered = translation_service._final_literary_recovery(
                [[{'text': source}]], [[wrong]], 'en', 'fa', 'local-key', {}, context,
            )

        self.assertEqual(recovered, [[wrong]])
        provider.assert_not_called()

    def test_final_literary_recovery_is_bounded_checkpointed_and_visible(self):
        context = translation_service.create_translation_team_context(
            'en', 'fa', domain='literary', provider_mode='offline_quality'
        )
        sources = ['A first passage.', 'A second passage.', 'A third passage.']
        drafts = ['متن اول', 'متن دوم', 'متن سوم']
        for source, draft in zip(sources, drafts):
            context.add_issue(
                category='qa_stage_fallback_item',
                stage='semantic_reviewer',
                severity='warning',
                message='Reviewer omitted this item.',
                source_excerpt=source,
                target_excerpt=draft,
            )
        progress = []
        cache = {}

        def approved_review(_sources, _drafts, *_args, **_kwargs):
            context.record_stage('final_recovery_reviewer', 1)
            return {0: 'ترجمه بازبینی‌شده'}

        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as directory:
            checkpoint_path = os.path.join(directory, 'recovery.checkpoint.json')
            with patch.object(
                translation_service,
                '_FINAL_LITERARY_RECOVERY_MAX_ITEMS',
                2,
            ), patch.object(
                translation_service,
                '_professional_revision_pass',
                side_effect=approved_review,
            ) as reviewer:
                recovered = translation_service._final_literary_recovery(
                    [[{'text': source} for source in sources]],
                    [drafts],
                    'en',
                    'fa',
                    'local-key',
                    {},
                    context,
                    cache=cache,
                    checkpoint_path=checkpoint_path,
                    checkpoint_metadata={'translation_id': 99},
                    progress_reporter=lambda page, message: progress.append((page, message)),
                )

            self.assertEqual(reviewer.call_count, 2)
            self.assertEqual(
                recovered[0][:2],
                ['ترجمه بازبینی‌شده'] * 2,
            )
            self.assertEqual(context.metrics['final_literary_recovery_deferred'], 1)
            self.assertTrue(os.path.isfile(checkpoint_path))
            self.assertEqual(len(cache), 2)
            self.assertEqual(len(progress), 2)
            self.assertIn('1 of 2', progress[0][1])

    def test_offline_document_mode_uses_deterministic_qa_after_draft(self):
        context = translation_service.create_translation_team_context(
            'en',
            'fa',
            domain='financial',
            provider_mode='offline',
            enable_back_translation=True,
        )
        translated_text = '\u0633\u06cc\u0633\u062a\u0645 \u062f\u0631 \u062f\u0633\u062a\u0631\u0633 \u0627\u0633\u062a.'

        with patch.object(
            translation_service,
            'call_local_chat',
            return_value=f'1. {translated_text}',
        ) as provider:
            translated = translation_service._translate_chunk_resilient(
                ['The system is available.'],
                'en',
                'fa',
                api_key='local-key',
                team_context=context,
            )

        self.assertEqual(translated, {0: translated_text})
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(context.metrics['editor_blocks'], 0)
        self.assertEqual(context.metrics['semantic_reviewer_blocks'], 0)
        self.assertEqual(context.metrics['deterministic_qa_blocks'], 1)
        self.assertEqual(context.metrics['back_translation_blocks'], 0)

    def test_offline_invalid_intake_json_uses_deterministic_brief(self):
        context = translation_service.create_translation_team_context(
            'en',
            'fa',
            domain='financial',
            provider_mode='offline',
        )

        with patch.object(
            translation_service,
            'call_local_chat',
            return_value='not json',
        ) as provider:
            brief = translation_service._build_translation_brief(
                [[{'text': 'Payment is due tomorrow.'}]],
                'en',
                'fa',
                api_key='local-key',
                team_context=context,
            )

        self.assertEqual(brief['domain'], 'financial')
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(context.metrics['intake_analysis_fallback_blocks'], 1)

    def test_high_risk_team_runs_back_translation_and_reconciliation(self):
        context = translation_service.create_translation_team_context(
            'en',
            'es',
            domain='legal',
            enable_back_translation=True,
        )

        def provider(**kwargs):
            if 'independent back-translator' in kwargs['system_prompt']:
                return '1. Payment is due tomorrow.'
            return '1. El pago vence mañana.'

        with patch.object(
            translation_service,
            'call_freemodel_chat',
            side_effect=provider,
        ) as mocked_provider:
            translated = translation_service._translate_chunk_resilient(
                ['Payment is due tomorrow.'],
                'en',
                'es',
                api_key='test-provider-key',
                team_context=context,
            )

        self.assertEqual(translated, {0: 'El pago vence mañana.'})
        self.assertEqual(mocked_provider.call_count, 5)
        self.assertEqual(context.metrics['back_translation_blocks'], 1)
        self.assertEqual(context.metrics['back_translation_reviewer_blocks'], 1)

    def test_publication_gate_rejects_missing_locked_glossary_term(self):
        context = translation_service.create_translation_team_context(
            'en',
            'es',
            resources={
                'glossary': [{
                    'source_term': 'termination',
                    'target_term': 'rescisión',
                    'authority': 'locked',
                }],
            },
            domain='legal',
        )
        source_groups = [[{'text': 'Termination requires notice.'}]]
        translated_pages = [['La cancelación requiere aviso.']]

        with self.assertRaisesRegex(
            translation_service.TranslationError,
            'Publication gate rejected',
        ):
            translation_service._automated_quality_report(
                source_groups,
                translated_pages,
                'es',
                literary=False,
                team_context=context,
            )

    def test_profile_router_detects_legal_domain_and_long_literary_work(self):
        legal = translation_service._detect_document_profile(
            [[{'text': 'This Agreement defines liability and governing law.'}]],
            1,
            'auto',
        )
        literary = translation_service._detect_document_profile(
            [[{'text': 'The moon rose over the silent road. ' * 8}]] * 100,
            100,
            'auto',
        )

        self.assertEqual(legal['domain'], 'legal')
        self.assertEqual(legal['mode'], 'document')
        self.assertEqual(literary['domain'], 'literary')
        self.assertEqual(literary['mode'], 'literary')

    def test_auto_team_activates_only_general_and_detected_domain_memory(self):
        context = translation_service.create_translation_team_context(
            'en',
            'es',
            resources={
                'memory': [
                    {
                        'domain': 'legal',
                        'source_text': 'notice',
                        'target_text': 'notificación',
                    },
                    {
                        'domain': 'financial',
                        'source_text': 'notice',
                        'target_text': 'aviso',
                    },
                ],
            },
            domain='auto',
        )

        self.assertIsNone(context.exact_memory_match('notice'))
        context.set_detected_domain('legal')
        self.assertEqual(context.exact_memory_match('notice'), 'notificación')


if __name__ == '__main__':
    unittest.main()
