import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from app.services import local_llm_api, translation_service


class LocalLLMApiTests(unittest.TestCase):
    @patch('app.services.local_llm_api.requests.get')
    def test_status_reports_local_model(self, get):
        health = Mock(status_code=200)
        models = Mock(status_code=200)
        models.json.return_value = {'data': [{'id': 'qwen3-local-test'}]}
        get.side_effect = [health, models]

        status = local_llm_api.local_llm_status()

        self.assertTrue(status['available'])
        self.assertEqual(status['model'], 'qwen3-local-test')
        self.assertEqual(status['privacy'], 'localhost-only')

    @patch('app.services.local_llm_api.requests.post')
    def test_chat_strips_reasoning_and_never_uses_an_external_endpoint(self, post):
        response = Mock(status_code=200, encoding='utf-8')
        response.json.return_value = {
            'choices': [{'message': {'content': '<think>private work</think>\nترجمه نهایی'}}],
        }
        post.return_value = response

        result = local_llm_api.call_local_chat('system', 'source text')

        self.assertEqual(result, 'ترجمه نهایی')
        self.assertTrue(post.call_args.args[0].startswith('http://127.0.0.1'))

    def test_activity_exposes_live_elapsed_time_and_safe_request_metadata(self):
        with local_llm_api._activity_lock:
            original = dict(local_llm_api._activity)
            local_llm_api._activity.update({
                'state': 'working',
                'phase': 'generating',
                'action': 'Reviewing a translation that needs attention',
                'started_at': (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat(),
                'prompt_characters': 1200,
                'max_output_tokens': 400,
                'context': {'translation_id': 7, 'document_name': 'private.pdf'},
            })
        try:
            activity = local_llm_api.local_llm_activity()
        finally:
            with local_llm_api._activity_lock:
                local_llm_api._activity.clear()
                local_llm_api._activity.update(original)

        self.assertEqual(activity['phase'], 'generating')
        self.assertGreaterEqual(activity['elapsed_seconds'], 4)
        self.assertEqual(activity['context']['translation_id'], 7)

    def test_offline_team_dispatches_only_to_local_adapter(self):
        context = translation_service.create_translation_team_context(
            'en',
            'fa',
            provider_mode='offline',
            provider_model='qwen3-local-test',
        )
        with (
            patch.object(translation_service, 'call_local_chat', return_value='local') as local,
            patch.object(translation_service, 'call_freemodel_chat') as online,
        ):
            result = translation_service._call_translation_chat(
                context,
                system_prompt='system',
                user_prompt='text',
            )

        self.assertEqual(result, 'local')
        local.assert_called_once()
        online.assert_not_called()


if __name__ == '__main__':
    unittest.main()
