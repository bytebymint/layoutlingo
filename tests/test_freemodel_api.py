import unittest
from unittest.mock import Mock, patch

from app.services import freemodel_api, translation_service


class FreemodelFailoverTests(unittest.TestCase):
    def setUp(self):
        freemodel_api._reset_provider_key_health()
        self.keys = (
            'primary-test-key-0001',
            'backup-test-key-0002',
            'backup-test-key-0003',
        )

    def tearDown(self):
        freemodel_api._reset_provider_key_health()

    @staticmethod
    def _response(status, payload, headers=None):
        response = Mock()
        response.status_code = status
        response.headers = headers or {}
        response.text = ''
        response.json.return_value = payload
        return response

    def test_402_and_429_fail_over_to_third_key_in_order(self):
        responses = [
            self._response(402, {'error': {'message': 'quota exhausted'}}),
            self._response(
                429,
                {'error': {'message': 'rate limited'}},
                {'Retry-After': '600'},
            ),
            self._response(200, {
                'choices': [{'message': {'content': '1. translated'}}],
            }),
        ]

        with patch.object(freemodel_api.requests, 'post', side_effect=responses) as post:
            result = freemodel_api.call_freemodel_chat(
                'system', 'user', api_key=self.keys
            )

        self.assertEqual(result, '1. translated')
        self.assertEqual(post.call_count, 3)
        used_keys = [
            call.kwargs['headers']['Authorization'].removeprefix('Bearer ')
            for call in post.call_args_list
        ]
        self.assertEqual(used_keys, list(self.keys))

    def test_failed_keys_stay_on_cooldown_for_later_page_requests(self):
        first_responses = [
            self._response(402, {'error': {'message': 'quota exhausted'}}),
            self._response(200, {
                'choices': [{'message': {'content': '1. first'}}],
            }),
        ]
        with patch.object(
            freemodel_api.requests, 'post', side_effect=first_responses
        ) as first_post:
            self.assertEqual(
                freemodel_api.call_freemodel_chat(
                    'system', 'user', api_key=self.keys[:2]
                ),
                '1. first',
            )
        self.assertEqual(first_post.call_count, 2)

        second_response = self._response(200, {
            'choices': [{'message': {'content': '1. second'}}],
        })
        with patch.object(
            freemodel_api.requests, 'post', return_value=second_response
        ) as second_post:
            self.assertEqual(
                freemodel_api.call_freemodel_chat(
                    'system', 'user', api_key=self.keys[:2]
                ),
                '1. second',
            )

        second_post.assert_called_once()
        self.assertEqual(
            second_post.call_args.kwargs['headers']['Authorization'],
            f'Bearer {self.keys[1]}',
        )

    def test_all_402_responses_raise_an_actionable_error(self):
        responses = [
            self._response(402, {'error': {'message': 'no credits'}})
            for _key in self.keys
        ]

        with patch.object(freemodel_api.requests, 'post', side_effect=responses):
            with self.assertRaises(freemodel_api.FreemodelProviderError) as raised:
                freemodel_api.call_freemodel_chat(
                    'system', 'user', api_key=self.keys
                )

        message = str(raised.exception)
        self.assertIn('All 3 configured translation API keys', message)
        self.assertIn('HTTP 402', message)
        self.assertIn('available quota', message)

    def test_translation_does_not_retry_or_split_when_every_key_is_unavailable(self):
        provider_error = freemodel_api.FreemodelProviderError(
            [
                {'key_number': index, 'status': 402, 'reason': 'no credits'}
                for index in range(1, 4)
            ],
            3,
        )
        with patch.object(
            translation_service,
            'call_freemodel_chat',
            side_effect=provider_error,
        ) as provider:
            with self.assertRaises(freemodel_api.FreemodelProviderError):
                translation_service._translate_chunk_resilient(
                    ['First paragraph.', 'Second paragraph.'],
                    'en',
                    'fa',
                    self.keys,
                    literary_quality=True,
                )

        provider.assert_called_once()


if __name__ == '__main__':
    unittest.main()
