"""Local NLLB translation runtime used for fast offline first-pass drafts."""

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone

from flask import current_app


logger = logging.getLogger(__name__)


class FastTranslationError(RuntimeError):
    """Raised when the local NLLB engine cannot produce a safe draft."""


_LANGUAGE_CODES = {
    'en': 'eng_Latn', 'es': 'spa_Latn', 'fr': 'fra_Latn', 'de': 'deu_Latn',
    'it': 'ita_Latn', 'pt': 'por_Latn', 'ar': 'arb_Arab', 'fa': 'pes_Arab',
    'he': 'heb_Hebr', 'ur': 'urd_Arab',
}
_runtime_lock = threading.Lock()
_translator = None
_tokenizer = None
_activity_lock = threading.Lock()
_activity = {
    'state': 'idle',
    'action': 'Standing by for a translation',
    'active_batches': 0,
    'completed_batches': 0,
    'translated_segments': 0,
    'last_duration_seconds': None,
    'last_completed_at': None,
    'last_error': None,
}


def fast_translation_activity() -> dict:
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


def _setting(name: str, default):
    try:
        return current_app.config.get(name, default)
    except RuntimeError:
        return os.environ.get(name, default)


def _model_path() -> str:
    configured = os.environ.get('FAST_TRANSLATION_MODEL_PATH') or _setting(
        'FAST_TRANSLATION_MODEL_PATH',
        r'C:\LayoutLingo-LocalAI\models\nllb-200-distilled-600m-ct2-int8',
    )
    return os.path.abspath(str(configured))


def fast_translation_status() -> dict:
    """Report whether the installed local NLLB runtime can be used."""
    model_path = _model_path()
    required = ('model.bin', 'config.json', 'tokenizer.json')
    missing = [name for name in required if not os.path.isfile(os.path.join(model_path, name))]
    if missing:
        return {
            'available': False,
            'model': 'nllb-200-distilled-600m-ct2-int8',
            'error': f'Fast translation model is missing: {", ".join(missing)}.',
            'loaded': False,
            'activity': fast_translation_activity(),
        }
    try:
        import ctranslate2  # noqa: F401
        from transformers import AutoTokenizer  # noqa: F401
    except ImportError as exc:
        runtime_hint = (
            ' Start the dashboard with .venv\\Scripts\\python.exe run.py.'
            if '.venv' not in os.path.normcase(sys.executable)
            else ''
        )
        return {
            'available': False,
            'model': 'nllb-200-distilled-600m-ct2-int8',
            'error': f'Fast translation runtime is unavailable: {exc.name}.{runtime_hint}',
            'loaded': False,
            'activity': fast_translation_activity(),
        }
    return {
        'available': True,
        'model': 'nllb-200-distilled-600m-ct2-int8',
        'path': model_path,
        'device': str(_setting('FAST_TRANSLATION_DEVICE', 'cpu')),
        'privacy': 'localhost-only',
        'loaded': bool(_translator is not None and _tokenizer is not None),
        'activity': fast_translation_activity(),
    }


def _load_runtime(source_language: str):
    global _translator, _tokenizer
    if source_language not in _LANGUAGE_CODES:
        raise FastTranslationError(f'Fast offline translation does not support {source_language}.')
    if _translator is None or _tokenizer is None:
        status = fast_translation_status()
        if not status.get('available'):
            raise FastTranslationError(status.get('error') or 'Fast translation runtime is unavailable.')
        import ctranslate2
        from transformers import AutoTokenizer

        model_path = _model_path()
        device = str(_setting('FAST_TRANSLATION_DEVICE', 'cpu')).lower()
        if device not in {'cpu', 'cuda', 'auto'}:
            device = 'cpu'
        if device == 'auto':
            device = 'cpu'
        _tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            local_files_only=True,
            src_lang=_LANGUAGE_CODES[source_language],
        )
        _translator = ctranslate2.Translator(
            model_path,
            device=device,
            compute_type=str(_setting('FAST_TRANSLATION_COMPUTE_TYPE', 'int8')),
            inter_threads=1,
            intra_threads=max(1, int(_setting('FAST_TRANSLATION_CPU_THREADS', '4'))),
        )
        logger.info('[FAST MT] Loaded NLLB translation runtime from %s on %s.', model_path, device)
    _tokenizer.src_lang = _LANGUAGE_CODES[source_language]
    return _translator, _tokenizer


