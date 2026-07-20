import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import current_app


logger = logging.getLogger(__name__)
_activity_lock = threading.Lock()
_activity = {
    'state': 'idle',
    'phase': 'standing_by',
    'action': 'Standing by for quality review',
    'active_requests': 0,
    'completed_requests': 0,
    'last_duration_seconds': None,
    'last_completed_at': None,
    'last_error': None,
}


class LocalLLMError(RuntimeError):
    """Raised when the explicitly selected local model cannot serve a request."""


def _setting(name: str, default):
    try:
        return current_app.config.get(name, default)
    except RuntimeError:
        return os.environ.get(name, default)


def _endpoint() -> str:
    return str(_setting(
        'LOCAL_LLM_ENDPOINT',
        'http://127.0.0.1:8080/v1/chat/completions',
    )).strip()


def _is_loopback_endpoint(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}


def _base_url() -> str:
    endpoint = urlsplit(_endpoint())
    path = endpoint.path
    for suffix in ('/v1/chat/completions', '/chat/completions'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((endpoint.scheme, endpoint.netloc, path.rstrip('/'), '', ''))


def _friendly_action(system_prompt: str, user_prompt: str) -> str:
    combined = f'{system_prompt or ""} {user_prompt or ""}'.casefold()
    if 'story bible' in combined or 'translation brief' in combined:
        return 'Building terminology and style guidance'
    if 'review' in combined or 'quality' in combined:
        return 'Reviewing a translation that needs attention'
    if 'editor' in combined or 'polish' in combined:
        return 'Polishing language and tone'
    return 'Translating a difficult passage'


def local_llm_activity() -> dict:
    with _activity_lock:
        activity = dict(_activity)
    started_at = activity.get('started_at')
    if activity.get('state') == 'working' and started_at:
        try:
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(started_at)
            activity['elapsed_seconds'] = max(0, int(elapsed.total_seconds()))
        except (TypeError, ValueError):
            activity['elapsed_seconds'] = None
    else:
        activity['elapsed_seconds'] = None
    return activity


def _start_activity(action: str, *, model: str, prompt_characters: int,
                    max_tokens: int, activity_context: dict | None = None):
    with _activity_lock:
        _activity['active_requests'] += 1
        _activity['state'] = 'working'
        _activity['phase'] = 'generating'
        _activity['action'] = action
        _activity['started_at'] = datetime.now(timezone.utc).isoformat()
        _activity['model'] = model
        _activity['prompt_characters'] = prompt_characters
        _activity['max_output_tokens'] = max_tokens
        _activity['context'] = dict(activity_context or {})
        _activity['last_error'] = None


def _finish_activity(started_at: float, error: str | None = None):
    with _activity_lock:
        _activity['active_requests'] = max(0, _activity['active_requests'] - 1)
        _activity['last_duration_seconds'] = round(time.monotonic() - started_at, 1)
        _activity['last_completed_at'] = datetime.now(timezone.utc).isoformat()
        if error:
            _activity['state'] = 'attention'
            _activity['phase'] = 'needs_attention'
            _activity['action'] = 'Waiting for the local engine to recover'
            _activity['last_error'] = error[:180]
        else:
            _activity['completed_requests'] += 1
            _activity['state'] = 'working' if _activity['active_requests'] else 'ready'
            if not _activity['active_requests']:
                _activity['phase'] = 'standing_by'
                _activity['action'] = 'Standing by for quality review'
                _activity['context'] = {}


def local_llm_status(timeout_seconds: float = 2.0) -> dict:
    """Return bounded health information without contacting any external host."""
    base_url = _base_url()
    if not _is_loopback_endpoint(base_url):
        return {
            'available': False,
            'error': 'Local endpoint must bind to localhost.',
            'activity': local_llm_activity(),
        }
    try:
        health = requests.get(f'{base_url}/health', timeout=timeout_seconds)
        if health.status_code != 200:
            return {
                'available': False,
                'error': f'Local engine health check returned HTTP {health.status_code}.',
                'activity': local_llm_activity(),
            }
        models = requests.get(
            f'{base_url}/v1/models',
            headers={'Authorization': f"Bearer {_setting('LOCAL_LLM_API_KEY', 'local-private-key')}"},
            timeout=timeout_seconds,
        )
        model_name = str(_setting('LOCAL_LLM_MODEL', 'aya-expanse-8b-local'))
        if models.status_code == 200:
            payload = models.json()
            available_models = [
                str(item.get('id'))
                for item in payload.get('data', [])
                if isinstance(item, dict) and item.get('id')
            ]
            if available_models:
                model_name = available_models[0]
        return {
            'available': True,
            'model': model_name,
            'endpoint': _endpoint(),
            'privacy': 'localhost-only',
            'activity': local_llm_activity(),
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {
            'available': False,
            'error': re.sub(r'\s+', ' ', str(exc)).strip()[:240]
                     or 'Local engine is not running.',
            'activity': local_llm_activity(),
        }


def _strip_reasoning(text: str) -> str:
    value = re.sub(r'<think>.*?</think>', '', text or '', flags=re.DOTALL | re.IGNORECASE)
    value = re.sub(r'<\|(?:END_OF_TURN_TOKEN|endoftext)\|>', '', value, flags=re.IGNORECASE)
    return value.strip()


def call_local_chat(system_prompt, user_prompt, temperature=0.2,
                    max_tokens=2000, api_key=None, model_name=None,
                    activity_context=None):
    """Call the localhost-only llama.cpp server with no online fallback."""
    endpoint = _endpoint()
    if not _is_loopback_endpoint(endpoint):
        raise LocalLLMError('Offline translation endpoint must use localhost.')

    key = str(_setting('LOCAL_LLM_API_KEY', 'local-private-key')).strip()
    model = str(_setting('LOCAL_LLM_MODEL', 'aya-expanse-8b-local')).strip()
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    local_user_prompt = user_prompt
    if model.lower().startswith('qwen'):
        local_user_prompt = f'/no_think\n{user_prompt}'
    messages.append({'role': 'user', 'content': local_user_prompt})
    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False,
        'cache_prompt': True,
    }
    timeout_seconds = max(
        60,
        int(_setting('LOCAL_LLM_TIMEOUT_SECONDS', 900)),
    )
    started_at = time.monotonic()
    _start_activity(
        _friendly_action(system_prompt, local_user_prompt),
        model=model,
        prompt_characters=len(system_prompt or '') + len(local_user_prompt or ''),
        max_tokens=max_tokens,
        activity_context=activity_context,
    )
    logger.info(
        '[LOCAL LLM] Sending %s-character prompt to %s (max output %s tokens).',
        len(system_prompt or '') + len(local_user_prompt or ''),
        model,
        max_tokens,
    )
    try:
        response = requests.post(
            endpoint,
            headers={
                'Authorization': f'Bearer {key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=(10, timeout_seconds),
        )
    except requests.RequestException as exc:
        _finish_activity(started_at, 'The local quality reviewer is unavailable.')
        raise LocalLLMError(
            'The local translation engine is unavailable. Start '
            r'C:\LayoutLingo-LocalAI\start-local-ai.ps1 and retry.'
        ) from exc

    response.encoding = 'utf-8'
    if response.status_code != 200:
        detail = re.sub(r'\s+', ' ', response.text or '').strip()[:500]
        _finish_activity(started_at, f'Local reviewer returned HTTP {response.status_code}.')
        raise LocalLLMError(
            f'Local translation engine returned HTTP {response.status_code}: {detail}'
        )
    try:
        result = response.json()
        choices = result.get('choices') or []
        content = choices[0].get('message', {}).get('content', '') if choices else ''
    except (ValueError, TypeError, AttributeError, IndexError) as exc:
        _finish_activity(started_at, 'The local reviewer returned an unreadable response.')
        raise LocalLLMError('Local translation engine returned invalid JSON.') from exc
    cleaned = _strip_reasoning(content)
    if not cleaned:
        _finish_activity(started_at, 'The local reviewer returned an empty response.')
        raise LocalLLMError('Local translation engine returned an empty response.')
    _finish_activity(started_at)
    logger.info(
        '[LOCAL LLM] Completed with %s in %.1fs (%s output characters).',
        model,
        time.monotonic() - started_at,
        len(cleaned),
    )
    return cleaned
