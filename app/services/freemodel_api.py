import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://api.freemodel.dev/v1/chat/completions"
DEFAULT_MODEL = "openai-t0"
DEFAULT_TIMEOUT = 180

_KEY_STATE_LOCK = threading.Lock()
_KEY_COOLDOWNS: dict[str, dict] = {}


class FreemodelProviderError(RuntimeError):
    """Raised when no configured provider key can complete a request."""

    def __init__(self, failures: list[dict], configured_key_count: int):
        self.failures = tuple(failures)
        self.configured_key_count = configured_key_count
        super().__init__(_provider_error_message(failures, configured_key_count))


def _provider_error_message(failures: list[dict], configured_key_count: int) -> str:
    statuses = {item.get('status') for item in failures if item.get('status')}
    key_word = 'key' if configured_key_count == 1 else 'keys'
    prefix = f'All {configured_key_count} configured translation API {key_word} are unavailable.'

    if statuses and statuses <= {402}:
        return (
            f'{prefix} The provider returned HTTP 402 (payment or quota required). '
            'Add credits or configure a key from an account with available quota.'
        )
    if statuses and statuses <= {429}:
        return (
            f'{prefix} The provider rate-limited every key (HTTP 429). '
            'Wait for the provider reset or configure another key with available quota.'
        )
    if statuses and statuses <= {401, 403}:
        return (
            f'{prefix} The provider rejected their credentials or permissions '
            f'(HTTP {"/".join(str(status) for status in sorted(statuses))}).'
        )

    details = []
    for failure in failures:
        status = failure.get('status')
        label = f'key {failure["key_number"]}'
        details.append(f'{label}: HTTP {status}' if status else f'{label}: request error')
    if details:
        return f'{prefix} ' + '; '.join(details) + '.'
    return prefix


def _iter_key_values(api_key=None):
    if isinstance(api_key, str):
        yield api_key
        # Older callers may still pass only the primary key.
        yield os.environ.get('FREEMODEL_API_KEY_2', '')
        yield os.environ.get('FREEMODEL_API_KEY_3', '')
    elif api_key:
        try:
            yield from api_key
        except TypeError:
            yield api_key
    else:
        yield os.environ.get('FREEMODEL_API_KEY', '')
        yield os.environ.get('FREEMODEL_API_KEY_2', '')
        yield os.environ.get('FREEMODEL_API_KEY_3', '')


def _resolve_api_keys(api_key=None) -> tuple[str, ...]:
    keys = []
    for value in _iter_key_values(api_key):
        if not isinstance(value, str):
            continue
        key = value.strip()
        if len(key) >= 10 and key not in keys:
            keys.append(key)
    if not keys:
        raise RuntimeError('No FREEMODEL_API_KEY is configured.')
    return tuple(keys)