def translate_fast_batch(texts: list[str], source_language: str,
                         target_language: str,
                         activity_context: dict | None = None) -> list[str]:
    """Translate a batch with local NLLB without any network fallback."""
    if source_language not in _LANGUAGE_CODES or target_language not in _LANGUAGE_CODES:
        raise FastTranslationError(
            f'Fast offline translation does not support {source_language}->{target_language}.'
        )
    if not texts:
        return []

    started = time.monotonic()
    with _activity_lock:
        _activity.update({
            'state': 'working',
            'phase': 'drafting',
            'action': f'Translating {len(texts)} text segment{"s" if len(texts) != 1 else ""}',
            'active_batches': _activity['active_batches'] + 1,
            'current_segments': _activity.get('current_segments', 0) + len(texts),
            'language_pair': f'{source_language.upper()} to {target_language.upper()}',
            'context': dict(activity_context or {}),
            'started_at': datetime.now(timezone.utc).isoformat(),
            'last_error': None,
        })
    try:
        with _runtime_lock:
            translator, tokenizer = _load_runtime(source_language)
            source_tokens = [
                tokenizer.convert_ids_to_tokens(tokenizer.encode(text, add_special_tokens=True))
                for text in texts
            ]
            results = translator.translate_batch(
                source_tokens,
                target_prefix=[[_LANGUAGE_CODES[target_language]]] * len(source_tokens),
                beam_size=max(1, int(_setting('FAST_TRANSLATION_BEAM_SIZE', '2'))),
                max_decoding_length=max(32, min(512, max(len(text) * 2 for text in texts))),
            )
            translations = []
            for result in results:
                if not result.hypotheses:
                    raise FastTranslationError(
                        'Fast translation engine returned an empty hypothesis.'
                    )
                value = tokenizer.decode(
                    tokenizer.convert_tokens_to_ids(result.hypotheses[0]),
                    skip_special_tokens=True,
                ).strip()
                if not value:
                    raise FastTranslationError('Fast translation engine returned empty text.')
                translations.append(value)
    except Exception as exc:
        with _activity_lock:
            active_batches = max(0, _activity['active_batches'] - 1)
            _activity.update({
                'state': 'working' if active_batches else 'attention',
                'phase': 'drafting' if active_batches else 'needs_attention',
                'action': (
                    'Translating another queued passage'
                    if active_batches
                    else 'Waiting to retry the translation draft'
                ),
                'active_batches': active_batches,
                'current_segments': max(
                    0, _activity.get('current_segments', 0) - len(texts)
                ),
                'last_duration_seconds': round(time.monotonic() - started, 1),
                'last_error': str(exc)[:180],
            })
        if isinstance(exc, FastTranslationError):
            raise
        raise FastTranslationError(f'Fast translation engine failed: {exc}') from exc

    duration = time.monotonic() - started
    with _activity_lock:
        active_batches = max(0, _activity['active_batches'] - 1)
        _activity.update({
            'state': 'working' if active_batches else 'ready',
            'phase': 'drafting' if active_batches else 'standing_by',
            'action': (
                'Translating another queued passage'
                if active_batches
                else 'Standing by for a translation'
            ),
            'active_batches': active_batches,
            'completed_batches': _activity['completed_batches'] + 1,
            'translated_segments': _activity['translated_segments'] + len(texts),
            'current_segments': max(
                0, _activity.get('current_segments', 0) - len(texts)
            ),
            'last_duration_seconds': round(duration, 1),
            'last_completed_at': datetime.now(timezone.utc).isoformat(),
            'last_error': None,
            'context': {} if not active_batches else _activity.get('context', {}),
        })
    logger.info(
        '[FAST MT] Translated %s segment(s) in %.1fs with NLLB.',
        len(texts), duration,
    )
    return translations