def _retry_after_seconds(response) -> float | None:
    value = (response.headers.get('Retry-After') or '').strip()
    if not value:
        return None
    try:
        return max(1.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(1.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _cooldown_seconds(status_code: int | None, response=None) -> float:
    if status_code == 429:
        retry_after = _retry_after_seconds(response) if response is not None else None
        return retry_after or max(
            30, int(os.environ.get('FREEMODEL_RATE_LIMIT_COOLDOWN_SECONDS', '300'))
        )
    if status_code in {401, 402, 403}:
        return max(
            60, int(os.environ.get('FREEMODEL_ACCOUNT_COOLDOWN_SECONDS', '3600'))
        )
    return max(5, int(os.environ.get('FREEMODEL_ERROR_COOLDOWN_SECONDS', '30')))


def _mark_key_unavailable(key: str, key_number: int, status: int | None,
                          reason: str, cooldown_seconds: float):
    with _KEY_STATE_LOCK:
        _KEY_COOLDOWNS[key] = {
            'until': time.monotonic() + cooldown_seconds,
            'key_number': key_number,
            'status': status,
            'reason': reason,
        }


def _active_key_state(key: str) -> dict | None:
    with _KEY_STATE_LOCK:
        state = _KEY_COOLDOWNS.get(key)
        if not state:
            return None
        if state['until'] <= time.monotonic():
            _KEY_COOLDOWNS.pop(key, None)
            return None
        return dict(state)


def _clear_key_state(key: str):
    with _KEY_STATE_LOCK:
        _KEY_COOLDOWNS.pop(key, None)


def _reset_provider_key_health():
    """Clear process-local key cooldowns. Intended for tests and app restarts."""
    with _KEY_STATE_LOCK:
        _KEY_COOLDOWNS.clear()


def _response_error_detail(response) -> str:
    detail = ''
    try:
        payload = response.json()
        error = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(error, dict):
            detail = str(error.get('message') or error.get('detail') or '')
        elif error:
            detail = str(error)
        elif isinstance(payload, dict):
            detail = str(payload.get('message') or payload.get('detail') or '')
    except (ValueError, TypeError):
        detail = response.text or ''
    return re.sub(r'\s+', ' ', detail).strip()[:240]


def call_freemodel_chat(system_prompt, user_prompt, temperature=0.2,
                        max_tokens=2000, api_key=None, model_name=None):
    """Call the OpenAI-compatible provider with ordered API-key failover."""
    keys = _resolve_api_keys(api_key)
    model = model_name or os.environ.get('FREEMODEL_MODEL', DEFAULT_MODEL)
    endpoint = os.environ.get('FREEMODEL_ENDPOINT', DEFAULT_ENDPOINT)
    messages = []
    if system_prompt:
        messages.append({'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_prompt})
    payload = {
        'model': model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
    }
    timeout_seconds = max(
        30, int(os.environ.get('FREEMODEL_TIMEOUT_SECONDS', str(DEFAULT_TIMEOUT)))
    )
    failures = []

    for key_number, key in enumerate(keys, start=1):
        state = _active_key_state(key)
        if state:
            failures.append({
                'key_number': key_number,
                'status': state.get('status'),
                'reason': state.get('reason', 'temporarily unavailable'),
            })
            continue

        headers = {
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
        }
        logger.info(
            'Calling FreeModel AI provider with model %s using key %s/%s.',
            model,
            key_number,
            len(keys),
        )
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=(15, timeout_seconds),
            )
        except requests.RequestException as exc:
            reason = re.sub(r'\s+', ' ', str(exc)).strip()[:240] or 'network error'
            cooldown = _cooldown_seconds(None)
            _mark_key_unavailable(key, key_number, None, reason, cooldown)
            failures.append({'key_number': key_number, 'status': None, 'reason': reason})
            logger.warning(
                'FreeModel AI provider key %s/%s had a request error; trying the next key.',
                key_number,
                len(keys),
            )
            continue

        # Some OpenAI-compatible gateways omit a charset. Requests may then
        # decode UTF-8 model output as ISO-8859-1, corrupting Persian/Arabic.
        response.encoding = 'utf-8'
        if response.status_code != 200:
            reason = _response_error_detail(response) or 'provider rejected the request'
            failure = {
                'key_number': key_number,
                'status': response.status_code,
                'reason': reason,
            }
            failures.append(failure)

            # Invalid payload/model/endpoint errors are not key-specific.
            if response.status_code in {400, 404, 405, 413, 415, 422}:
                raise FreemodelProviderError(failures, len(keys))

            cooldown = _cooldown_seconds(response.status_code, response)
            _mark_key_unavailable(
                key, key_number, response.status_code, reason, cooldown
            )
            logger.warning(
                'FreeModel AI provider key %s/%s failed with HTTP %s; trying the next key.',
                key_number,
                len(keys),
                response.status_code,
            )
            continue

        try:
            payload_json = response.json()
        except (ValueError, TypeError) as exc:
            reason = f'invalid JSON response: {exc}'
            cooldown = _cooldown_seconds(None)
            _mark_key_unavailable(key, key_number, None, reason, cooldown)
            failures.append({'key_number': key_number, 'status': None, 'reason': reason})
            continue

        choices = payload_json.get('choices') or [] if isinstance(payload_json, dict) else []
        content = choices[0].get('message', {}).get('content', '') if choices else ''
        if isinstance(content, str) and content.strip():
            _clear_key_state(key)
            return content.strip()

        reason = 'empty provider response'
        cooldown = _cooldown_seconds(None)
        _mark_key_unavailable(key, key_number, None, reason, cooldown)
        failures.append({'key_number': key_number, 'status': None, 'reason': reason})

    raise FreemodelProviderError(failures, len(keys))


def call_gemini_chat(system_prompt, user_prompt, temperature=0.2,
                     max_tokens=2000, api_key=None, model_name=None):
    """Alias for backward compatibility."""
    return call_freemodel_chat(
        system_prompt, user_prompt, temperature, max_tokens, api_key, model_name
    )


def call_gemini_vision(file_path, prompt, api_key=None, model_name=None):
    """Run real Gemini document/image OCR when a Gemini key is configured."""
    key = (api_key or '').strip()
    if not key:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            model_name or os.environ.get('GEMINI_VISION_MODEL', 'gemini-1.5-flash')
        )
        extension = os.path.splitext(file_path)[1].lower()
        if extension == '.pdf':
            uploaded = genai.upload_file(file_path)
            try:
                response = model.generate_content([uploaded, prompt])
            finally:
                try:
                    genai.delete_file(uploaded.name)
                except Exception:
                    logger.warning('Could not delete temporary Gemini upload %s', uploaded.name)
        else:
            from PIL import Image

            with Image.open(file_path) as image:
                response = model.generate_content([prompt, image.copy()])
        text = getattr(response, 'text', None)
        return text.strip() if isinstance(text, str) and text.strip() else None
    except Exception as exc:
        logger.warning('Gemini vision OCR failed: %s', exc)
        return None
