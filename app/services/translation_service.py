import json
import logging
import os
import re
import time
import unicodedata
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from html import escape
from threading import Lock

from app.services.freemodel_api import FreemodelProviderError, call_freemodel_chat
from app.services.fast_translation_api import FastTranslationError, translate_fast_batch
from app.services.local_llm_api import call_local_chat

logger = logging.getLogger(__name__)


class TranslationError(RuntimeError):
    """Raised when a translation cannot be completed without data loss."""


class TranslationLayoutError(TranslationError):
    """Raised when translated text cannot fit safely in its source region."""


class TranslationCancelled(TranslationError):
    """Raised when a durable translation job is cancelled by its owner."""


class TranslationQualityError(TranslationError):
    """Raised with a complete QA dossier when publication is rejected."""

    def __init__(self, message: str, quality_report: dict):
        super().__init__(message)
        self.quality_report = quality_report


class TranslationItemQualityError(TranslationError):
    """A single failed passage that a human can correct without restarting."""

    def __init__(self, message: str, source_text: str, target_text: str,
                 item_index: int, category: str = 'target_language'):
        super().__init__(message)
        self.source_text = source_text or ''
        self.target_text = target_text or ''
        self.item_index = item_index
        self.category = category

    def to_review_issue(self, page_number: int | None = None) -> dict:
        return {
            'category': self.category,
            'severity': 'error',
            'message': str(self),
            'page_number': page_number,
            'source_excerpt': self.source_text[:1000],
            'target_excerpt': self.target_text[:1000],
            'status': 'open',
        }

LANGUAGE_OPTIONS = [
    {'code': 'en', 'label': 'English'},
    {'code': 'es', 'label': 'Spanish'},
    {'code': 'fr', 'label': 'French'},
    {'code': 'de', 'label': 'German'},
    {'code': 'it', 'label': 'Italian'},
    {'code': 'pt', 'label': 'Portuguese'},
    {'code': 'ar', 'label': 'Arabic'},
    {'code': 'fa', 'label': 'Farsi'},
    {'code': 'he', 'label': 'Hebrew'},
    {'code': 'ur', 'label': 'Urdu'},
]
LANGUAGE_LABELS = {item['code']: item['label'] for item in LANGUAGE_OPTIONS}
RTL_LANGUAGE_CODES = {'ar', 'fa', 'he', 'ur'}

# Module-level font cache so we don't recreate fitz.Font objects on every page
_font_cache: dict = {}
_archive_cache: dict = {}
_checkpoint_lock = Lock()

_RTL_FONT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', 'assets', 'fonts')
)
_RTL_FONT_FILES = {
    'ar': 'NotoSansArabic.ttf',
    'fa': 'NotoSansArabic.ttf',
    'he': 'NotoSansHebrew.ttf',
    'ur': 'NotoNastaliqUrdu.ttf',
}
# The primary script fonts intentionally omit many technical symbols. Keep the
# fallback set explicit because MuPDF requires a font selection per glyph.
_RTL_FALLBACK_FONT_FILES = (
    'NotoSans.ttf',
    'NotoSansSymbols.ttf',
    'NotoSansMath.ttf',
)
_RTL_LINE_HEIGHTS = {'ar': 1.3, 'fa': 1.3, 'he': 1.25, 'ur': 1.8}
_RTL_MIN_SCALE = min(1.0, max(0.3, float(os.environ.get('RTL_MIN_FONT_SCALE', '0.55'))))
_RTL_EMERGENCY_MIN_SCALE = min(
    _RTL_MIN_SCALE,
    max(0.25, float(os.environ.get('RTL_EMERGENCY_MIN_FONT_SCALE', '0.35'))),
)
_RTL_PAGE_MARGIN = max(8.0, float(os.environ.get('RTL_PAGE_MARGIN_POINTS', '24')))

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_language_label(code):
    if not code:
        return 'Unknown'
    return LANGUAGE_LABELS.get(code.lower(), code.title())


def is_rtl_language(code):
    return bool(code and code.lower() in RTL_LANGUAGE_CODES)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean_output(text):
    value = (text or '').strip()
    if value.startswith('```'):
        value = re.sub(r'^```(?:[a-zA-Z0-9_-]+)?\s*', '', value)
        value = re.sub(r'\s*```$', '', value).strip()
    return value


def _is_literary_heading(text: str) -> bool:
    """Identify a short structural heading that needs its own semantic review."""
    candidate = re.sub(r'\s+', ' ', text or '').strip()
    return bool(candidate and len(candidate) <= 180 and _LITERARY_HEADING_PATTERN.search(candidate))


def _validate_literary_heading_semantics(source_text: str, target_text: str,
                                         source_language: str,
                                         target_language: str):
    """Reject a heading when a protected narrative concept was lost."""
    guards = _LITERARY_HEADING_SEMANTIC_GUARDS.get(
        ((source_language or '').lower(), (target_language or '').lower()),
        {},
    )
    source_folded = (source_text or '').casefold()
    target_folded = (target_text or '').casefold()
    missing = [
        concept for concept, accepted_terms in guards.items()
        if concept in source_folded and not any(term in target_folded for term in accepted_terms)
    ]
    if missing:
        raise TranslationError(
            'Heading semantic guard rejected the translation; missing concept(s): '
            + ', '.join(missing)
        )


def _needs_translation(text):
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    # Skip pure numbers / punctuation / symbols — nothing translatable
    if re.fullmatch(r'[\d\W_]+', stripped):
        return False
    return True


def _fitz_color_to_tuple(color_int):
    """Convert a PyMuPDF integer color (0xRRGGBB) to an (r, g, b) float tuple."""
    if color_int is None:
        return (0.0, 0.0, 0.0)
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >> 8) & 0xFF) / 255.0
    b = (color_int & 0xFF) / 255.0
    return (r, g, b)


def _get_font(is_bold: bool, is_italic: bool, rtl_font_path: str | None = None):
    """
    Return a cached fitz.Font object for the given style.

    Uses fitz.Font() objects (not bare font-name strings) so that
    PyMuPDF never raises "need font file or buffer".
    """
    import fitz  # local import to keep startup fast

    # --- RTL: use system font file ---
    if rtl_font_path:
        key = ('rtl', rtl_font_path)
        if key not in _font_cache:
            try:
                _font_cache[key] = fitz.Font(fontfile=rtl_font_path)
            except Exception:
                _font_cache[key] = fitz.Font("helv")
        return _font_cache[key]

    # --- LTR: choose from Base-14 ---
    # Use full PDF names — PyMuPDF 1.24+ dropped several short aliases (e.g. "helb")
    if is_bold and is_italic:
        fname = "Helvetica-BoldOblique"
    elif is_bold:
        fname = "Helvetica-Bold"
    elif is_italic:
        fname = "Helvetica-Oblique"
    else:
        fname = "helv"   # "helv" short alias still works

    if fname not in _font_cache:
        try:
            _font_cache[fname] = fitz.Font(fname)
        except Exception:
            # Absolute fallback
            _font_cache[fname] = fitz.Font("helv")
    return _font_cache[fname]


def _rtl_font_path(language_code: str) -> str:
    filename = _RTL_FONT_FILES.get((language_code or '').lower())
    if not filename:
        raise TranslationLayoutError(f'No RTL font is configured for {language_code}.')

    path = os.path.join(_RTL_FONT_DIR, filename)
    if not os.path.isfile(path):
        raise TranslationLayoutError(f'Required RTL font is missing: {filename}.')
    return path


def _rtl_archive():
    import fitz

    if 'rtl' not in _archive_cache:
        _archive_cache['rtl'] = fitz.Archive(_RTL_FONT_DIR)
    return _archive_cache['rtl']


def _rtl_coverage_fonts(language_code: str):
    import fitz

    font_paths = [_rtl_font_path(language_code)]
    for filename in _RTL_FALLBACK_FONT_FILES:
        path = os.path.join(_RTL_FONT_DIR, filename)
        if not os.path.isfile(path):
            raise TranslationLayoutError(f'Required fallback font is missing: {filename}.')
        font_paths.append(path)

    fonts = []
    for font_path in font_paths:
        cache_key = ('coverage', font_path)
        if cache_key not in _font_cache:
            _font_cache[cache_key] = fitz.Font(fontfile=font_path)
        fonts.append(_font_cache[cache_key])
    return fonts


def _validate_rtl_font_coverage(text: str, language_code: str):
    """Fail when no bundled font can represent a visible character."""
    fonts = _rtl_coverage_fonts(language_code)

    missing = sorted({
        char
        for char in text
        if not char.isspace()
        and unicodedata.category(char) not in {'Cc', 'Cf'}
        and not any(font.has_glyph(ord(char)) for font in fonts)
    })
    if missing:
        codepoints = ', '.join(f'U+{ord(char):04X}' for char in missing[:8])
        raise TranslationLayoutError(
            f'Bundled {get_language_label(language_code)} font is missing glyphs: {codepoints}.'
        )


def _rtl_text_html(text: str, language_code: str) -> str:
    """Escape text, isolate business facts, and select required fallback fonts."""
    fonts = _rtl_coverage_fonts(language_code)

    def render_segment(segment: str) -> str:
        fragments = []
        for char in segment:
            if char == '\n':
                fragments.append('<br>')
                continue

            safe_char = escape(char)
            if (
                char.isspace()
                or unicodedata.category(char) in {'Cc', 'Cf'}
                or fonts[0].has_glyph(ord(char))
            ):
                fragments.append(safe_char)
                continue

            fallback_index = next(
                (index for index, font in enumerate(fonts[1:]) if font.has_glyph(ord(char))),
                None,
            )
            if fallback_index is None:
                fragments.append(safe_char)
            else:
                fragments.append(
                    f'<span class="rtl-fallback-{fallback_index}">{safe_char}</span>'
                )
        return ''.join(fragments)

    fragments = []
    last_end = 0
    for match in _FACTUAL_TOKEN_PATTERN.finditer(text or ''):
        fragments.append(render_segment(text[last_end:match.start()]))
        fragments.append(
            '<span class="rtl-protected-fact" dir="ltr">'
            f'{render_segment(match.group(0))}'
            '</span>'
        )
        last_end = match.end()
    fragments.append(render_segment((text or '')[last_end:]))
    return ''.join(fragments)


def _insert_rtl_htmlbox(page, rect, text: str, language_code: str,
                        font_size: float, color: tuple, is_bold: bool,
                        scale_low: float | None = None):
    """Render logical RTL Unicode with MuPDF's complex-script HTML engine."""
    _validate_rtl_font_coverage(text, language_code)
    font_filename = os.path.basename(_rtl_font_path(language_code))
    fallback_css = '\n'.join(
        f'''@font-face {{ font-family: DocumentFallback{index}; src: url("{filename}"); }}
        .rtl-fallback-{index} {{ font-family: DocumentFallback{index}; }}'''
        for index, filename in enumerate(_RTL_FALLBACK_FONT_FILES)
    )
    red, green, blue = (round(channel * 255) for channel in color)
    line_height = _RTL_LINE_HEIGHTS.get(language_code.lower(), 1.3)
    weight = 700 if is_bold else 400
    safe_text = _rtl_text_html(text, language_code)
    html = f'<div dir="rtl" lang="{language_code.lower()}">{safe_text}</div>'
    css = f'''
        @font-face {{ font-family: DocumentRTL; src: url("{font_filename}"); }}
        {fallback_css}
        .rtl-protected-fact {{
            direction: ltr;
            unicode-bidi: isolate;
            display: inline;
        }}
        div {{
            font-family: DocumentRTL;
            font-size: {max(4.0, font_size):.2f}pt;
            font-weight: {weight};
            line-height: {line_height};
            color: rgb({red}, {green}, {blue});
            direction: rtl;
            text-align: right;
            unicode-bidi: plaintext;
            overflow-wrap: break-word;
            white-space: pre-wrap;
            margin: 0;
            padding: 0;
        }}
    '''
    spare_height, scale = page.insert_htmlbox(
        rect,
        html,
        css=css,
        archive=_rtl_archive(),
        scale_low=scale_low or _RTL_MIN_SCALE,
        overlay=True,
    )
    minimum_scale = scale_low or _RTL_MIN_SCALE
    if spare_height < 0 or scale < minimum_scale:
        raise TranslationLayoutError(
            'RTL text does not fit its page region at the minimum readable scale.'
        )
    return scale


def _expanded_rtl_rect(page_rect, rect):
    """Widen an RTL region without crossing neighboring vertical content."""
    import fitz

    return fitz.Rect(
        max(page_rect.x0 + _RTL_PAGE_MARGIN, rect.x0 - _RTL_PAGE_MARGIN),
        max(page_rect.y0 + _RTL_PAGE_MARGIN, rect.y0),
        min(page_rect.x1 - _RTL_PAGE_MARGIN, rect.x1 + (_RTL_PAGE_MARGIN * 2)),
        min(page_rect.y1 - _RTL_PAGE_MARGIN, rect.y1),
    )


def _full_page_rtl_rect(page_rect):
    import fitz

    return fitz.Rect(
        page_rect.x0 + _RTL_PAGE_MARGIN,
        page_rect.y0 + _RTL_PAGE_MARGIN,
        page_rect.x1 - _RTL_PAGE_MARGIN,
        page_rect.y1 - _RTL_PAGE_MARGIN,
    )


def _render_rtl_item_with_fallbacks(page, item: dict, language_code: str,
                                    allow_full_page: bool = False):
    rects = [
        (item['rect'], _RTL_MIN_SCALE),
        (_expanded_rtl_rect(page.rect, item['rect']), _RTL_MIN_SCALE),
        (_expanded_rtl_rect(page.rect, item['rect']), _RTL_EMERGENCY_MIN_SCALE),
    ]
    if allow_full_page:
        rects.append((_full_page_rtl_rect(page.rect), _RTL_EMERGENCY_MIN_SCALE))
    last_error = None
    for rect, scale_low in rects:
        try:
            return _insert_rtl_htmlbox(
                page,
                rect,
                item['translated'],
                language_code,
                item['size'],
                item['color'],
                item['is_bold'],
                scale_low=scale_low,
            )
        except TranslationLayoutError as exc:
            last_error = exc
            logger.info(
                'RTL layout fallback needed for page region %.1f %.1f %.1f %.1f: %s',
                item['rect'].x0,
                item['rect'].y0,
                item['rect'].x1,
                item['rect'].y1,
                exc,
            )
    raise TranslationLayoutError(str(last_error))


# ---------------------------------------------------------------------------
# Batch translation
# ---------------------------------------------------------------------------

_BATCH_SIZE = max(1, int(os.environ.get('TRANSLATION_BATCH_SIZE', '20')))
_BATCH_CHAR_LIMIT = max(500, int(os.environ.get('TRANSLATION_BATCH_CHAR_LIMIT', '6000')))
_API_RETRIES = max(1, int(os.environ.get('TRANSLATION_API_RETRIES', '3')))
_RETRY_BASE_SECONDS = max(0.0, float(os.environ.get('TRANSLATION_RETRY_BASE_SECONDS', '1')))
_MAX_RTL_LATIN_WORDS = max(
    0, int(os.environ.get('TRANSLATION_MAX_RTL_LATIN_WORDS', '2'))
)
_MAX_BATCH_SPLIT_DEPTH = max(0, int(os.environ.get('TRANSLATION_MAX_BATCH_SPLIT_DEPTH', '6')))
_PAGE_WORKERS = max(1, int(os.environ.get('TRANSLATION_PAGE_WORKERS', '4')))
_LITERARY_MIN_PAGES = max(10, int(os.environ.get('TRANSLATION_LITERARY_MIN_PAGES', '80')))
_BOOK_CONTEXT_CHAR_LIMIT = max(
    500, int(os.environ.get('TRANSLATION_BOOK_CONTEXT_CHAR_LIMIT', '3000'))
)
_BOOK_BIBLE_SAMPLE_CHARS = max(
    3000, int(os.environ.get('TRANSLATION_BOOK_BIBLE_SAMPLE_CHARS', '18000'))
)
_QUALITY_MIN_TARGET_SCRIPT_RATIO = min(
    0.95,
    max(0.25, float(os.environ.get('TRANSLATION_MIN_TARGET_SCRIPT_RATIO', '0.55'))),
)
_DRAFT_MODEL = os.environ.get('TRANSLATION_DRAFT_MODEL', '').strip() or None
_EDITOR_MODEL = os.environ.get('TRANSLATION_EDITOR_MODEL', '').strip() or None
_REVIEWER_MODEL = os.environ.get('TRANSLATION_REVIEWER_MODEL', '').strip() or _EDITOR_MODEL
_QUALITY_GATE_SCORE = min(
    100.0,
    max(70.0, float(os.environ.get('TRANSLATION_QUALITY_GATE_SCORE', '90'))),
)
# Aya is a specialist pass, not a second full-book translator. These limits
# keep Offline Quality predictable on a single local model while preserving
# targeted recovery for actual meaning failures.
_LITERARY_TITLE_REVIEW_BUDGET = max(
    0, int(os.environ.get('FAST_QUALITY_LITERARY_TITLE_REVIEW_BUDGET', '12'))
)
_FINAL_LITERARY_RECOVERY_MAX_ITEMS = max(
    0, int(os.environ.get('FINAL_LITERARY_RECOVERY_MAX_ITEMS', '4'))
)
_FINAL_LITERARY_RECOVERY_CONTEXT_CHARS = max(
    400, int(os.environ.get('FINAL_LITERARY_RECOVERY_CONTEXT_CHARS', '1800'))
)
_LITERARY_HEADING_PATTERN = re.compile(
    r'(?i)(?:^(?:chapter|part|book)\s+'
    r'(?:[0-9]+|[ivxlcdm]+|[a-z][a-z-]*)\b|'
    r'^(?:prologue|epilogue|introduction|afterword)\b)'
)
# Small MT models can collapse these story concepts into adjacent, but wrong,
# meanings. The bilingual title reviewer remains primary; these are hard gates.
_LITERARY_HEADING_SEMANTIC_GUARDS = {
    ('en', 'fa'): {
        'fog': ('مه', 'غبار'),
        'lighthouse': ('فانوس دریایی',),
        'tide': ('جزر', 'مد'),
    },
}

TRANSLATION_DOMAINS = {
    'auto',
    'general',
    'legal',
    'financial',
    'medical',
    'technical',
    'marketing',
    'literary',
}
HIGH_RISK_DOMAINS = {'legal', 'financial', 'medical'}


def _stage_attempt_limit(team_context: 'TranslationTeamContext | None',
                         *, essential: bool = False) -> int:
    """Avoid expensive repeated self-reviews on the single-slot local model."""
    if team_context and team_context.is_offline:
        return min(_API_RETRIES, 2 if essential else 1)
    return _API_RETRIES


def _record_stage_fallback(team_context: 'TranslationTeamContext | None',
                           stage: str, item_count: int, reason: str):
    """Record one non-fatal QA-stage warning while retaining validated drafts."""
    if not team_context:
        return
    clean_reason = re.sub(r'\s+', ' ', str(reason or 'invalid response')).strip()[:240]
    with team_context._lock:
        team_context.metrics[f'{stage}_fallback_blocks'] += item_count
        if any(
            issue.get('category') == 'qa_stage_fallback'
            and issue.get('stage') == stage
            for issue in team_context.issues
        ):
            return
        team_context.issues.append({
            'category': 'qa_stage_fallback',
            'stage': stage,
            'severity': 'warning',
            'message': (
                f'{stage.replace("_", " ").title()} returned unusable output; '
                f'the last fact-validated translation was retained. {clean_reason}'
            ),
        })


def _record_stage_item_fallback(team_context: 'TranslationTeamContext | None',
                                stage: str, source_text: str, target_text: str,
                                reason: str):
    """Keep enough evidence to retry a rejected review item after the full book run."""
    if not team_context:
        return
    clean_reason = re.sub(r'\s+', ' ', str(reason or 'invalid response')).strip()[:240]
    source_excerpt = (source_text or '')[:300]
    with team_context._lock:
        if any(
            issue.get('category') == 'qa_stage_fallback_item'
            and issue.get('stage') == stage
            and issue.get('source_excerpt') == source_excerpt
            and not issue.get('resolved')
            for issue in team_context.issues
        ):
            return
        team_context.issues.append({
            'category': 'qa_stage_fallback_item',
            'stage': stage,
            'severity': 'warning',
            'message': f'{stage.replace("_", " ").title()} rejected this item. {clean_reason}',
            'source_excerpt': source_excerpt,
            'target_excerpt': (target_text or '')[:300],
        })


def _normalize_memory_text(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip().casefold()


def _human_review_cache_key(source_text: str) -> str:
    return f'human-review:{_normalize_memory_text(source_text)}'


@dataclass
class TranslationTeamContext:
    """Shared, thread-safe knowledge and audit state for one translation job."""

    source_language: str
    target_language: str
    requested_domain: str = 'auto'
    quality_level: str = 'professional'
    enable_back_translation: bool = True
    provider_mode: str = 'online'
    provider_model: str | None = None
    translation_id: int | None = None
    document_name: str | None = None
    glossary: list[dict] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    memory: list[dict] = field(default_factory=list)
    detected_domain: str = 'general'
    risk_level: str = 'normal'
    brief: dict = field(default_factory=dict)
    metrics: Counter = field(default_factory=Counter)
    issues: list[dict] = field(default_factory=list)
    accepted_segments: list[dict] = field(default_factory=list)
    human_approved_sources: set[str] = field(default_factory=set)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self):
        requested = (self.requested_domain or 'auto').lower()
        if requested not in TRANSLATION_DOMAINS:
            requested = 'auto'
        self.requested_domain = requested
        self.quality_level = (
            self.quality_level
            if self.quality_level in {'draft', 'professional'}
            else 'professional'
        )
        self.provider_mode = self.provider_mode if self.provider_mode in {
            'online', 'offline', 'offline_fast', 'offline_quality',
        } else 'online'
        self.detected_domain = requested if requested != 'auto' else 'general'
        self.risk_level = (
            'high' if self.detected_domain in HIGH_RISK_DOMAINS else 'normal'
        )
        self._resource_glossary = list(self.glossary)
        self._resource_entities = list(self.entities)
        self._resource_memory = list(self.memory)
        self._activate_domain_resources()

    def _activate_domain_resources(self):
        selected = {'general', self.detected_domain}

        def applies(item):
            return str(item.get('domain') or 'general').lower() in selected

        self.glossary = [item for item in self._resource_glossary if applies(item)]
        self.entities = [item for item in self._resource_entities if applies(item)]
        self.memory = [item for item in self._resource_memory if applies(item)]
        self._memory_exact = {
            _normalize_memory_text(item.get('source_text', '')): item
            for item in self.memory
            if item.get('source_text') and item.get('target_text')
        }

    @property
    def professional(self) -> bool:
        return self.quality_level == 'professional'

    @property
    def is_offline(self) -> bool:
        return self.provider_mode in {'offline', 'offline_fast', 'offline_quality'}

    @property
    def uses_fast_translation(self) -> bool:
        return self.provider_mode in {'offline_fast', 'offline_quality'}

    @property
    def uses_targeted_aya_review(self) -> bool:
        return self.provider_mode == 'offline_quality'

    @property
    def should_back_translate(self) -> bool:
        return bool(
            self.professional
            and self.enable_back_translation
            and self.risk_level == 'high'
            and not self.is_offline
        )

    @property
    def uses_consolidated_local_review(self) -> bool:
        return self.professional and self.is_offline

    def set_detected_domain(self, domain: str):
        selected = domain if domain in TRANSLATION_DOMAINS - {'auto'} else 'general'
        if self.requested_domain != 'auto':
            selected = self.requested_domain
        self.detected_domain = selected
        self.risk_level = 'high' if selected in HIGH_RISK_DOMAINS else 'normal'
        self._activate_domain_resources()

    def exact_memory_match(self, text: str) -> str | None:
        item = self._memory_exact.get(_normalize_memory_text(text))
        if not item:
            return None
        with self._lock:
            self.metrics['translation_memory_hits'] += 1
        return str(item.get('target_text') or '').strip() or None

    def binding_context(self, texts: list[str] | None = None) -> str:
        source_blob = '\n'.join(texts or []).casefold()
        glossary = []
        for item in self.glossary:
            source_term = str(item.get('source_term') or '')
            if not source_blob or source_term.casefold() in source_blob \
                    or item.get('authority') == 'forbidden':
                glossary.append({
                    'source': source_term,
                    'target': item.get('target_term', ''),
                    'authority': item.get('authority', 'preferred'),
                    'notes': item.get('notes', ''),
                })

        entities = []
        for item in self.entities:
            source_entity = str(item.get('source_entity') or item.get('source') or '')
            if not source_blob or source_entity.casefold() in source_blob:
                entities.append({
                    'source': source_entity,
                    'target': item.get('target_entity') or item.get('target') or '',
                    'type': item.get('entity_type') or item.get('type') or 'other',
                })

        payload = {
            'domain': self.detected_domain,
            'risk_level': self.risk_level,
            'translation_brief': self.brief,
            'glossary': glossary[:200],
            'named_entities': entities[:200],
        }
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))

    def record_stage(self, stage: str, item_count: int):
        with self._lock:
            self.metrics[f'{stage}_blocks'] += item_count

    def record_human_approval(self, source_text: str):
        with self._lock:
            self.human_approved_sources.add(_normalize_memory_text(source_text))
            self.metrics['human_qa_resolution_blocks'] += 1

    def is_human_approved(self, source_text: str) -> bool:
        with self._lock:
            return _normalize_memory_text(source_text) in self.human_approved_sources

    def record_segments(self, source_texts: list[str], translations: dict[int, str]):
        with self._lock:
            for index, source_text in enumerate(source_texts):
                target_text = translations.get(index)
                if target_text:
                    self.accepted_segments.append({
                        'source_text': source_text,
                        'target_text': target_text,
                    })

    def add_issue(self, **issue):
        with self._lock:
            self.issues.append(issue)

    def export_segments(self) -> list[dict]:
        with self._lock:
            unique = {}
            for item in self.accepted_segments:
                unique[_normalize_memory_text(item['source_text'])] = dict(item)
            return list(unique.values())


def create_translation_team_context(source_language: str, target_language: str,
                                    resources: dict | None = None,
                                    domain: str = 'auto',
                                    quality_level: str = 'professional',
                                    enable_back_translation: bool = True,
                                    provider_mode: str = 'online',
                                    provider_model: str | None = None,
                                    translation_id: int | None = None,
                                    document_name: str | None = None,
                                    ) -> TranslationTeamContext:
    resources = resources or {}
    return TranslationTeamContext(
        source_language=source_language,
        target_language=target_language,
        requested_domain=domain,
        quality_level=quality_level,
        enable_back_translation=enable_back_translation,
        provider_mode=provider_mode,
        provider_model=provider_model,
        translation_id=translation_id,
        document_name=document_name,
        glossary=list(resources.get('glossary') or []),
        entities=list(resources.get('entities') or []),
        memory=list(resources.get('memory') or []),
    )


def _call_translation_chat(team_context: TranslationTeamContext | None = None,
                           **kwargs):
    """Dispatch a team stage only to the provider selected for this job."""
    if team_context and team_context.is_offline:
        kwargs['activity_context'] = {
            'translation_id': team_context.translation_id,
            'document_name': team_context.document_name,
            'language_pair': (
                f'{team_context.source_language.upper()} to '
                f'{team_context.target_language.upper()}'
            ),
        }
        return call_local_chat(**kwargs)
    return call_freemodel_chat(**kwargs)

# These values are business facts, not translation material. They are replaced
# with opaque placeholders before the provider call and restored only after an
# exact integrity check. This prevents an invoice translation from changing a
# total, date, identifier, URL, or percentage.
_FACTUAL_TOKEN_PATTERN = re.compile(
    r'''(?x)
    (?i:https?://)[^\s<>]+ |
    [\w.+-]+@[\w.-]+\.[A-Za-z]{2,} |
    \b(?:[A-Za-z0-9-]+\.)+(?i:com|ae|org|net|io|co|uk|edu|gov)\b |
    \b(?i:AED|USD|EUR|GBP|SAR|QAR|KWD|OMR|BHD)\s*[0-9][0-9,]*(?:\.\d+)?\b |
    \b[A-Z]{2,}(?:\s+[A-Z])+\b |
    \b(?:[A-Z]{2,}[A-Z0-9]*)(?:[-/][A-Z0-9]+)+\b |
    \b(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b |
    \b[0-9]+(?:st|nd|rd|th)\b |
    \b[0-9][0-9,]*(?:\.\d+)?\s*% |
    \b[0-9][0-9,]*(?:\.\d+)?\b
    '''
)
_ENGLISH_NUMBER_VALUES = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
    'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5,
    'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9, 'tenth': 10,
    'eleventh': 11, 'twelfth': 12, 'thirteenth': 13,
    'fourteenth': 14, 'fifteenth': 15, 'sixteenth': 16,
    'seventeenth': 17, 'eighteenth': 18, 'nineteenth': 19,
    'twentieth': 20, 'thirtieth': 30, 'fortieth': 40, 'fiftieth': 50,
    'sixtieth': 60, 'seventieth': 70, 'eightieth': 80, 'ninetieth': 90,
    # Literary prose frequently expresses quantities without cardinal words.
    # NLLB may legitimately render these expressions as target-script digits.
    'single': 1, 'once': 1,
    'both': 2, 'pair': 2, 'couple': 2, 'twice': 2, 'double': 2,
    'duo': 2, 'twins': 2,
    'thrice': 3, 'triple': 3, 'trio': 3,
    'dozen': 12,
}
_ENGLISH_NUMBER_SCALES = {'hundred': 100, 'thousand': 1000, 'million': 1000000}
_ENGLISH_NUMBER_WORDS = tuple(_ENGLISH_NUMBER_VALUES) + tuple(_ENGLISH_NUMBER_SCALES)
_ENGLISH_NUMBER_SEQUENCE_PATTERN = re.compile(
    r'\b(?:' + '|'.join(_ENGLISH_NUMBER_WORDS) + r')'
    r'(?:[\s-]+(?:(?:and)[\s-]+)?(?:' + '|'.join(_ENGLISH_NUMBER_WORDS) + r'))*\b',
    re.IGNORECASE,
)
# Local models occasionally preserve the marker name while dropping one or
# both bracket pairs. Treat every bracket variant as the same internal token;
# these strings must never become user-visible document content.
_PROTECTED_TOKEN_PATTERN = re.compile(
    r'(?i)(?:\[\[?\s*)?KEEP[_-](\d{1,4})(?:\s*\]\]?)?'
)
# NLLB can occasionally decode a placeholder into marker-like prose instead of
# a valid KEEP_### token. These fragments are internal corruption, not content.
_MALFORMED_PROTECTED_TOKEN_PATTERN = re.compile(
    r'''(?ix)
    \[\[?\s*(?:SKEEP|SKE(?:EP)?)(?:[_:\-A-Za-z0-9]*) |
    \bSKEEP(?:\b|[_:\-][A-Za-z0-9_:\-]*) |
    \bSKE(?:EP)?_[A-Za-z0-9_:\-]+ |
    \bKEEP_(?!\d{1,4}\b)[A-Za-z][A-Za-z0-9_:\-]* |
    (?:\bSTEP_){3,}\bSTEP\b
    '''
)
# Multi-word title-cased sequences in business documents are normally names or
# addresses, not untranslated prose. They are checked separately from language
# quality so a preserved company/location name does not fail an RTL translation.
_PROPER_NAME_SEQUENCE_PATTERN = re.compile(
    r'\b(?:[A-Z][A-Za-z]{1,}\s+){1,}[A-Z][A-Za-z]{1,}\b'
)
# Dotted framework and package identifiers are names, not untranslated prose.
# They are intentionally removed before the RTL leakage gate evaluates words.
_DOTTED_TECH_IDENTIFIER_PATTERN = re.compile(
    r'\b[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+\b'
)
_COMPOUND_LATIN_IDENTIFIER_PATTERN = re.compile(
    r'\b[A-Za-z][A-Za-z0-9]*(?:[-/][A-Za-z0-9]+)+\b'
)
_CAMEL_CASE_IDENTIFIER_PATTERN = re.compile(
    r'\b(?:[A-Z]{2,}[A-Za-z0-9]*|[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+)\b'
)
_RTL_ALLOWED_LATIN_TERMS = {
    # Abbreviations, platforms, frameworks, languages, and common tools that
    # are conventionally kept in Latin script in Persian/Arabic/Urdu documents.
    'am', 'pm', 'usa', 'uk', 'cia', 'fbi', 'gps', 'vhs', 'dvd',
    'api', 'sdk', 'ui', 'ux', 'cms', 'crm', 'erp', 'seo', 'pwa',
    'cdn', 'waf', 'ddos', 'dns', 'ssl', 'tls', 'http', 'https', 'ftp', 'ssh',
    'ip', 'ipv4', 'ipv6', 'owasp', 'sso', 'oauth', 'jwt', 'mfa', '2fa',
    'qa', 'ci', 'cd', 'devops', 'gdpr', 'iso', 'ai', 'ml',
    'html', 'css', 'javascript', 'typescript', 'react', 'next', 'vue',
    'angular', 'node', 'nodejs', 'python', 'java', 'php', 'sql', 'mysql',
    'postgresql', 'mongodb', 'docker', 'kubernetes', 'linux', 'windows',
    'aws', 'azure', 'gcp', 'firebase', 'wordpress', 'payload', 'cloudflare',
    'tailwind', 'tailwindcss', 'bootstrap', 'laravel', 'django', 'flask',
    'fastapi', 'graphql', 'figma', 'github',
    'git', 'stripe', 'paypal', 'ios', 'android', 'google', 'meta', 'openai',
}


def _normalize_decimal_digits(text: str) -> str:
    """Normalize localized decimal glyphs without changing surrounding text."""
    normalized = []
    for character in text or '':
        try:
            normalized.append(str(unicodedata.decimal(character)))
        except (TypeError, ValueError):
            normalized.append(character)
    return ''.join(normalized)


def _factual_tokens(text: str) -> list[str]:
    """Return ordered, non-overlapping values that must survive translation."""
    normalized_text = _normalize_decimal_digits(text or '')
    return [match.group(0) for match in _FACTUAL_TOKEN_PATTERN.finditer(normalized_text)]


def _spelled_number_facts(text: str) -> Counter:
    """Allow an English quantity expression to become target-language digits."""
    facts = Counter()
    for match in _ENGLISH_NUMBER_SEQUENCE_PATTERN.finditer(text or ''):
        words = re.findall(r'[A-Za-z]+', match.group(0).lower())
        current = 0
        total = 0
        for word in words:
            if word == 'and':
                continue
            if word in _ENGLISH_NUMBER_VALUES:
                current += _ENGLISH_NUMBER_VALUES[word]
                continue
            scale = _ENGLISH_NUMBER_SCALES[word]
            if scale == 100:
                current = max(1, current) * scale
            else:
                total += max(1, current) * scale
                current = 0
        facts[str(total + current)] += 1
    return facts


def _protect_factual_tokens(text: str) -> tuple[str, dict[str, str]]:
    """Replace factual values with stable placeholders before provider input."""
    parts = []
    placeholders: dict[str, str] = {}
    last_end = 0
    for index, match in enumerate(_FACTUAL_TOKEN_PATTERN.finditer(text or '')):
        marker = f'[[KEEP_{index:03d}]]'
        parts.append(text[last_end:match.start()])
        parts.append(marker)
        placeholders[marker] = match.group(0)
        last_end = match.end()
    parts.append((text or '')[last_end:])
    return ''.join(parts), placeholders


def _canonicalize_protected_tokens(text: str,
                                   placeholders: dict[str, str]) -> str:
    """Normalize local-model bracket damage for known protected markers."""
    known = {
        int(match.group(1)): marker
        for marker in placeholders
        if (match := re.fullmatch(r'\[\[KEEP_(\d{3})\]\]', marker))
    }

    def replace(match):
        marker = known.get(int(match.group(1)))
        return marker if marker is not None else match.group(0)

    return _PROTECTED_TOKEN_PATTERN.sub(replace, text or '')


def _needs_model_translation(text: str) -> bool:
    """Return false when a cell contains only facts that must remain unchanged."""
    if not _needs_translation(text):
        return False
    protected, _placeholders = _protect_factual_tokens(text)
    residual = re.sub(r'\[\[KEEP_\d{3}\]\]', ' ', protected)
    return any(char.isalpha() for char in residual)


def _enforce_locked_glossary_terms(source_text: str, translated_text: str,
                                   team_context: TranslationTeamContext | None) -> str:
    """Make approved locked terminology deterministic after model translation.

    Small local models can produce a valid synonym instead of the exact term
    approved for publication.  For a source segment that is itself a locked
    term, the approved target is the only publishable result.  For a longer
    segment, retain the model translation and add the canonical term when it
    was omitted, so the later glossary gate remains meaningful without
    discarding the translated context.
    """
    if not team_context or not translated_text:
        return translated_text

    source_normalized = _normalize_memory_text(source_text)
    enforced = translated_text.strip()
    enforced_count = 0
    for term in team_context.glossary:
        authority = str(term.get('authority') or 'preferred').lower()
        if authority != 'locked' and not team_context.uses_fast_translation:
            continue

        source_term = str(term.get('source_term') or term.get('source') or '').strip()
        target_term = str(term.get('target_term') or term.get('target') or '').strip()
        if not source_term or not target_term:
            continue

        case_sensitive = bool(term.get('case_sensitive'))
        source_haystack = source_text if case_sensitive else source_text.casefold()
        source_needle = source_term if case_sensitive else source_term.casefold()
        target_haystack = enforced if case_sensitive else enforced.casefold()
        target_needle = target_term if case_sensitive else target_term.casefold()
        if source_needle not in source_haystack or target_needle in target_haystack:
            continue

        if source_normalized == _normalize_memory_text(source_term):
            enforced = target_term
        else:
            enforced = f'{enforced.rstrip()} ({target_term})'.strip()
        enforced_count += 1

    if team_context.uses_fast_translation:
        for entity in team_context.entities:
            source_entity = str(
                entity.get('source_entity') or entity.get('source') or ''
            ).strip()
            target_entity = str(
                entity.get('target_entity') or entity.get('target') or ''
            ).strip()
            if not source_entity or not target_entity \
                    or source_entity.casefold() not in source_text.casefold() \
                    or target_entity.casefold() in enforced.casefold():
                continue
            if source_normalized == _normalize_memory_text(source_entity):
                enforced = target_entity
            else:
                enforced = f'{enforced.rstrip()} ({target_entity})'.strip()
            enforced_count += 1

    if enforced_count:
        _validate_fact_integrity(source_text, enforced)
        with team_context._lock:
            team_context.metrics['locked_glossary_enforcements'] += enforced_count
        logger.info(
            '[TRANSLATE] Enforced %s glossary term(s) for a source segment.',
            enforced_count,
        )
    return enforced


def _validate_fact_integrity(source_text: str, translated_text: str):
    """Reject translations which add, remove, or alter protected business facts."""
    if _PROTECTED_TOKEN_PATTERN.search(translated_text or '') \
            or _MALFORMED_PROTECTED_TOKEN_PATTERN.search(translated_text or ''):
        raise TranslationError('Translation contains an unresolved internal protected token.')
    source_facts = Counter(_factual_tokens(source_text))
    translated_facts = Counter(_factual_tokens(translated_text))
    allowed_translated_facts = source_facts + _spelled_number_facts(source_text)
    if source_facts - translated_facts or translated_facts - allowed_translated_facts:
        missing = list((source_facts - translated_facts).elements())
        added = list((translated_facts - allowed_translated_facts).elements())
        details = []
        if missing:
            details.append(f'missing {missing[:3]}')
        if added:
            details.append(f'added {added[:3]}')
        raise TranslationError(
            'Translation changed protected business facts' +
            (f" ({'; '.join(details)})." if details else '.')
        )


def _restore_protected_tokens(source_text: str, translated_text: str,
                              placeholders: dict[str, str]) -> str:
    """Restore only an exact placeholder round trip, then verify source facts."""
    restored = _canonicalize_protected_tokens(translated_text, placeholders)
    for marker, value in placeholders.items():
        if restored.count(marker) != 1:
            raise TranslationError(
                f'Translation did not preserve required protected token {marker}.'
            )
        restored = restored.replace(marker, value)
    if _PROTECTED_TOKEN_PATTERN.search(restored):
        raise TranslationError('Translation returned an unrecognized protected token.')
    _validate_fact_integrity(source_text, restored)
    return restored


def _restore_local_tokens_with_recovery(source_text: str, translated_text: str,
                                        placeholders: dict[str, str],
                                        target_language: str) -> tuple[str, bool]:
    """Recover omitted local-model markers without permitting factual changes."""
    if _MALFORMED_PROTECTED_TOKEN_PATTERN.search(translated_text or ''):
        raise TranslationError('Translation contains a malformed internal protected token.')
    normalized = _canonicalize_protected_tokens(translated_text, placeholders)
    normalized_marker = normalized != (translated_text or '')
    try:
        return _restore_protected_tokens(source_text, normalized, placeholders), normalized_marker
    except TranslationError:
        pass

    restored = normalized
    recovered = normalized_marker
    for marker, value in placeholders.items():
        marker_count = restored.count(marker)
        if marker_count >= 1:
            # Local models sometimes repeat a placeholder even though every
            # marker represents exactly one source occurrence. Restore the
            # first occurrence and discard only redundant marker copies.
            restored = restored.replace(marker, value, 1)
            if marker_count > 1:
                restored = restored.replace(marker, '')
                recovered = True
                logger.warning(
                    '[LOCAL MT] Collapsed %s duplicate occurrence(s) of %s.',
                    marker_count - 1,
                    marker,
                )
            continue
        recovered = True

    if _PROTECTED_TOKEN_PATTERN.search(restored):
        restored = _PROTECTED_TOKEN_PATTERN.sub('', restored)
        recovered = True

    source_facts = Counter(_factual_tokens(source_text))
    translated_facts = Counter(_factual_tokens(restored))
    allowed_translated_facts = source_facts + _spelled_number_facts(source_text)
    if translated_facts - allowed_translated_facts:
        raise TranslationError('Translation added protected business facts.')
    for value, required_count in source_facts.items():
        missing_count = required_count - translated_facts.get(value, 0)
        if missing_count > 0:
            restored = f'{restored.rstrip()} {" ".join([value] * missing_count)}'.strip()
            recovered = True

    code = (target_language or '').lower()
    if code in {'fa', 'ar', 'ur', 'he'}:
        target_letters = [
            char for char in restored
            if (
                ('\u0600' <= char <= '\u06ff' and code in {'fa', 'ar', 'ur'})
                or ('\u0590' <= char <= '\u05ff' and code == 'he')
            )
        ]
        if len(target_letters) < 2:
            raise TranslationError('Local marker recovery found no usable target-language text.')
    _validate_fact_integrity(source_text, restored)
    return restored, recovered


def _restore_fast_tokens_with_sanitization(source_text: str, translated_text: str,
                                           placeholders: dict[str, str],
                                           target_language: str) -> tuple[str, bool]:
    """Discard NLLB-invented factual values before restoring source facts exactly."""
    try:
        restored, recovered = _restore_local_tokens_with_recovery(
            source_text, translated_text, placeholders, target_language
        )
        if '[[KEEP' not in restored:
            return restored, recovered
        sanitized_input = restored
    except TranslationError as exc:
        recoverable_keep_marker = (
            'malformed internal protected token' in str(exc)
            and re.search(r'(?i)\[\[?\s*KEEP[_-]', translated_text or '')
            and not re.search(r'(?i)\[\[?\s*SKEEP', translated_text or '')
        )
        if 'added protected business facts' not in str(exc) \
                and not recoverable_keep_marker:
            raise
        sanitized_input = translated_text

    # NLLB can occasionally decode a number or currency marker as a different
    # real-looking value. Remove all decoded factual values, then let the same
    # recovery path restore only the values present in the source segment.
    # Validation normalizes localized numerals before looking for facts. Apply
    # the same normalization here so Persian/Arabic digits cannot survive the
    # sanitizer and fail again during the fallback integrity check.
    sanitized_input = _normalize_decimal_digits(sanitized_input or '')
    sanitized = _FACTUAL_TOKEN_PATTERN.sub('', sanitized_input)
    try:
        restored, _recovered = _restore_local_tokens_with_recovery(
            source_text, sanitized, placeholders, target_language
        )
        if '[[KEEP' in restored:
            raise TranslationError('Fast translation returned a malformed protected marker.')
    except TranslationError:
        logger.warning(
            '[FAST MT] Fact recovery mismatch. source facts=%s raw facts=%s '
            'sanitized facts=%s source=%r raw=%r',
            _factual_tokens(source_text),
            _factual_tokens(translated_text),
            _factual_tokens(sanitized),
            (source_text or '')[:180],
            (translated_text or '')[:180],
        )
        # A malformed marker can leave punctuation that is not matched by the
        # ordinary marker cleanup. Rebuild the factual suffix from the source
        # rather than trusting any numeric-looking residue from the fast model.
        factual_suffix = ' '.join(_factual_tokens(source_text))
        fallback = _FACTUAL_TOKEN_PATTERN.sub('', sanitized)
        fallback = re.sub(r'\[+\s*KEEP[^\s\]]*\]*', '', fallback, flags=re.IGNORECASE)
        fallback = f'{fallback.rstrip()} {factual_suffix}'.strip()
        _validate_fact_integrity(source_text, fallback)
        _validate_target_language_batch({0: fallback}, target_language, [source_text])
        logger.warning('[FAST MT] Rebuilt a malformed factual suffix from the source.')
        return fallback, True
    logger.warning('[FAST MT] Removed an invented factual value before restoration.')
    return restored, True


def _pending_batches(indices: list[int], texts: list[str]):
    """Yield batches bounded by both item count and prompt character count."""
    batch_indices = []
    batch_texts = []
    batch_chars = 0

    for index, text in zip(indices, texts):
        text_chars = len(text)
        would_overflow = (
            batch_texts
            and (len(batch_texts) >= _BATCH_SIZE or batch_chars + text_chars > _BATCH_CHAR_LIMIT)
        )
        if would_overflow:
            yield batch_indices, batch_texts
            batch_indices = []
            batch_texts = []
            batch_chars = 0

        batch_indices.append(index)
        batch_texts.append(text)
        batch_chars += text_chars

    if batch_texts:
        yield batch_indices, batch_texts


def _translate_batch(texts: list[str], source_language: str, target_language: str,
                     api_key, cache: dict, translation_context: str = '',
                     literary_quality: bool = False,
                     cache_namespace: str = '',
                     team_context: TranslationTeamContext | None = None,
                     progress_callback=None) -> list[str]:
    """
    Translate a list of text snippets using as few API calls as possible.

    Items already in cache, or that don't need translation, are resolved
    locally.  The remaining items are grouped into batches of up to
    _BATCH_SIZE and sent in a single numbered-list prompt each.

    Returns a list of translated strings the same length and order as input.
    """
    if source_language.lower() == target_language.lower():
        return list(texts)

    results: list = [None] * len(texts)
    pending_indices: list[int] = []
    pending_texts: list[str] = []

    for i, text in enumerate(texts):
        value = (text or '').strip()
        if not value or not _needs_model_translation(value):
            results[i] = text
        else:
            human_review = cache.get(_human_review_cache_key(value))
            if not human_review:
                normalized_value = _normalize_memory_text(value)
                human_review = next((
                    cached for key, cached in cache.items()
                    if str(key).startswith('human-review:')
                    and normalized_value.startswith(
                        str(key)[len('human-review:'):]
                    )
                ), None)
            if human_review:
                try:
                    _validate_fact_integrity(value, human_review)
                    results[i] = human_review
                    if team_context:
                        team_context.record_human_approval(value)
                    continue
                except TranslationError:
                    logger.warning(
                        'Ignoring invalid human-review checkpoint for item %s.', i + 1
                    )
            memory_match = team_context.exact_memory_match(value) if team_context else None
            if memory_match:
                try:
                    _validate_fact_integrity(value, memory_match)
                    _validate_target_language_batch({i: memory_match}, target_language, {i: value})
                    results[i] = memory_match
                    continue
                except TranslationError:
                    logger.warning(
                        'Ignoring invalid translation-memory match for item %s.',
                        i + 1,
                    )
            cache_key = f'{cache_namespace}:{i}:{value}' if cache_namespace else value
            if cache_key in cache:
                cached = cache[cache_key]
                try:
                    if literary_quality:
                        _validate_target_language_batch({i: cached}, target_language, {i: value})
                    results[i] = cached
                except TranslationError:
                    logger.warning(
                        'Ignoring invalid cached translation for item %s; re-translating.',
                        i + 1,
                    )
                    pending_indices.append(i)
                    pending_texts.append(value)
            else:
                pending_indices.append(i)
                pending_texts.append(value)

    batches = list(_pending_batches(pending_indices, pending_texts))
    for batch_position, (chunk_indices, chunk_texts) in enumerate(batches):
        batch_started = time.monotonic()
        logger.info(
            '[TRANSLATE] Batch %s/%s started: %s segments, %s characters.',
            batch_position + 1,
            len(batches),
            len(chunk_texts),
            sum(len(item) for item in chunk_texts),
        )
        if progress_callback:
            progress_callback(
                batch_position + 1,
                len(batches),
                'Drafting with deterministic QA',
            )
        translated_chunk = _translate_chunk_resilient(
            chunk_texts,
            source_language,
            target_language,
            api_key,
            translation_context=translation_context,
            literary_quality=literary_quality,
            team_context=team_context,
        )

        for list_pos, (orig_idx, original) in enumerate(zip(chunk_indices, chunk_texts)):
            translated = (translated_chunk.get(list_pos) or original).strip() or original
            translated = _enforce_locked_glossary_terms(
                original,
                translated,
                team_context,
            )
            cache_key = (
                f'{cache_namespace}:{orig_idx}:{original}' if cache_namespace else original
            )
            cache[cache_key] = translated
            results[orig_idx] = translated
        if progress_callback:
            progress_callback(batch_position + 1, len(batches), 'Quality-checked')
        logger.info(
            '[TRANSLATE] Batch %s/%s finished in %.1fs.',
            batch_position + 1,
            len(batches),
            time.monotonic() - batch_started,
        )

    # Any slot still None → use original (safety net)
    for i, text in enumerate(texts):
        if results[i] is None:
            results[i] = text
        results[i] = _enforce_locked_glossary_terms(text, results[i], team_context)

    if team_context:
        team_context.record_segments(
            texts,
            {index: value for index, value in enumerate(results) if value},
        )
    return results


_FAST_REVIEW_MARKERS = re.compile(
    r'\b(?:due|within|shall|must|not|never|except|unless|liable|warranty|payment|'
    r'total|discount|invoice|agreement|termination|liability|confidential)\b',
    re.IGNORECASE,
)


def _targeted_fast_review(source_texts: list[str], drafts: dict[int, str],
                          source_language: str, target_language: str, api_key,
                          translation_context: str,
                          team_context: TranslationTeamContext,
                          *, literary: bool) -> dict[int, str]:
    """Spend Aya capacity only on the fast-draft segments most likely to matter."""
    if not team_context.uses_targeted_aya_review:
        team_context.record_stage('deterministic_qa', len(source_texts))
        return drafts

    merged = dict(drafts)
    # Most short chapter headings can be verified cheaply. Aya is reserved for
    # the small set where the fast draft actually loses a guarded story concept.
    heading_indexes = []
    for index, source in enumerate(source_texts):
        if not literary or not _is_literary_heading(source):
            continue
        try:
            _validate_literary_heading_semantics(
                source, merged[index], source_language, target_language
            )
            team_context.record_stage('deterministic_title_qa', 1)
        except TranslationError:
            heading_indexes.append(index)

    with team_context._lock:
        title_reserved = team_context.metrics['title_reviewer_reserved']
        title_remaining = max(0, _LITERARY_TITLE_REVIEW_BUDGET - title_reserved)
        reviewed_heading_indexes = heading_indexes[:title_remaining]
        deferred_heading_indexes = heading_indexes[title_remaining:]
        team_context.metrics['title_reviewer_reserved'] += len(reviewed_heading_indexes)
        team_context.metrics['title_reviewer_deferred'] += len(deferred_heading_indexes)

    for index in reviewed_heading_indexes:
        source = source_texts[index]
        before = team_context.metrics['title_reviewer_blocks']
        reviewed = _professional_revision_pass(
            [source],
            {0: merged[index]},
            source_language,
            target_language,
            api_key,
            translation_context,
            team_context,
            stage='title_reviewer',
            essential=True,
            strict=True,
            candidate_validator=lambda candidate, source=source: (
                _validate_literary_heading_semantics(
                    source, candidate, source_language, target_language
                )
            ),
        )
        merged[index] = reviewed[0]
        with team_context._lock:
            title_reviewed = team_context.metrics['title_reviewer_blocks'] > before
            team_context.metrics['mandatory_title_review_requested'] += 1
            if title_reviewed:
                team_context.metrics['mandatory_title_reviewed'] += 1
        if not title_reviewed:
            team_context.add_issue(
                category='mandatory_title_review',
                stage='title_reviewer',
                severity='critical',
                message=(
                    'Mandatory chapter-title semantic review did not return a valid '
                    'approved translation.'
                ),
                source_excerpt=source[:300],
                target_excerpt=merged[index][:300],
            )

    for index in deferred_heading_indexes:
        source = source_texts[index]
        team_context.add_issue(
            category='mandatory_title_review',
            stage='title_reviewer',
            severity='critical',
            message=(
                'The automatic chapter-title review budget was reached before this '
                'meaning-sensitive heading could be reviewed.'
            ),
            source_excerpt=source[:300],
            target_excerpt=merged[index][:300],
        )

    candidates: list[tuple[int, int]] = []
    for index, source in enumerate(source_texts):
        if index in heading_indexes:
            continue
        source_folded = source.casefold()
        has_locked_term = any(
            str(term.get('authority') or '').lower() == 'locked'
            and str(term.get('source_term') or term.get('source') or '').casefold()
            in source_folded
            for term in team_context.glossary
        )
        high_risk = bool(_FAST_REVIEW_MARKERS.search(source))
        literary_signal = literary and (
            len(source) >= 420 or '"' in source or '\u201c' in source or '\u201d' in source
        )
        if has_locked_term or high_risk or literary_signal:
            candidates.append((
                (4 if has_locked_term else 0)
                + (3 if high_risk else 0)
                + (2 if literary_signal else 0),
                index,
            ))

    # Keep Aya as a quality specialist, not a second bulk translator.
    if literary and source_texts and not candidates and not heading_indexes:
        # A chapter title that has already passed deterministic semantic QA
        # must not be sent back to Aya merely to satisfy the literary fallback.
        first_content_index = next(
            (
                index for index, source in enumerate(source_texts)
                if not _is_literary_heading(source)
            ),
            None,
        )
        if first_content_index is not None:
            candidates.append((1, first_content_index))
    candidates = [index for _score, index in sorted(candidates, reverse=True)]
    review_budget = int(os.environ.get(
        'FAST_QUALITY_LITERARY_REVIEW_BUDGET' if literary
        else 'FAST_QUALITY_DOCUMENT_REVIEW_BUDGET',
        '8' if literary else '2',
    ))
    with team_context._lock:
        reserved = team_context.metrics['targeted_aya_review_reserved']
        remaining = max(0, review_budget - reserved)
        candidates = candidates[:remaining]
        team_context.metrics['targeted_aya_review_reserved'] += len(candidates)
    if not candidates:
        team_context.record_stage('deterministic_qa', len(source_texts) - len(heading_indexes))
        return merged

    review_sources = [source_texts[index] for index in candidates]
    review_drafts = {position: drafts[index] for position, index in enumerate(candidates)}
    reviewed = _professional_revision_pass(
        review_sources,
        review_drafts,
        source_language,
        target_language,
        api_key,
        translation_context,
        team_context,
        stage='semantic_reviewer',
    )
    for position, index in enumerate(candidates):
        merged[index] = reviewed[position]
    team_context.record_stage('targeted_aya_review', len(candidates))
    team_context.record_stage(
        'deterministic_qa', len(source_texts) - len(candidates) - len(heading_indexes)
    )
    return merged


def _translate_chunk_resilient(texts: list[str], source_language: str,
                                target_language: str, api_key,
                                split_depth: int = 0,
                                translation_context: str = '',
                                literary_quality: bool = False,
                                team_context: TranslationTeamContext | None = None
                                ) -> dict[int, str]:
    """Translate a batch, splitting it when a provider cannot return a valid response."""
    try:
        translated = _call_batch_api(
            texts,
            source_language,
            target_language,
            api_key,
            translation_context=translation_context,
            literary_quality=literary_quality,
            team_context=team_context,
        )
        if literary_quality:
            if team_context and team_context.uses_fast_translation:
                translated = _targeted_fast_review(
                    texts, translated, source_language, target_language, api_key,
                    translation_context, team_context, literary=True,
                )
            else:
                translated = _polish_translation_chunk(
                    texts,
                    translated,
                    source_language,
                    target_language,
                    api_key,
                    translation_context,
                    team_context,
                )
                if team_context:
                    team_context.record_stage('editor', len(texts))
        elif team_context and team_context.professional \
                and not team_context.uses_consolidated_local_review:
            translated = _professional_revision_pass(
                texts,
                translated,
                source_language,
                target_language,
                api_key,
                translation_context,
                team_context,
                stage='editor',
            )
        if team_context and team_context.professional:
            if team_context.uses_fast_translation and not literary_quality:
                translated = _targeted_fast_review(
                    texts, translated, source_language, target_language, api_key,
                    translation_context, team_context, literary=False,
                )
            elif team_context.is_offline and not literary_quality:
                # The local document draft is already fact-validated below. A second pass by
                # the same small local model roughly doubles invoice latency without adding
                # independent assurance, so retain deterministic QA as the final document gate.
                team_context.record_stage('deterministic_qa', len(texts))
            elif not (literary_quality and team_context.uses_consolidated_local_review):
                translated = _professional_revision_pass(
                    texts,
                    translated,
                    source_language,
                    target_language,
                    api_key,
                    translation_context,
                    team_context,
                    stage='semantic_reviewer',
                )
        if team_context and team_context.should_back_translate:
            translated = _back_translation_review(
                texts,
                translated,
                source_language,
                target_language,
                api_key,
                translation_context,
                team_context,
            )
        for index, source_text in enumerate(texts):
            _validate_fact_integrity(source_text, translated[index])
        _validate_target_language_batch(translated, target_language, texts)
        return translated
    except TranslationError as exc:
        if len(texts) == 1:
            if team_context and team_context.uses_fast_translation \
                    and 'target-language QA' in str(exc):
                logger.warning(
                    '[FAST MT] Single segment failed language QA; sending it to the local reviewer.'
                )
                repaired = _repair_fast_segment_with_local_reviewer(
                    texts[0],
                    source_language,
                    target_language,
                    api_key,
                    translation_context,
                    literary_quality,
                    team_context,
                )
                team_context.record_stage('fast_draft_qa_repaired_by_reviewer', 1)
                return {0: repaired}
            raise
        if split_depth >= _MAX_BATCH_SPLIT_DEPTH:
            raise

        midpoint = len(texts) // 2
        logger.warning(
            'Retrying invalid translation batch as %s and %s items.',
            midpoint, len(texts) - midpoint,
        )
        left = _translate_chunk_resilient(
            texts[:midpoint], source_language, target_language, api_key,
            split_depth + 1, translation_context, literary_quality, team_context,
        )
        right = _translate_chunk_resilient(
            texts[midpoint:], source_language, target_language, api_key,
            split_depth + 1, translation_context, literary_quality, team_context,
        )
        merged = {i: value for i, value in left.items()}
        merged.update({midpoint + i: value for i, value in right.items()})
        return merged


def _call_batch_api(texts: list[str], source_language: str, target_language: str,
                    api_key, translation_context: str = '',
                    literary_quality: bool = False,
                    team_context: TranslationTeamContext | None = None
                    ) -> dict[int, str]:
    """
    Send a numbered list of texts to the translation API.
    Returns a dict mapping 0-based index → translated string.
    Raises TranslationError instead of silently publishing untranslated text.
    """
    protected_items = [_protect_factual_tokens(text) for text in texts]
    normalized_texts = [re.sub(r'\s+', ' ', item[0]).strip() for item in protected_items]
    if team_context and team_context.uses_fast_translation:
        try:
            drafts = translate_fast_batch(
                normalized_texts,
                source_language.lower(),
                target_language.lower(),
                activity_context={
                    'translation_id': team_context.translation_id,
                    'document_name': team_context.document_name,
                    'language_pair': f'{source_language.upper()} to {target_language.upper()}',
                },
            )
        except FastTranslationError as exc:
            raise TranslationError(str(exc)) from exc
        if len(drafts) != len(texts):
            raise TranslationError(
                f'Fast translation returned {len(drafts)} items for {len(texts)} inputs.'
            )
        restored = {}
        recovered_items = []
        for index, draft in enumerate(drafts):
            try:
                value, recovered = _restore_fast_tokens_with_sanitization(
                    texts[index],
                    draft,
                    protected_items[index][1],
                    target_language,
                )
            except TranslationError as exc:
                # NLLB can occasionally return source-language text for a short
                # segment with a damaged marker. Hand only that segment to Aya
                # instead of discarding the whole page or publishing bad text.
                repairable_fast_error = any(fragment in str(exc) for fragment in (
                    'target-language text',
                    'internal protected token',
                ))
                if not repairable_fast_error:
                    raise
                logger.warning(
                    '[FAST MT] Segment %s/%s needs local reviewer repair: %s',
                    index + 1,
                    len(texts),
                    exc,
                )
                value = _repair_fast_segment_with_local_reviewer(
                    texts[index],
                    source_language,
                    target_language,
                    api_key,
                    translation_context,
                    literary_quality,
                    team_context,
                )
                recovered = True
                team_context.record_stage('fast_draft_repaired_by_reviewer', 1)
            restored[index] = value
            if recovered:
                recovered_items.append(index)
        if recovered_items:
            logger.info(
                '[FAST MT] Reinserted protected facts for %s segment(s).',
                len(recovered_items),
            )
            team_context.record_stage('protected_fact_recovery', len(recovered_items))
        team_context.record_stage('fast_draft', len(texts))
        _validate_target_language_batch(restored, target_language, texts)
        return restored

    numbered = "\n".join(f"{n + 1}. {text}" for n, text in enumerate(normalized_texts))
    context_section = (
        f"\nBook context and binding terminology:\n{translation_context[:12000]}\n"
        if translation_context
        else ''
    )
    team_section = (
        '\nProfessional translation brief, glossary, and named-entity memory:\n'
        f'{team_context.binding_context(texts)[:16000]}\n'
        if team_context
        else ''
    )
    literary_rules = (
        "- Translate as publishable literary prose, preserving voice, mood, subtext, and dialogue rhythm.\n"
        "- Prefer natural target-language wording over literal, word-for-word phrasing.\n"
        "- Follow the supplied character names, places, honorifics, and terminology consistently.\n"
        if literary_quality
        else ''
    )
    prompt = (
        f"Translate the following items from {get_language_label(source_language)} "
        f"to {get_language_label(target_language)}.\n"
        "Rules:\n"
        f"{literary_rules}"
        "- Preserve every [[KEEP_###]] token exactly once and in its original item.\n"
        "- Do not add, remove, or invent numbers, dates, currency, identifiers, or URLs.\n"
        "- Keep each item separate and return the same number of items.\n"
        "- Return ONLY the translated items in the same numbered list format.\n"
        "- Do NOT add explanations, headers, or extra text.\n"
        f"{context_section}{team_section}\n"
        + numbered
    )
    system_prompt = (
        'You are the lead literary translator for a professional Persian publishing team. '
        'Produce fluent, idiomatic, publication-quality prose while preserving meaning and voice. '
        if literary_quality and target_language.lower() == 'fa'
        else 'You are a professional document translation engine. '
    ) + 'Output only a numbered list of translations, nothing else.'
    last_error = 'empty response'
    last_quality_error = None
    best_parsed: dict[int, str] = {}
    attempts = _stage_attempt_limit(team_context, essential=True)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_translation_chat(
                team_context,
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.2 if literary_quality else 0.1,
                max_tokens=max(2000, min(12000, len(numbered) * 3)),
                api_key=api_key,
                model_name=_DRAFT_MODEL,
            )
            cleaned = _clean_output(response or '')
            parsed = _parse_numbered_response(cleaned, len(texts))
            if len(parsed) > len(best_parsed):
                best_parsed = parsed
            if len(parsed) == len(texts):
                restored = {}
                recovered_items = []
                for index in range(len(texts)):
                    if team_context and team_context.is_offline:
                        value, recovered = _restore_local_tokens_with_recovery(
                            texts[index],
                            parsed[index],
                            protected_items[index][1],
                            target_language,
                        )
                        if recovered:
                            recovered_items.append(index)
                    else:
                        value = _restore_protected_tokens(
                            texts[index],
                            parsed[index],
                            protected_items[index][1],
                        )
                    restored[index] = value
                if recovered_items:
                    logger.warning(
                        '[TRANSLATE] Reinserted protected facts for %s local item(s): %s.',
                        len(recovered_items),
                        ', '.join(str(index + 1) for index in recovered_items[:10]),
                    )
                    team_context.record_stage(
                        'protected_fact_recovery', len(recovered_items)
                    )
                _validate_target_language_batch(restored, target_language, texts)
                return restored
            last_error = f'expected {len(texts)} items, received {len(parsed)}'
        except FreemodelProviderError:
            raise
        except Exception as exc:
            last_error = str(exc)
            if isinstance(exc, TranslationItemQualityError):
                last_quality_error = exc

        logger.warning(
            'Translation API attempt %s/%s failed: %s', attempt, attempts, last_error
        )
        if attempt < attempts and _RETRY_BASE_SECONDS:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

    recovered = {}
    invalid_indices = set()
    for index, parsed_text in best_parsed.items():
        try:
            recovered[index] = _restore_protected_tokens(
                texts[index],
                parsed_text,
                protected_items[index][1],
            )
        except TranslationError as exc:
            logger.warning(
                'Discarding invalid partial translation item %s/%s: %s',
                index + 1,
                len(texts),
                exc,
            )
            invalid_indices.add(index)

    missing_indices = [
        index for index in range(len(texts))
        if index not in recovered or index in invalid_indices
    ]
    if recovered and missing_indices:
        logger.warning(
            'Recovering partial translation batch: kept %s/%s items; retrying %s missing items individually.',
            len(recovered),
            len(texts),
            len(missing_indices),
        )
        for index in missing_indices:
            single = _call_batch_api(
                [texts[index]],
                source_language,
                target_language,
                api_key,
                translation_context=translation_context,
                literary_quality=literary_quality,
                team_context=team_context,
            )
            recovered[index] = single[0]
        _validate_target_language_batch(recovered, target_language, texts)
        return recovered

    if last_quality_error is not None:
        raise last_quality_error
    raise TranslationError(f'Translation provider failed after {attempts} attempts ({last_error}).')


def _repair_fast_segment_with_local_reviewer(source_text: str, source_language: str,
                                              target_language: str, api_key,
                                              translation_context: str,
                                              literary_quality: bool,
                                              team_context: TranslationTeamContext) -> str:
    """Translate one rejected fast-model segment with Aya and preserve job context."""
    reviewer_context = TranslationTeamContext(
        source_language=source_language,
        target_language=target_language,
        requested_domain=team_context.requested_domain,
        quality_level=team_context.quality_level,
        enable_back_translation=False,
        provider_mode='offline',
        provider_model=team_context.provider_model,
        translation_id=team_context.translation_id,
        document_name=team_context.document_name,
        glossary=list(team_context.glossary),
        entities=list(team_context.entities),
        memory=list(team_context.memory),
    )
    reviewer_context.set_detected_domain(team_context.detected_domain)
    reviewer_context.brief = dict(team_context.brief)

    repaired = _call_batch_api(
        [source_text],
        source_language,
        target_language,
        api_key,
        translation_context=translation_context,
        literary_quality=literary_quality,
        team_context=reviewer_context,
    )
    value = repaired[0]
    _validate_fact_integrity(source_text, value)
    _validate_target_language_batch({0: value}, target_language, [source_text])
    return value


def _remove_source_backed_identifiers(text: str, source_text: str) -> str:
    """Remove schema, code, and product identifiers that belong in Latin script."""
    cleaned = text or ''
    identifiers = set(_COMPOUND_LATIN_IDENTIFIER_PATTERN.findall(source_text or ''))
    identifiers.update(_CAMEL_CASE_IDENTIFIER_PATTERN.findall(source_text or ''))
    identifiers = sorted(
        identifiers,
        key=len,
        reverse=True,
    )
    for identifier in identifiers:
        parts = re.split(r'[-/]', identifier)
        flexible_identifier = r'[-/]'.join(re.escape(part) for part in parts)
        source_backed = re.compile(
            rf'(?i)(?<![A-Za-z0-9]){flexible_identifier}(?![A-Za-z0-9])'
        )
        cleaned = source_backed.sub(' ', cleaned)
    return cleaned


def _target_script_checkable_text(text: str, source_text: str = '') -> str:
    """Remove protected and established Latin terminology before script QA."""
    checkable_text = _remove_source_backed_identifiers(text, source_text)
    checkable_text = _FACTUAL_TOKEN_PATTERN.sub(' ', checkable_text)
    checkable_text = _PROPER_NAME_SEQUENCE_PATTERN.sub(' ', checkable_text)
    checkable_text = _DOTTED_TECH_IDENTIFIER_PATTERN.sub(' ', checkable_text)

    def remove_allowed_term(match):
        return ' ' if match.group(0).casefold() in _RTL_ALLOWED_LATIN_TERMS else match.group(0)

    return re.sub(r'\b[A-Za-z][A-Za-z]{1,}\b', remove_allowed_term, checkable_text)


def _target_script_ratio(text: str, language_code: str,
                         source_text: str = '') -> float:
    """Return the share of checkable letters written in the expected target script."""
    checkable_text = _target_script_checkable_text(text, source_text)
    letters = [char for char in checkable_text if char.isalpha()]
    if not letters:
        return 1.0

    code = (language_code or '').lower()
    if code in {'fa', 'ar', 'ur'}:
        matching = [char for char in letters if '\u0600' <= char <= '\u06ff']
    elif code == 'he':
        matching = [char for char in letters if '\u0590' <= char <= '\u05ff']
    else:
        return 1.0
    return len(matching) / len(letters)


def _latin_word_leakage(text: str, language_code: str,
                        source_text: str = '') -> list[str]:
    """Return suspicious English prose while retaining established technical names."""
    code = (language_code or '').lower()
    if code not in {'fa', 'ar', 'ur'}:
        return []

    checkable_text = _target_script_checkable_text(text, source_text)
    return re.findall(r'\b[A-Za-z][A-Za-z]{1,}\b', checkable_text)


def _is_latin_technical_label(source_text: str, translated_text: str,
                              leaked_words: list[str]) -> bool:
    """Allow a short product label, but never allow copied English prose."""
    source = (source_text or '').strip()
    if not source or len(source) > 120 or re.search(r'[!?]', source):
        return False

    source_words = re.findall(r'\b[A-Za-z][A-Za-z]{1,}\b', source)
    if not source_words or len(source_words) > 8:
        return False

    source_folded = {word.casefold() for word in source_words}
    if any(word.casefold() not in source_folded for word in leaked_words):
        return False

    has_technical_signal = bool(_DOTTED_TECH_IDENTIFIER_PATTERN.search(source)) or any(
        word.casefold() in _RTL_ALLOWED_LATIN_TERMS or word.isupper()
        for word in source_words
    )
    if not has_technical_signal:
        return False

    # Unknown preserved words must look like names (Payload, CoreCare), not
    # sentence words such as "is", "used", or "tomorrow".
    return all(word[0].isupper() or word.isupper() for word in leaked_words)


def _validate_target_language_batch(translations: dict[int, str], target_language: str,
                                    source_texts: list[str] | dict[int, str] | None = None):
    """Reject substantial source-language leakage in complex-script output."""
    for index, translated in translations.items():
        source_text = (
            source_texts.get(index, '')
            if isinstance(source_texts, dict)
            else (
                source_texts[index]
                if source_texts is not None and index < len(source_texts)
                else ''
            )
        )
        checkable_text = _target_script_checkable_text(translated, source_text)
        letters = [char for char in checkable_text if char.isalpha()]
        letter_count = len(letters)
        if letter_count < 12:
            continue
        ratio = _target_script_ratio(translated, target_language, source_text)
        leaked_words = _latin_word_leakage(
            translated, target_language, source_text
        )
        target_letter_count = round(letter_count * ratio)
        if target_letter_count == 0 and source_texts is not None:
            if _is_latin_technical_label(source_text, translated, leaked_words):
                continue
        required_ratio = _QUALITY_MIN_TARGET_SCRIPT_RATIO
        if len(leaked_words) == 1 and target_letter_count >= 4:
            required_ratio = min(required_ratio, 0.25)
        elif len(leaked_words) == 2 and target_letter_count >= 8:
            required_ratio = min(required_ratio, 0.40)
        if ratio < required_ratio:
            detail = (
                f'English leakage: {", ".join(leaked_words[:5])}'
                if len(leaked_words) > _MAX_RTL_LATIN_WORDS
                else f'{ratio:.0%} expected-script letters'
            )
            raise TranslationItemQualityError(
                f'Item {index + 1} failed target-language QA ({detail}).',
                source_text,
                translated,
                index,
            )


def _polish_translation_chunk(source_texts: list[str], drafts: dict[int, str],
                              source_language: str, target_language: str,
                              api_key, translation_context: str,
                              team_context: TranslationTeamContext | None = None
                              ) -> dict[int, str]:
    """Run an independent literary editor pass and validate its final output."""
    records = []
    protected_drafts = []
    for index, source in enumerate(source_texts):
        draft = drafts[index]
        _validate_fact_integrity(source, draft)
        protected_source, _source_placeholders = _protect_factual_tokens(source)
        protected_draft, draft_placeholders = _protect_factual_tokens(draft)
        protected_drafts.append((draft, draft_placeholders))
        records.append(
            f'{index + 1}. SOURCE: {re.sub(r"\\s+", " ", protected_source).strip()}\n'
            f'DRAFT: {re.sub(r"\\s+", " ", protected_draft).strip()}'
        )

    target_label = get_language_label(target_language)
    prompt = (
        f'Edit each draft into publication-quality {target_label} literary prose.\n'
        'Correct grammar, unnatural wording, literal phrasing, dialogue punctuation, register, '
        'and continuity while preserving the complete source meaning.\n'
        'Binding rules:\n'
        '- Preserve every [[KEEP_###]] token exactly once in its own item.\n'
        '- Do not add plot details, remove nuances, summarize, censor, or explain.\n'
        '- Keep character voice, tense, point of view, mood, and paragraph boundaries.\n'
        '- Follow the book context and terminology consistently.\n'
        '- Return only the final edited text as the same numbered list.\n\n'
        f'BOOK CONTEXT:\n{translation_context[:12000]}\n\n'
        + '\n\n'.join(records)
    )
    last_error = 'empty editor response'
    attempts = _stage_attempt_limit(team_context)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_translation_chat(
                team_context,
                system_prompt=(
                    f'You are a senior {target_label} literary editor and translation QA lead. '
                    'Return only the corrected numbered items.'
                ),
                user_prompt=prompt,
                temperature=0.15,
                max_tokens=max(2000, min(12000, sum(len(item) for item in records) * 3)),
                api_key=api_key,
                model_name=_EDITOR_MODEL,
            )
            parsed = _parse_numbered_response(_clean_output(response or ''), len(source_texts))
            polished = dict(drafts)
            invalid_items = []
            for index, source in enumerate(source_texts):
                if index not in parsed:
                    invalid_items.append(index)
                    continue
                try:
                    _draft, placeholders = protected_drafts[index]
                    candidate = _restore_protected_tokens(
                        source, parsed[index], placeholders
                    )
                    _validate_target_language_batch(
                        {index: candidate}, target_language, {index: source}
                    )
                    polished[index] = candidate
                except TranslationError:
                    invalid_items.append(index)
            _validate_target_language_batch(polished, target_language, source_texts)
            if invalid_items:
                _record_stage_fallback(
                    team_context,
                    'literary_editor',
                    len(invalid_items),
                    f'{len(invalid_items)} of {len(source_texts)} items were rejected',
                )
            return polished
        except Exception as exc:
            last_error = str(exc)

        logger.warning(
            'Literary editor attempt %s/%s failed: %s',
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts and _RETRY_BASE_SECONDS:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    _record_stage_fallback(
        team_context, 'literary_editor', len(source_texts), last_error
    )
    return dict(drafts)


def _professional_revision_pass(source_texts: list[str],
                                drafts: dict[int, str],
                                source_language: str,
                                target_language: str,
                                api_key,
                                translation_context: str,
                                team_context: TranslationTeamContext,
                                stage: str,
                                back_translations: dict[int, str] | None = None,
                                essential: bool = False,
                                strict: bool = False,
                                candidate_validator=None,
                                max_tokens: int | None = None,
                                context_char_limit: int = 8000,
                                ) -> dict[int, str]:
    """Run an independent editor or semantic reviewer and return approved text."""
    protected_drafts = []
    records = []
    for index, source_text in enumerate(source_texts):
        draft = drafts[index]
        _validate_fact_integrity(source_text, draft)
        protected_draft, placeholders = _protect_factual_tokens(draft)
        protected_drafts.append((draft, placeholders))
        record = (
            f'{index + 1}. SOURCE: {re.sub(r"\\s+", " ", source_text).strip()}\n'
            f'CANDIDATE: {re.sub(r"\\s+", " ", protected_draft).strip()}'
        )
        if back_translations:
            record += (
                '\nBACK-TRANSLATION: '
                + re.sub(r'\s+', ' ', back_translations.get(index, '')).strip()
            )
        records.append(record)

    target_label = get_language_label(target_language)
    source_label = get_language_label(source_language)
    if stage == 'title_reviewer':
        role = f'senior bilingual {target_label} literary title editor'
        instructions = (
            'Translate and review each chapter heading as publication-ready literary '
            'text. Preserve every concrete narrative concept exactly: never replace fog '
            'with wind or storm, a lighthouse with a torch or generic light, or a tide '
            'with a storm. Keep names, chapter numbering, and tone intact.'
        )
    elif stage == 'editor':
        role = f'senior {target_label} translation editor'
        instructions = (
            'Edit each candidate for fluent native wording, grammar, register, domain style, '
            'terminology, and consistency. Preserve the complete source meaning without adding '
            'or removing information.'
        )
    elif stage == 'back_translation_reviewer':
        role = 'bilingual back-translation reconciliation specialist'
        instructions = (
            f'Compare the {source_label} source, the {target_label} candidate, and its '
            f'{source_label} back-translation. Correct every omission, addition, ambiguity, '
            'polarity error, scope error, or mistranslation in the target candidate.'
        )
    else:
        if team_context.uses_consolidated_local_review:
            role = 'senior bilingual translation editor and semantic reviewer'
            instructions = (
                'Perform one complete final review: independently compare every source and '
                'candidate, then correct fluency, grammar, register, terminology, omissions, '
                'additions, ambiguity, polarity, scope, names, and numbers. For high-risk '
                'content, internally back-translate the candidate before approving it.'
            )
        else:
            role = 'independent bilingual semantic translation reviewer'
            instructions = (
                'Independently compare every source and candidate. Correct mistranslations, '
                'omissions, additions, ambiguity, tone errors, inconsistent names, and domain '
                'terminology. Do not merely proofread the target language.'
            )

    prompt = (
        f'{instructions}\n'
        'Binding rules:\n'
        '- Preserve every [[KEEP_###]] token from CANDIDATE exactly once in its own item.\n'
        '- Obey locked glossary terms and never use forbidden terms.\n'
        '- Keep each item separate and preserve its full informational content.\n'
        '- Return only the final approved target text in the same numbered-list format.\n'
        + (
            '- For a single title, return exactly `1. <approved title>` with no commentary.\n'
            if stage == 'title_reviewer' else ''
        )
        + '\n'
        f'TEAM KNOWLEDGE:\n{team_context.binding_context(source_texts)[:16000]}\n'
        f'{translation_context[:max(0, context_char_limit)]}\n\n'
        + '\n\n'.join(records)
    )
    last_error = 'empty response'
    attempts = _stage_attempt_limit(team_context, essential=essential)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_translation_chat(
                team_context,
                system_prompt=(
                    f'You are the {role}. You are independent from the draft translator. '
                    'Return only approved numbered target-language items.'
                ),
                user_prompt=prompt,
                temperature=0.05,
                max_tokens=max_tokens or (
                    256 if stage == 'title_reviewer'
                    else max(2000, min(12000, sum(len(item) for item in records) * 3))
                ),
                api_key=api_key,
                model_name=_REVIEWER_MODEL if stage != 'editor' else _EDITOR_MODEL,
            )
            parsed = _parse_numbered_response(
                _clean_output(response or ''),
                len(source_texts),
            )
            revised = dict(drafts)
            invalid_items = []
            invalid_reasons = {}
            for index, source_text in enumerate(source_texts):
                if index not in parsed:
                    invalid_items.append(index)
                    invalid_reasons[index] = 'The reviewer omitted the required numbered item.'
                    continue
                try:
                    _draft, placeholders = protected_drafts[index]
                    candidate = _restore_protected_tokens(
                        source_text,
                        parsed[index],
                        placeholders,
                    )
                    _validate_target_language_batch(
                        {index: candidate}, target_language, {index: source_text}
                    )
                    if candidate_validator:
                        candidate_validator(candidate)
                    revised[index] = candidate
                except TranslationError as exc:
                    invalid_items.append(index)
                    invalid_reasons[index] = str(exc)
            if invalid_items and strict:
                raise TranslationError(
                    f'{len(invalid_items)} of {len(source_texts)} item(s) failed required review.'
                )
            _validate_target_language_batch(revised, target_language, source_texts)
            team_context.record_stage(stage, len(source_texts) - len(invalid_items))
            if invalid_items:
                _record_stage_fallback(
                    team_context,
                    stage,
                    len(invalid_items),
                    f'{len(invalid_items)} of {len(source_texts)} items were rejected',
                )
                for index in invalid_items:
                    _record_stage_item_fallback(
                        team_context,
                        stage,
                        source_texts[index],
                        drafts[index],
                        invalid_reasons.get(index, 'invalid reviewer output'),
                    )
            return revised
        except Exception as exc:
            last_error = str(exc)
        logger.warning(
            '%s attempt %s/%s failed: %s',
            stage,
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts and _RETRY_BASE_SECONDS:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    _record_stage_fallback(team_context, stage, len(source_texts), last_error)
    for index, source_text in enumerate(source_texts):
        _record_stage_item_fallback(
            team_context, stage, source_text, drafts[index], last_error
        )
    return dict(drafts)


def _back_translation_review(source_texts: list[str],
                             translations: dict[int, str],
                             source_language: str,
                             target_language: str,
                             api_key,
                             translation_context: str,
                             team_context: TranslationTeamContext
                             ) -> dict[int, str]:
    """Back-translate high-risk text, then reconcile it against the source."""
    protected_targets = [
        _protect_factual_tokens(translations[index])
        for index in range(len(source_texts))
    ]
    numbered = '\n'.join(
        f'{index + 1}. {re.sub(r"\\s+", " ", item[0]).strip()}'
        for index, item in enumerate(protected_targets)
    )
    prompt = (
        f'Back-translate these approved {get_language_label(target_language)} items into '
        f'{get_language_label(source_language)} as literally and completely as possible.\n'
        'Preserve every [[KEEP_###]] token exactly once. Do not consult or imitate the '
        'original source wording. Return only the same numbered list.\n\n'
        + numbered
    )
    last_error = 'empty back-translation response'
    attempts = _stage_attempt_limit(team_context)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_translation_chat(
                team_context,
                system_prompt=(
                    'You are an independent back-translator used for high-risk translation QA. '
                    'Return only numbered back-translations.'
                ),
                user_prompt=prompt,
                temperature=0.0,
                max_tokens=max(2000, min(12000, len(numbered) * 3)),
                api_key=api_key,
                model_name=_REVIEWER_MODEL,
            )
            parsed = _parse_numbered_response(
                _clean_output(response or ''),
                len(source_texts),
            )
            if len(parsed) != len(source_texts):
                raise TranslationError(
                    f'expected {len(source_texts)} items, received {len(parsed)}'
                )
            back_translations = {}
            for index in range(len(source_texts)):
                target_text, placeholders = (
                    translations[index],
                    protected_targets[index][1],
                )
                back_translations[index] = _restore_protected_tokens(
                    target_text,
                    parsed[index],
                    placeholders,
                )
            team_context.record_stage('back_translation', len(source_texts))
            return _professional_revision_pass(
                source_texts,
                translations,
                source_language,
                target_language,
                api_key,
                translation_context,
                team_context,
                stage='back_translation_reviewer',
                back_translations=back_translations,
            )
        except Exception as exc:
            last_error = str(exc)
        logger.warning(
            'Back-translation attempt %s/%s failed: %s',
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts and _RETRY_BASE_SECONDS:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    _record_stage_fallback(
        team_context, 'back_translation', len(source_texts), last_error
    )
    return dict(translations)


def _parse_numbered_response(response: str, item_count: int) -> dict[int, str]:
    """Parse numbered output while allowing wrapped lines inside an item."""
    parsed: dict[int, str] = {}
    current_index = None

    for raw_line in (response or '').splitlines():
        line = raw_line.strip()
        match = re.match(r'^(\d+)[.)]\s*(.*)', line)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < item_count and index not in parsed:
                current_index = index
                parsed[index] = match.group(2).strip()
            else:
                current_index = None
        elif line and current_index is not None:
            parsed[current_index] = f'{parsed[current_index]} {line}'.strip()

    parsed = {index: value for index, value in parsed.items() if value}
    # Small local models occasionally return a one-item title without the list
    # marker. Recover only that unambiguous single-item form.
    if item_count == 1 and not parsed:
        single_item = (response or '').strip()
        if single_item:
            parsed[0] = single_item
    return parsed


def _load_translation_checkpoint_payload(checkpoint_path: str | None) -> dict:
    if not checkpoint_path or not os.path.isfile(checkpoint_path):
        return {}
    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as checkpoint_file:
            payload = json.load(checkpoint_file)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError) as exc:
        logger.warning('Ignoring unreadable translation checkpoint %s: %s', checkpoint_path, exc)
        return {}


def _load_translation_checkpoint(checkpoint_path: str | None) -> dict:
    payload = _load_translation_checkpoint_payload(checkpoint_path)
    cache = payload.get('translations', {})
    return cache if isinstance(cache, dict) else {}


def _save_translation_checkpoint(checkpoint_path: str | None, cache: dict,
                                 metadata: dict | None = None):
    if not checkpoint_path:
        return
    checkpoint_dir = os.path.dirname(os.path.abspath(checkpoint_path))
    os.makedirs(checkpoint_dir, exist_ok=True)
    temp_path = f'{checkpoint_path}.{uuid.uuid4().hex}.tmp'
    try:
        with _checkpoint_lock:
            with open(temp_path, 'w', encoding='utf-8') as checkpoint_file:
                json.dump(
                    {
                        'version': 2,
                        'translations': cache,
                        'metadata': metadata or {},
                    },
                    checkpoint_file,
                    ensure_ascii=False,
                    separators=(',', ':'),
                )
            # Windows can temporarily hold a just-closed checkpoint open.
            # Retrying the atomic replacement preserves resumability instead
            # of failing a long translation for a transient sharing violation.
            for attempt in range(1, 6):
                try:
                    os.replace(temp_path, checkpoint_path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(0.05 * attempt)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning('Could not remove checkpoint temporary file %s', temp_path)


def validate_human_translation(source_text: str, target_text: str,
                               target_language: str):
    """Trust the user's language choice while still protecting source facts."""
    if not (target_text or '').strip():
        raise TranslationError('The approved translation cannot be empty.')
    _validate_fact_integrity(source_text, target_text)


def apply_human_review_corrections(checkpoint_path: str,
                                   corrections: list[dict]) -> int:
    """Store approved passages as durable overrides for a resumed translation."""
    payload = _load_translation_checkpoint_payload(checkpoint_path)
    cache = payload.get('translations')
    if not isinstance(cache, dict):
        cache = {}
    metadata = payload.get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}

    applied = 0
    for item in corrections:
        source_text = str(item.get('source_text') or '').strip()
        target_text = str(item.get('target_text') or '').strip()
        if not source_text or not target_text:
            continue
        cache[_human_review_cache_key(source_text)] = target_text
        source_key = _normalize_memory_text(source_text)
        for key in list(cache):
            if str(key).startswith('human-review:'):
                continue
            key_text = str(key)
            cached_source = (
                key_text.split(':', 2)[2]
                if re.match(r'^book-page-\d+:\d+:', key_text)
                else key_text
            )
            if _normalize_memory_text(cached_source).startswith(source_key):
                cache[key] = target_text
        applied += 1

    _save_translation_checkpoint(checkpoint_path, cache, metadata)
    return applied


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

_BROKEN_PDF_LIGATURE_WIDTHS = {
    'ebgaramond': {
        0.506: 'fl',
        0.518: 'fi',
        0.575: 'ff',
        0.761: 'ffl',
        0.776: 'ffi',
    },
    'josefinsans': {
        0.949: 'ffi',
    },
}


def _pdf_span_text(span: dict) -> str:
    """Recover known embedded-font ligatures that MuPDF reports as U+FFFD."""
    if 'text' in span:
        return str(span.get('text') or '')

    characters = span.get('chars') or []
    font_name = str(span.get('font') or '').casefold()
    width_map = next((
        mapping for family, mapping in _BROKEN_PDF_LIGATURE_WIDTHS.items()
        if family in font_name
    ), {})
    font_size = max(0.01, float(span.get('size') or 1.0))
    output = []
    for character in characters:
        value = str(character.get('c') or '')
        if value != '\ufffd' or not width_map:
            output.append(value)
            continue
        bbox = character.get('bbox') or (0.0, 0.0, 0.0, 0.0)
        width_ratio = (float(bbox[2]) - float(bbox[0])) / font_size
        nearest_ratio = min(width_map, key=lambda candidate: abs(candidate - width_ratio))
        output.append(
            width_map[nearest_ratio]
            if abs(nearest_ratio - width_ratio) <= 0.02
            else value
        )
    return ''.join(output)


def _collect_text_groups(page, literary: bool = False, textpage=None) -> list[dict]:
    """Extract visual lines for documents or paragraph blocks for literary prose."""
    groups = []
    try:
        extract_options = {'flags': 11}
        if textpage is not None:
            extract_options['textpage'] = textpage
        blocks = page.get_text("dict", **extract_options)["blocks"]
        has_replacement_glyph = any(
            '\ufffd' in str(span.get('text') or '')
            for block in blocks if block.get('type') == 0
            for line in block.get('lines', [])
            for span in line.get('spans', [])
        )
        if has_replacement_glyph:
            blocks = page.get_text("rawdict", **extract_options)["blocks"]
            repaired = sum(
                str(character.get('c') or '') == '\ufffd'
                and '\ufffd' not in _pdf_span_text(span)
                for block in blocks if block.get('type') == 0
                for line in block.get('lines', [])
                for span in line.get('spans', [])
                for character in span.get('chars', [])
                if str(character.get('c') or '') == '\ufffd'
            )
            if repaired:
                logger.info(
                    '[EXTRACT] Recovered %s embedded-font ligature(s) on page %s.',
                    repaired,
                    page.number + 1,
                )
    except Exception as exc:
        logger.warning("get_text failed: %s", exc)
        return groups

    if literary:
        for block in blocks:
            if block.get('type') != 0:
                continue
            lines = block.get('lines', [])
            spans = [span for line in lines for span in line.get('spans', [])]
            line_texts = [
                ' '.join(
                    _pdf_span_text(span).strip()
                    for span in line.get('spans', [])
                    if _pdf_span_text(span).strip()
                )
                for line in lines
            ]
            line_texts = [text for text in line_texts if text]
            if not spans or not line_texts:
                continue
            dominant = max(spans, key=lambda span: len(_pdf_span_text(span).strip()))
            bbox = block.get('bbox') or dominant.get('bbox')
            if not bbox:
                continue
            groups.append({
                'text': ' '.join(line_texts),
                'bbox': bbox,
                'size': float(dominant.get('size', 10.0)),
                'color': _fitz_color_to_tuple(dominant.get('color', 0)),
                'font': dominant.get('font', ''),
                'flags': int(dominant.get('flags', 0)),
                'ocr': textpage is not None,
            })
        return groups

    for block in blocks:
        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):
            line_spans = line.get("spans", [])
            line_text = " ".join(
                _pdf_span_text(span)
                for span in line_spans
                if _pdf_span_text(span).strip()
            )
            if not line_text.strip() or not line_spans:
                continue

            dominant = max(
                line_spans,
                key=lambda span: len(_pdf_span_text(span).strip()),
            )
            bbox = line.get("bbox") or dominant.get("bbox")
            if not bbox:
                continue

            groups.append({
                'text': line_text,
                'bbox': bbox,
                'size': float(dominant.get("size", 10.0)),
                'color': _fitz_color_to_tuple(dominant.get("color", 0)),
                'font': dominant.get("font", ""),
                'flags': int(dominant.get("flags", 0)),
                'ocr': textpage is not None,
            })

    return groups


def _collect_text_groups_with_ocr(page, source_language: str,
                                  literary: bool = False) -> list[dict]:
    """Use embedded text first, then Tesseract-backed coordinate OCR."""
    groups = _collect_text_groups(page, literary=literary)
    if groups:
        return groups
    language_map = {
        'en': 'eng',
        'es': 'spa',
        'fr': 'fra',
        'de': 'deu',
        'it': 'ita',
        'pt': 'por',
        'ar': 'ara',
        'fa': 'fas',
        'he': 'heb',
        'ur': 'urd',
    }
    try:
        textpage = page.get_textpage_ocr(
            language=language_map.get((source_language or '').lower(), 'eng'),
            dpi=220,
            full=True,
        )
        groups = _collect_text_groups(
            page,
            literary=literary,
            textpage=textpage,
        )
        if groups:
            logger.info('Coordinate OCR recovered %s text groups.', len(groups))
        return groups
    except Exception as exc:
        logger.warning('Coordinate OCR unavailable for scanned PDF page: %s', exc)
        return []


def _document_uses_literary_mode(page_groups: list[list[dict]], total_pages: int,
                                 requested_mode: str) -> bool:
    profile = _detect_document_profile(
        page_groups,
        total_pages,
        requested_mode,
        requested_domain='auto',
    )
    return profile['mode'] == 'literary'


def _detect_document_profile(page_groups: list[list[dict]], total_pages: int,
                             requested_mode: str,
                             requested_domain: str = 'auto') -> dict:
    """Route the document by textual evidence, not page count alone."""
    mode = (requested_mode or 'auto').lower()
    if mode not in {'auto', 'literary', 'document'}:
        raise TranslationError(f'Unsupported translation mode: {requested_mode}.')

    text = ' '.join(group['text'] for groups in page_groups for group in groups)
    normalized = re.sub(r'\s+', ' ', text).casefold()
    letter_count = sum(char.isalpha() for char in text)
    word_count = len(re.findall(r'\b\w+\b', text, flags=re.UNICODE))
    sentence_count = max(1, len(re.findall(r'[.!?。！？]+', text)))
    dialogue_count = len(re.findall(r'(?:^|\s)["“‘—-][A-Za-z\u0600-\u06ff]', text))
    chapter_count = len(re.findall(
        r'(?im)^\s*(?:chapter|part|book|prologue|epilogue)\b',
        text,
    ))
    average_sentence_words = word_count / sentence_count
    literary_score = 0
    literary_score += 3 if total_pages >= _LITERARY_MIN_PAGES else 0
    literary_score += 2 if chapter_count >= 1 else 0
    literary_score += 2 if dialogue_count >= 3 else 0
    literary_score += 1 if average_sentence_words >= 12 else 0
    literary_score += 1 if word_count >= 1500 else 0

    domain_keywords = {
        'legal': (
            'agreement', 'whereas', 'hereby', 'party', 'parties', 'liability',
            'indemn', 'governing law', 'termination', 'confidentiality',
        ),
        'financial': (
            'invoice', 'subtotal', 'tax', 'amount due', 'balance sheet',
            'revenue', 'payment', 'account number', 'currency',
        ),
        'medical': (
            'patient', 'diagnosis', 'dosage', 'prescription', 'clinical',
            'contraindication', 'symptom', 'treatment', 'medical history',
        ),
        'technical': (
            'api', 'configuration', 'installation', 'system', 'software',
            'algorithm', 'specification', 'database', 'protocol',
        ),
        'marketing': (
            'brand', 'campaign', 'customer', 'benefit', 'offer', 'audience',
            'market', 'product', 'conversion',
        ),
    }
    scores = {
        domain: sum(normalized.count(keyword) for keyword in keywords)
        for domain, keywords in domain_keywords.items()
    }
    requested_domain = (requested_domain or 'auto').lower()
    if requested_domain in TRANSLATION_DOMAINS - {'auto'}:
        domain = requested_domain
    elif (
        literary_score >= 4 and letter_count >= 500
    ) or (
        total_pages >= _LITERARY_MIN_PAGES and letter_count >= 1000
    ):
        domain = 'literary'
    else:
        best_domain, best_score = max(scores.items(), key=lambda item: item[1])
        domain = best_domain if best_score >= 2 else 'general'

    if mode == 'literary':
        detected_mode = 'literary'
        domain = 'literary' if requested_domain == 'auto' else domain
    elif mode == 'document':
        detected_mode = 'document'
    else:
        detected_mode = (
            'literary'
            if domain == 'literary'
            or (literary_score >= 5 and letter_count >= 1000)
            else 'document'
        )

    return {
        'mode': detected_mode,
        'domain': domain,
        'risk_level': 'high' if domain in HIGH_RISK_DOMAINS else 'normal',
        'literary_score': literary_score,
        'domain_scores': scores,
    }


def _page_source_text(groups: list[dict], limit: int | None = None) -> str:
    value = '\n'.join(group['text'].strip() for group in groups if group['text'].strip())
    return value[:limit] if limit else value


def _book_sample(page_groups: list[list[dict]]) -> str:
    """Sample the beginning, middle, and end so the book bible is representative."""
    if not page_groups:
        return ''
    page_count = len(page_groups)
    candidate_indexes = list(range(min(6, page_count)))
    candidate_indexes.extend([
        page_count // 4,
        page_count // 2,
        (page_count * 3) // 4,
        page_count - 1,
    ])
    chunks = []
    remaining = _BOOK_BIBLE_SAMPLE_CHARS
    for page_index in dict.fromkeys(candidate_indexes):
        if remaining <= 0:
            break
        page_text = _page_source_text(page_groups[page_index], min(2500, remaining))
        if page_text:
            chunks.append(f'PAGE {page_index + 1}:\n{page_text}')
            remaining -= len(page_text)
    return '\n\n'.join(chunks)


def _parse_json_object(response: str) -> dict:
    cleaned = _clean_output(response or '')
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start < 0 or end <= start:
        raise TranslationError('AI response did not contain a JSON object.')
    try:
        value = json.loads(cleaned[start:end + 1])
    except (ValueError, TypeError) as exc:
        raise TranslationError(f'AI response contained invalid JSON: {exc}') from exc
    if not isinstance(value, dict):
        raise TranslationError('AI response JSON must be an object.')
    return value


def _build_translation_brief(page_groups: list[list[dict]],
                             source_language: str,
                             target_language: str,
                             api_key,
                             team_context: TranslationTeamContext) -> dict:
    """Have an intake analyst produce a bounded, auditable document brief."""
    if team_context.uses_fast_translation and not team_context.uses_targeted_aya_review:
        brief = {
            'domain': team_context.detected_domain,
            'purpose': 'Faithful, complete document translation',
            'audience': 'Original document audience',
            'register': 'Professional and source-faithful',
            'risk_flags': (
                ['High-risk terminology and factual values require strict preservation']
                if team_context.risk_level == 'high' else []
            ),
            'style_rules': [
                'Preserve all facts, identifiers, amounts, dates, and named entities',
                'Use fluent target-language grammar without adding or omitting meaning',
            ],
            'entities': [],
            'terms': [],
        }
        team_context.brief = brief
        team_context.record_stage('deterministic_intake', 1)
        logger.info('[FAST MT] Built the document brief deterministically; Aya was not used.')
        return brief

    sample = _book_sample(page_groups)
    prompt = (
        f'Analyze this {get_language_label(source_language)} document before translation to '
        f'{get_language_label(target_language)}.\n'
        f'The deterministic router classified it as domain "{team_context.detected_domain}".\n'
        'Return JSON only with this exact shape:\n'
        '{"domain":"","purpose":"","audience":"","register":"","risk_flags":[],'
        '"style_rules":[],"entities":[{"source":"","target":"","type":"","notes":""}],'
        '"terms":[{"source":"","target":"","authority":"preferred","notes":""}]}\n'
        'Do not invent facts. For legal, financial, and medical material, identify ambiguity '
        'and high-risk terminology. Existing team terminology below is binding:\n'
        f'{team_context.binding_context()[:12000]}\n\nDOCUMENT SAMPLE:\n{sample}'
    )
    last_error = 'empty response'
    attempts = _stage_attempt_limit(team_context)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_translation_chat(
                team_context,
                system_prompt=(
                    'You are the intake analyst and terminology manager for a professional '
                    'translation team. Return valid JSON only.'
                ),
                user_prompt=prompt,
                temperature=0.05,
                max_tokens=1200,
                api_key=api_key,
                model_name=_EDITOR_MODEL,
            )
            value = _parse_json_object(response or '')
            reported_domain = str(value.get('domain') or '').strip().lower()
            if team_context.requested_domain == 'auto' \
                    and reported_domain in TRANSLATION_DOMAINS - {'auto'}:
                team_context.set_detected_domain(reported_domain)
            brief = {
                'domain': team_context.detected_domain,
                'purpose': str(value.get('purpose') or '')[:500],
                'audience': str(value.get('audience') or '')[:500],
                'register': str(value.get('register') or '')[:500],
                'risk_flags': list(value.get('risk_flags') or [])[:30],
                'style_rules': list(value.get('style_rules') or [])[:30],
                'entities': list(value.get('entities') or [])[:200],
                'terms': list(value.get('terms') or [])[:300],
            }
            team_context.brief = brief
            team_context.entities.extend(brief['entities'])
            team_context.glossary.extend([
                {
                    'source_term': item.get('source', ''),
                    'target_term': item.get('target', ''),
                    'authority': item.get('authority', 'preferred'),
                    'notes': item.get('notes', ''),
                    'case_sensitive': False,
                }
                for item in brief['terms']
                if item.get('source') and item.get('target')
            ])
            team_context.record_stage('intake_analysis', 1)
            return brief
        except Exception as exc:
            last_error = str(exc)
        logger.warning(
            'Translation brief attempt %s/%s failed: %s',
            attempt,
            attempts,
            last_error,
        )
        if attempt < attempts and _RETRY_BASE_SECONDS:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    brief = {
        'domain': team_context.detected_domain,
        'purpose': 'Faithful, complete document translation',
        'audience': 'Original document audience',
        'register': 'Professional and source-faithful',
        'risk_flags': (
            ['High-risk terminology and factual values require strict preservation']
            if team_context.risk_level == 'high'
            else []
        ),
        'style_rules': [
            'Preserve all facts, identifiers, amounts, dates, and named entities',
            'Use fluent target-language grammar without adding or omitting meaning',
        ],
        'entities': [],
        'terms': [],
    }
    team_context.brief = brief
    team_context.record_stage('intake_analysis', 1)
    _record_stage_fallback(team_context, 'intake_analysis', 1, last_error)
    return brief


def _build_book_bible(page_groups: list[list[dict]], source_language: str,
                      target_language: str, api_key,
                      team_context: TranslationTeamContext | None = None) -> dict:
    """Create a binding terminology and style guide before page translation."""
    if team_context and team_context.uses_fast_translation \
            and not team_context.uses_targeted_aya_review:
        team_context.record_stage('deterministic_intake', 1)
        return {
            'genre': '',
            'tone': 'Preserve the source tone',
            'narrative_voice': 'Preserve the source narrator and point of view',
            'style_rules': [
                'Do not add, omit, summarize, or explain source content',
                'Keep names, terms, tense, and dialogue style consistent',
            ],
            'characters': [],
            'places': [],
            'terms': [],
        }
    sample = _book_sample(page_groups)
    if team_context and team_context.is_offline:
        sample = sample[:6000]
    prompt = (
        f'Analyze this {get_language_label(source_language)} story before it is translated to '
        f'{get_language_label(target_language)}. Build a concise translation bible.\n'
        'Return JSON only with this exact shape:\n'
        '{"genre":"", "tone":"", "narrative_voice":"", "style_rules":[], '
        '"characters":[{"source":"", "target":"", "notes":""}], '
        '"places":[{"source":"", "target":""}], '
        '"terms":[{"source":"", "target":"", "notes":""}]}\n'
        'Infer only what the sample supports. Keep names and recurring terms consistent. '
        'For Farsi, choose natural Persian transliterations and literary conventions.\n\n'
        + sample
    )
    last_error = 'empty response'
    attempts = _stage_attempt_limit(team_context)
    for attempt in range(1, attempts + 1):
        try:
            response = _call_translation_chat(
                team_context,
                system_prompt=(
                    'You are a senior book-translation editor preparing a binding style sheet. '
                    'Return valid JSON only.'
                ),
                user_prompt=prompt,
                temperature=0.1,
                max_tokens=1200,
                api_key=api_key,
                model_name=_EDITOR_MODEL,
            )
            bible = _parse_json_object(response or '')
            return {
                'genre': str(bible.get('genre', ''))[:200],
                'tone': str(bible.get('tone', ''))[:500],
                'narrative_voice': str(bible.get('narrative_voice', ''))[:500],
                'style_rules': list(bible.get('style_rules') or [])[:20],
                'characters': list(bible.get('characters') or [])[:100],
                'places': list(bible.get('places') or [])[:100],
                'terms': list(bible.get('terms') or [])[:200],
            }
        except Exception as exc:
            last_error = str(exc)
        logger.warning(
            'Book bible attempt %s/%s failed: %s', attempt, attempts, last_error
        )
        if attempt < attempts and _RETRY_BASE_SECONDS:
            time.sleep(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    _record_stage_fallback(team_context, 'book_bible', 1, last_error)
    return {
        'genre': '',
        'tone': 'Preserve the source tone',
        'narrative_voice': 'Preserve the source narrator and point of view',
        'style_rules': [
            'Do not add, omit, summarize, or explain source content',
            'Keep names, terms, tense, and dialogue style consistent',
        ],
        'characters': [],
        'places': [],
        'terms': [],
    }


def _chapter_labels(page_groups: list[list[dict]]) -> list[str]:
    labels = []
    current = ''
    for groups in page_groups:
        for group in groups[:5]:
            candidate = re.sub(r'\s+', ' ', group['text']).strip()
            if _is_literary_heading(candidate):
                current = candidate
                break
        labels.append(current)
    return labels


def _book_page_context(page_index: int, page_groups: list[list[dict]],
                       book_bible: dict, chapter_label: str = '') -> str:
    context = ['BINDING BOOK BIBLE:', json.dumps(book_bible, ensure_ascii=False)]
    if chapter_label:
        context.extend(['CURRENT CHAPTER:', chapter_label])
    for label, index in (
        ('PREVIOUS PAGE', page_index - 1),
        ('CURRENT PAGE', page_index),
        ('NEXT PAGE', page_index + 1),
    ):
        if 0 <= index < len(page_groups):
            page_text = _page_source_text(page_groups[index], _BOOK_CONTEXT_CHAR_LIMIT)
            if page_text:
                context.extend([f'{label} SOURCE:', page_text])
    return '\n'.join(context)


def _automated_quality_report(source_groups: list[list[dict]],
                              translated_pages: list[list[str]],
                              target_language: str,
                              literary: bool,
                              team_context: TranslationTeamContext | None = None
                              ) -> dict:
    checked = 0
    unchanged = 0
    weighted_ratio = 0.0
    weighted_letters = 0
    protected_facts = 0
    segment_targets: dict[str, set[str]] = {}
    issues = [
        dict(item) for item in (team_context.issues if team_context else [])
        if not item.get('resolved')
    ]
    glossary_checks = 0
    entity_checks = 0
    for page_index, (groups, translations) in enumerate(
        zip(source_groups, translated_pages),
        start=1,
    ):
        for block_index, (group, translated) in enumerate(
            zip(groups, translations),
            start=1,
        ):
            if _is_non_translatable_value(group['text']):
                continue
            checked += 1
            _validate_fact_integrity(group['text'], translated)
            protected_facts += len(_factual_tokens(group['text']))
            if team_context and team_context.is_human_approved(group['text']):
                continue
            if group['text'].strip().casefold() == translated.strip().casefold():
                unchanged += 1
                issues.append({
                    'category': 'untranslated_text',
                    'severity': 'critical' if literary else 'error',
                    'message': 'Source and target text are unchanged.',
                    'page_number': page_index,
                    'block_number': block_index,
                    'source_excerpt': group['text'][:1000],
                    'target_excerpt': translated[:1000],
                })
            checkable_text = _target_script_checkable_text(
                translated, group['text']
            )
            letters = sum(char.isalpha() for char in checkable_text)
            weighted_ratio += _target_script_ratio(
                translated, target_language, group['text']
            ) * letters
            weighted_letters += letters
            source_key = _normalize_memory_text(group['text'])
            segment_targets.setdefault(source_key, set()).add(
                _normalize_memory_text(translated)
            )

            if literary and _is_literary_heading(group['text']):
                try:
                    _validate_literary_heading_semantics(
                        group['text'], translated, 'en', target_language
                    )
                except TranslationError as exc:
                    issues.append({
                        'category': 'heading_semantics',
                        'severity': 'critical',
                        'message': str(exc),
                        'page_number': page_index,
                        'block_number': block_index,
                        'source_excerpt': group['text'][:1000],
                        'target_excerpt': translated[:1000],
                    })

            if team_context:
                source_cmp = group['text']
                target_cmp = translated
                for term in team_context.glossary:
                    source_term = str(term.get('source_term') or term.get('source') or '')
                    target_term = str(term.get('target_term') or term.get('target') or '')
                    if not source_term or not target_term:
                        continue
                    authority = str(term.get('authority') or 'preferred').lower()
                    case_sensitive = bool(term.get('case_sensitive'))
                    source_haystack = source_cmp if case_sensitive else source_cmp.casefold()
                    source_needle = source_term if case_sensitive else source_term.casefold()
                    target_haystack = target_cmp if case_sensitive else target_cmp.casefold()
                    target_needle = target_term if case_sensitive else target_term.casefold()
                    if authority == 'forbidden':
                        if target_needle in target_haystack:
                            issues.append({
                                'category': 'forbidden_terminology',
                                'severity': 'critical',
                                'message': f'Forbidden target term used: {target_term}',
                                'page_number': page_index,
                                'block_number': block_index,
                                'source_excerpt': source_cmp[:1000],
                                'target_excerpt': target_cmp[:1000],
                            })
                        continue
                    if source_needle not in source_haystack:
                        continue
                    glossary_checks += 1
                    if target_needle not in target_haystack:
                        issues.append({
                            'category': 'glossary',
                            'severity': 'critical' if authority == 'locked' else 'warning',
                            'message': (
                                f'{authority.title()} translation missing for '
                                f'"{source_term}" -> "{target_term}".'
                            ),
                            'page_number': page_index,
                            'block_number': block_index,
                            'source_excerpt': source_cmp[:1000],
                            'target_excerpt': target_cmp[:1000],
                        })

                for entity in team_context.entities:
                    source_entity = str(
                        entity.get('source_entity') or entity.get('source') or ''
                    )
                    target_entity = str(
                        entity.get('target_entity') or entity.get('target') or ''
                    )
                    if not source_entity or not target_entity \
                            or source_entity.casefold() not in source_cmp.casefold():
                        continue
                    entity_checks += 1
                    if target_entity.casefold() not in target_cmp.casefold():
                        issues.append({
                            'category': 'named_entity',
                            'severity': 'error',
                            'message': (
                                f'Named entity translation is inconsistent: '
                                f'"{source_entity}" should be "{target_entity}".'
                            ),
                            'page_number': page_index,
                            'block_number': block_index,
                            'source_excerpt': source_cmp[:1000],
                            'target_excerpt': target_cmp[:1000],
                        })

    script_ratio = weighted_ratio / weighted_letters if weighted_letters else 1.0
    inconsistent_segments = sum(
        1 for targets in segment_targets.values() if len(targets) > 1
    )
    if inconsistent_segments:
        issues.append({
            'category': 'consistency',
            'severity': 'warning',
            'message': (
                f'{inconsistent_segments} repeated source segment(s) have inconsistent '
                'target translations.'
            ),
        })

    if literary:
        for issue in issues:
            if issue.get('category') == 'qa_stage_fallback_item':
                issue['severity'] = 'critical'
                issue['message'] = (
                    'Unresolved literary QA failure: ' + issue.get('message', '')
                )

    if literary and unchanged and not team_context:
        raise TranslationError(
            f'Automated QA found {unchanged} untranslated literary text block(s).'
        )
    if not team_context:
        score = round(min(99.0, 90.0 + (script_ratio * 9.0)), 1)
    else:
        penalties = {'warning': 1.5, 'error': 5.0, 'critical': 15.0}
        score = round(max(
            0.0,
            100.0
            - sum(penalties.get(item.get('severity'), 2.0) for item in issues)
            - max(0.0, (1.0 - script_ratio) * 20.0),
        ), 1)

    qa_complete = not any(item.get('severity') == 'critical' for item in issues)
    report = {
        'score': score,
        'mode': 'literary' if literary else 'document',
        'qa_complete': qa_complete,
        'publication_ready': qa_complete and score >= _QUALITY_GATE_SCORE,
        'blocks_checked': checked,
        'unchanged_blocks': unchanged,
        'protected_facts_checked': protected_facts,
        'target_script_ratio': round(script_ratio, 4),
        'ai_editor_pass': qa_complete and bool(
            literary
            or (
                team_context
                and team_context.professional
                and not (team_context.is_offline and not literary)
            )
        ),
        'human_review_required': False,
        'issues': issues,
    }
    if team_context:
        report.update({
            'provider_mode': team_context.provider_mode,
            'provider_model': team_context.provider_model,
            'domain': team_context.detected_domain,
            'risk_level': team_context.risk_level,
            'quality_level': team_context.quality_level,
            'pipeline_stages': [
                'intake_analyst',
                'terminology_manager',
                'translation_memory',
                'draft_translator',
                *(
                    ['deterministic_fact_and_language_qa']
                    if team_context.is_offline and not literary
                    else (
                        ['consolidated_local_editor_reviewer']
                        if team_context.uses_consolidated_local_review
                        else ['target_language_editor', 'semantic_reviewer']
                    )
                ),
                *(
                    ['back_translator', 'back_translation_reviewer']
                    if team_context.should_back_translate
                    else []
                ),
                'fact_checker',
                'consistency_checker',
                'layout_engineer',
                'publication_gate',
            ],
            'translation_memory_hits': team_context.metrics['translation_memory_hits'],
            'editor_blocks': team_context.metrics['editor_blocks'],
            'semantic_reviewed_blocks': team_context.metrics['semantic_reviewer_blocks'],
            'back_translation_blocks': team_context.metrics['back_translation_blocks'],
            'mandatory_title_reviews_requested': team_context.metrics[
                'mandatory_title_review_requested'
            ],
            'mandatory_title_reviews_completed': team_context.metrics[
                'mandatory_title_reviewed'
            ],
            'deterministic_title_checks': team_context.metrics['deterministic_title_qa_blocks'],
            'final_recovery_reviews': team_context.metrics['final_recovery_reviewer_blocks'],
            'final_recovery_planned': team_context.metrics['final_literary_recovery_planned'],
            'final_recovery_deferred': team_context.metrics['final_literary_recovery_deferred'],
            'recovered_review_items': sum(
                1 for item in team_context.issues if item.get('resolved')
            ),
            'glossary_checks': glossary_checks,
            'entity_checks': entity_checks,
            'inconsistent_repeated_segments': inconsistent_segments,
            'human_review_recommended': team_context.detected_domain in {'legal', 'medical'},
            'translation_brief': team_context.brief,
        })
        critical_issues = [
            item for item in issues if item.get('severity') == 'critical'
        ]
        if critical_issues:
            sample = '; '.join(item['message'] for item in critical_issues[:3])
            raise TranslationQualityError(
                f'Publication gate rejected the translation: {sample}',
                report,
            )
        if score < _QUALITY_GATE_SCORE:
            raise TranslationQualityError(
                f'Publication gate score {score:.1f}/100 is below the required '
                f'{_QUALITY_GATE_SCORE:.1f}/100.',
                report,
            )
    return report


def _translate_literary_pages(page_groups: list[list[dict]], source_language: str,
                                target_language: str, api_key, cache: dict,
                                checkpoint_path: str | None,
                                checkpoint_metadata: dict,
                                progress_reporter,
                                cancel_callback=None,
                                team_context: TranslationTeamContext | None = None
                                ) -> tuple[list[list[str]], dict]:
    """Translate book pages concurrently using a shared immutable story bible."""
    book_bible = checkpoint_metadata.get('book_bible')
    if not isinstance(book_bible, dict) or not book_bible:
        progress_reporter(0, 'Building AI story bible and terminology memory...')
        book_bible = _build_book_bible(
            page_groups, source_language, target_language, api_key, team_context
        )
        checkpoint_metadata['book_bible'] = book_bible
        checkpoint_metadata['translation_mode'] = 'literary'
        _save_translation_checkpoint(checkpoint_path, cache, checkpoint_metadata)
    if team_context:
        team_context.set_detected_domain('literary')
        team_context.brief = book_bible
        team_context.entities.extend([
            {
                'source': item.get('source', ''),
                'target': item.get('target', ''),
                'type': 'character',
                'notes': item.get('notes', ''),
            }
            for item in book_bible.get('characters', [])
        ] + [
            {
                'source': item.get('source', ''),
                'target': item.get('target', ''),
                'type': 'place',
                'notes': item.get('notes', ''),
            }
            for item in book_bible.get('places', [])
        ])
        team_context.glossary.extend([
            {
                'source_term': item.get('source', ''),
                'target_term': item.get('target', ''),
                'authority': 'preferred',
                'notes': item.get('notes', ''),
            }
            for item in book_bible.get('terms', [])
        ])
        team_context.record_stage('intake_analysis', 1)

    translated_pages: list[list[str] | None] = [None] * len(page_groups)
    chapter_labels = _chapter_labels(page_groups)
    configured_workers = (
        1 if team_context and team_context.is_offline else _PAGE_WORKERS
    )
    worker_count = min(configured_workers, max(1, len(page_groups)))
    sequential_mode = worker_count == 1

    def translate_page(page_index: int):
        groups = page_groups[page_index]
        if not groups:
            return page_index, [], {}
        local_cache = dict(cache)
        namespace = f'book-page-{page_index + 1}'
        translations = _translate_batch(
            [group['text'] for group in groups],
            source_language,
            target_language,
            api_key,
            local_cache,
            translation_context=_book_page_context(
                page_index, page_groups, book_bible, chapter_labels[page_index]
            ),
            literary_quality=True,
            cache_namespace=namespace,
            team_context=team_context,
            progress_callback=(
                lambda batch_current, batch_total, state: progress_reporter(
                    page_index,
                    f'Translating page {page_index + 1} of {len(page_groups)}: '
                    f'batch {batch_current} of {batch_total} - {state}.',
                )
                if sequential_mode else None
            ),
        )
        prefix = f'{namespace}:'
        page_cache = {
            key: value for key, value in local_cache.items() if key.startswith(prefix)
        }
        return page_index, translations, page_cache

    completed = 0

    def commit_page(page_index: int, translations: list[str], page_cache: dict):
        """Durably save one finished page before advancing user-visible progress."""
        nonlocal completed
        translated_pages[page_index] = translations
        cache.update(page_cache)
        _save_translation_checkpoint(checkpoint_path, cache, checkpoint_metadata)
        completed += 1
        progress_reporter(
            completed,
            f'AI-translated and edited {completed} of {len(page_groups)} pages.',
        )

    # A one-worker ThreadPoolExecutor can continue dequeuing pages while its
    # caller is blocked committing an earlier result. Offline translation must
    # instead provide strict backpressure: translate, checkpoint, then advance.
    if sequential_mode:
        for page_index in range(len(page_groups)):
            if cancel_callback and cancel_callback():
                raise TranslationCancelled('Translation cancelled by user.')
            progress_reporter(
                completed,
                f'Translating page {page_index + 1} of {len(page_groups)}...',
            )
            result_page, translations, page_cache = translate_page(page_index)
            commit_page(result_page, translations, page_cache)
        return [page or [] for page in translated_pages], book_bible

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix='literary-page',
    ) as executor:
        futures = {}
        for page_index in range(len(page_groups)):
            if cancel_callback and cancel_callback():
                raise TranslationCancelled('Translation cancelled by user.')
            futures[executor.submit(translate_page, page_index)] = page_index

        for future in as_completed(futures):
            if cancel_callback and cancel_callback():
                for pending in futures:
                    pending.cancel()
                raise TranslationCancelled('Translation cancelled by user.')
            page_index, translations, page_cache = future.result()
            commit_page(page_index, translations, page_cache)

    return [page or [] for page in translated_pages], book_bible


def _final_literary_recovery(page_groups: list[list[dict]],
                             translated_pages: list[list[str]],
                             source_language: str, target_language: str,
                             api_key, book_bible: dict,
                             team_context: TranslationTeamContext | None,
                             *, cache: dict | None = None,
                             checkpoint_path: str | None = None,
                             checkpoint_metadata: dict | None = None,
                             progress_reporter=None,
                             cancel_callback=None):
    """Repair recorded literary QA failures without re-reviewing the whole book."""
    if not team_context or not team_context.uses_targeted_aya_review:
        return translated_pages

    with team_context._lock:
        unresolved = [
            dict(issue) for issue in team_context.issues
            if not issue.get('resolved')
            and issue.get('category') in {
                'qa_stage_fallback_item', 'mandatory_title_review',
            }
        ]

    recovery_targets: dict[tuple[int, int], bool] = {}
    for page_index, groups in enumerate(page_groups):
        for block_index, group in enumerate(groups):
            source = group['text']
            source_key = _normalize_memory_text(source)
            matched_issues = [
                issue for issue in unresolved
                if (issue_source := _normalize_memory_text(issue.get('source_excerpt', '')))
                and (source_key.startswith(issue_source) or issue_source.startswith(source_key))
            ]
            if matched_issues:
                recovery_targets[(page_index, block_index)] = _is_literary_heading(source)

    if not recovery_targets:
        return translated_pages

    targets = list(recovery_targets.items())
    planned_targets = targets[:_FINAL_LITERARY_RECOVERY_MAX_ITEMS]
    deferred_count = len(targets) - len(planned_targets)
    with team_context._lock:
        team_context.metrics['final_literary_recovery_planned'] += len(planned_targets)
        team_context.metrics['final_literary_recovery_deferred'] += deferred_count

    logger.warning(
        '[LITERARY QA] Running bounded final recovery for %s of %s unresolved segment(s).',
        len(planned_targets), len(targets),
    )
    for position, ((page_index, block_index), is_heading) in enumerate(
            planned_targets, start=1):
        if cancel_callback and cancel_callback():
            raise TranslationCancelled('Translation cancelled by user.')
        source = page_groups[page_index][block_index]['text']
        draft = translated_pages[page_index][block_index]
        if progress_reporter:
            progress_reporter(
                page_index + 1,
                f'Recovering literary review {position} of {len(planned_targets)} '
                f'on page {page_index + 1}...',
            )
        before = team_context.metrics['final_recovery_reviewer_blocks']
        reviewed = _professional_revision_pass(
            [source],
            {0: draft},
            source_language,
            target_language,
            api_key,
            _book_page_context(page_index, page_groups, book_bible),
            team_context,
            stage='final_recovery_reviewer',
            essential=True,
            strict=True,
            candidate_validator=(
                (lambda candidate, source=source: _validate_literary_heading_semantics(
                    source, candidate, source_language, target_language
                )) if is_heading else None
            ),
            max_tokens=384,
            context_char_limit=_FINAL_LITERARY_RECOVERY_CONTEXT_CHARS,
        )
        recovered = team_context.metrics['final_recovery_reviewer_blocks'] > before
        if recovered:
            translated_pages[page_index][block_index] = reviewed[0]
            if cache is not None:
                cache_key = f'book-page-{page_index + 1}:{block_index}:{source}'
                cache[cache_key] = reviewed[0]
                _save_translation_checkpoint(
                    checkpoint_path,
                    cache,
                    checkpoint_metadata,
                )
            source_key = _normalize_memory_text(source)
            with team_context._lock:
                for issue in team_context.issues:
                    issue_source = _normalize_memory_text(issue.get('source_excerpt', ''))
                    if issue_source and source_key.startswith(issue_source):
                        issue['resolved'] = True
                        issue['resolution'] = 'Repaired by final literary recovery review.'
        else:
            team_context.add_issue(
                category='final_literary_recovery',
                stage='final_recovery_reviewer',
                severity='critical',
                message='Final literary recovery could not obtain a valid reviewed translation.',
                page_number=page_index + 1,
                block_number=block_index + 1,
                source_excerpt=source[:300],
                target_excerpt=draft[:300],
            )
    if deferred_count:
        team_context.add_issue(
            category='final_literary_recovery_deferred',
            stage='final_recovery_reviewer',
            severity='warning',
            message=(
                f'{deferred_count} recorded literary QA item(s) were not automatically '
                're-reviewed because the local review budget was reached.'
            ),
        )
    return translated_pages


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def _split_oversized_word(word: str, font, size: float, width: float) -> list[str]:
    """Split a word only when it cannot fit on an otherwise empty line."""
    chunks = []
    current = ""
    for char in word:
        candidate = current + char
        if current and font.text_length(candidate, fontsize=size) > width:
            chunks.append(current)
            current = char
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _wrap_text(text: str, font, size: float, width: float) -> list[str]:
    """Wrap text using the actual selected PDF font metrics."""
    if not text:
        return []

    lines = []
    for paragraph in re.split(r'\s*\n\s*', text):
        words = paragraph.split()
        if not words:
            continue
        current = ""
        for word in words:
            pieces = _split_oversized_word(word, font, size, width)
            for piece in pieces:
                candidate = f'{current} {piece}'.strip()
                if current and font.text_length(candidate, fontsize=size) > width:
                    lines.append(current)
                    current = piece
                else:
                    current = candidate
        if current:
            lines.append(current)
    return lines


def _fit_text_to_rect(text: str, font, rect, preferred_size: float,
                      minimum_scale: float = 0.55):
    """Return wrapped lines at a readable scale without clipping text."""
    minimum_size = max(3.2, preferred_size * minimum_scale)
    size = max(minimum_size, preferred_size)
    while size >= minimum_size - 0.01:
        lines = _wrap_text(text, font, size, max(1.0, rect.width - 1.0))
        line_height = size * 1.15
        if lines and len(lines) * line_height <= rect.height + (size * 0.25):
            return lines, size, line_height
        size = round(size - 0.5, 2)

    raise TranslationLayoutError(
        'Translated text does not fit its page region without clipping.'
    )


def _rtl_render_rect(page_rect, group: dict, all_groups: list[dict], language_code: str):
    """Use free space below a block for complex-script line metrics without collisions."""
    import fitz

    source_rect = fitz.Rect(group['bbox'])
    desired_bottom = page_rect.y1

    for other in all_groups:
        if other is group:
            continue
        other_rect = fitz.Rect(other['bbox'])
        horizontal_overlap = min(source_rect.x1, other_rect.x1) - max(source_rect.x0, other_rect.x0)
        if horizontal_overlap > 0 and other_rect.y0 >= source_rect.y1 - 0.1:
            desired_bottom = min(desired_bottom, other_rect.y0 - 0.5)

    return fitz.Rect(
        source_rect.x0,
        source_rect.y0,
        source_rect.x1,
        max(source_rect.y1, desired_bottom),
    )


def _ltr_render_rect(page_rect, group: dict, all_groups: list[dict]):
    """Give expanded translations unused vertical space before reducing type size."""
    import fitz

    source_rect = fitz.Rect(group['bbox'])
    available_bottom = page_rect.y1 - 8.0
    for other in all_groups:
        if other is group:
            continue
        other_rect = fitz.Rect(other['bbox'])
        horizontal_overlap = min(source_rect.x1, other_rect.x1) - max(source_rect.x0, other_rect.x0)
        if horizontal_overlap > 0 and other_rect.y0 >= source_rect.y1 - 0.1:
            available_bottom = min(available_bottom, other_rect.y0 - 1.0)
    return fitz.Rect(
        source_rect.x0,
        source_rect.y0,
        source_rect.x1,
        max(source_rect.y1, available_bottom),
    )


def _validate_translated_pdf(pdf_path: str, expected_pages: int):
    """Open the completed artifact and reject structurally broken or empty output."""
    import fitz

    try:
        with fitz.open(pdf_path) as translated_pdf:
            if len(translated_pdf) != expected_pages:
                raise TranslationError(
                    f'Output page count changed from {expected_pages} to {len(translated_pdf)}.'
                )
            extracted = ''.join(page.get_text() for page in translated_pdf)
            if not extracted.strip():
                raise TranslationError('Translated PDF validation found no extractable text.')
            if '\ufffd' in extracted:
                raise TranslationError('Translated PDF contains replacement glyphs.')
            if _PROTECTED_TOKEN_PATTERN.search(extracted) \
                    or _MALFORMED_PROTECTED_TOKEN_PATTERN.search(extracted):
                raise TranslationError(
                    'Translated PDF contains an unresolved internal protected token.'
                )
    except TranslationError:
        raise
    except Exception as exc:
        raise TranslationError(f'Translated PDF validation failed: {exc}') from exc


def _is_non_translatable_value(text: str) -> bool:
    """Keep standalone amounts, codes, URLs, and names in their source cells."""
    value = (text or '').strip()
    if not value:
        return True
    if not any(char.isalpha() for char in value):
        return True
    protected, placeholders = _protect_factual_tokens(value)
    residual = re.sub(r'\[\[KEEP_\d{3}\]\]', '', protected)
    if placeholders and not re.search(r'[A-Za-z\u0600-\u06ff]', residual):
        return True
    if re.fullmatch(r'[A-Z]\.[A-Z]\.?\s+[A-Za-z][A-Za-z .-]+', value):
        return True
    return False


# ---------------------------------------------------------------------------
# Main PDF translation entry point
# ---------------------------------------------------------------------------

def translate_and_render_pdf(source_pdf_path: str, source_language: str,
                              target_language: str, output_path: str,
                              api_key=None, progress_callback=None,
                              cancel_callback=None, checkpoint_path=None,
                              translation_mode: str = 'auto',
                              quality_callback=None,
                              team_context: TranslationTeamContext | None = None
                              ) -> str:
    """Translate a text-based PDF with bounded layout and atomic output."""
    import fitz

    rtl = is_rtl_language(target_language)
    checkpoint_payload = _load_translation_checkpoint_payload(checkpoint_path)
    translation_cache = checkpoint_payload.get('translations', {})
    if not isinstance(translation_cache, dict):
        translation_cache = {}
    checkpoint_metadata = checkpoint_payload.get('metadata', {})
    if not isinstance(checkpoint_metadata, dict):
        checkpoint_metadata = {}
    all_translated_text: list[str] = []
    translated_group_count = 0
    changed_group_count = 0

    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    temp_output_path = os.path.join(
        output_dir,
        f'.{os.path.basename(output_path)}.{uuid.uuid4().hex}.tmp.pdf',
    )

    doc = None
    try:
        doc = fitz.open(source_pdf_path)
        total_pages = len(doc)
        if total_pages == 0:
            raise TranslationError('The source PDF has no pages.')

        def report_progress(current, message):
            if not progress_callback:
                return
            try:
                progress_callback(current, total_pages, message)
            except Exception as exc:
                logger.warning('Progress callback failed: %s', exc)

        report_progress(0, 'Opening PDF and preparing layout analysis...')

        line_page_groups = []
        for page_index, page in enumerate(doc):
            if cancel_callback and cancel_callback():
                raise TranslationCancelled('Translation cancelled by user.')
            report_progress(
                0,
                f'Analyzing document layout page {page_index + 1} of {total_pages}...',
            )
            line_page_groups.append(
                _collect_text_groups_with_ocr(
                    page,
                    source_language,
                    literary=False,
                )
            )

        report_progress(0, 'Choosing translation profile...')
        profile = _detect_document_profile(
            line_page_groups,
            total_pages,
            translation_mode,
            requested_domain=(
                team_context.requested_domain if team_context else 'auto'
            ),
        )
        literary = profile['mode'] == 'literary'
        if team_context:
            team_context.set_detected_domain(profile['domain'])
        if literary:
            page_groups = []
            for page_index, page in enumerate(doc):
                if cancel_callback and cancel_callback():
                    raise TranslationCancelled('Translation cancelled by user.')
                report_progress(
                    0,
                    f'Extracting literary paragraphs page {page_index + 1} of {total_pages}...',
                )
                page_groups.append(
                    _collect_text_groups_with_ocr(
                        page,
                        source_language,
                        literary=True,
                    )
                )
        elif rtl:
            # A visual line is not a stable translation unit: RTL text commonly
            # wraps differently and line-by-line expansion can collide with the
            # next source line. Preserve each PDF text block as one bounded unit.
            page_groups = []
            for page_index, page in enumerate(doc):
                if cancel_callback and cancel_callback():
                    raise TranslationCancelled('Translation cancelled by user.')
                report_progress(
                    0,
                    f'Preparing RTL paragraph layout page {page_index + 1} of {total_pages}...',
                )
                page_groups.append(
                    _collect_text_groups_with_ocr(
                        page,
                        source_language,
                        literary=True,
                    )
                )
        else:
            page_groups = line_page_groups

        if team_context and not literary:
            saved_brief = checkpoint_metadata.get('translation_brief')
            if isinstance(saved_brief, dict) and saved_brief:
                team_context.brief = saved_brief
                team_context.entities.extend(saved_brief.get('entities') or [])
                team_context.glossary.extend([
                    {
                        'source_term': item.get('source', ''),
                        'target_term': item.get('target', ''),
                        'authority': item.get('authority', 'preferred'),
                        'notes': item.get('notes', ''),
                    }
                    for item in saved_brief.get('terms') or []
                ])
            else:
                report_progress(0, 'Building translation brief and terminology plan...')
                brief = _build_translation_brief(
                    page_groups,
                    source_language,
                    target_language,
                    api_key,
                    team_context,
                )
                checkpoint_metadata['translation_brief'] = brief
                checkpoint_metadata['detected_domain'] = team_context.detected_domain
                _save_translation_checkpoint(
                    checkpoint_path,
                    translation_cache,
                    checkpoint_metadata,
                )
        translated_pages: list[list[str]] = [[] for _ in range(total_pages)]
        if literary:
            translated_pages, _book_bible = _translate_literary_pages(
                page_groups,
                source_language,
                target_language,
                api_key,
                translation_cache,
                checkpoint_path,
                checkpoint_metadata,
                report_progress,
                cancel_callback,
                team_context,
            )
            report_progress(
                total_pages,
                'Preparing targeted literary recovery checks...',
            )
            translated_pages = _final_literary_recovery(
                page_groups,
                translated_pages,
                source_language,
                target_language,
                api_key,
                _book_bible,
                team_context,
                cache=translation_cache,
                checkpoint_path=checkpoint_path,
                checkpoint_metadata=checkpoint_metadata,
                progress_reporter=report_progress,
                cancel_callback=cancel_callback,
            )
            report_progress(total_pages, 'Running publication QA and rendering...')

        for page_index, page in enumerate(doc):
            if cancel_callback and cancel_callback():
                raise TranslationCancelled('Translation cancelled by user.')

            groups = page_groups[page_index]
            if not groups:
                report_progress(
                    page_index + 1,
                    f'Page {page_index + 1} of {total_pages} has no embedded text.',
                )
                continue

            if literary:
                translated_texts = translated_pages[page_index]
            else:
                def report_batch_progress(batch_current, batch_total, stage):
                    report_progress(
                        page_index,
                        f'Translating page {page_index + 1} of {total_pages}: '
                        f'{stage} batch {batch_current} of {batch_total}...',
                    )

                translated_texts = _translate_batch(
                    [group['text'] for group in groups],
                    source_language,
                    target_language,
                    api_key,
                    translation_cache,
                    team_context=team_context,
                    progress_callback=report_batch_progress,
                )
                # Checkpoints can restore a page without sending it through a
                # provider. Enforce locked terminology again at the render
                # boundary so restored output cannot bypass publication rules.
                translated_texts = [
                    _enforce_locked_glossary_terms(
                        group['text'],
                        translated,
                        team_context,
                    )
                    for group, translated in zip(groups, translated_texts)
                ]
                for group, translated in zip(groups, translated_texts):
                    translation_cache[group['text']] = translated
                translated_pages[page_index] = translated_texts

            translated_groups = []
            for group, translated in zip(groups, translated_texts):
                if _is_non_translatable_value(group['text']):
                    all_translated_text.append(group['text'])
                    continue

                translated_text = (translated or '').strip()
                if not translated_text:
                    raise TranslationError(
                        f'Translation provider returned empty text on page {page_index + 1}.'
                    )

                source_rect = fitz.Rect(group['bbox'])
                rect = (
                    _rtl_render_rect(page.rect, group, groups, target_language)
                    if rtl else source_rect
                )
                font_name = group['font'] or ''
                is_bold = bool(group['flags'] & (1 << 4)) or 'bold' in font_name.lower()
                is_italic = bool(group['flags'] & (1 << 1)) or any(
                    value in font_name.lower() for value in ('italic', 'oblique')
                )
                render_group = {
                    **group,
                    'translated': translated_text,
                    'is_bold': is_bold,
                    'rect': rect,
                    'source_rect': source_rect,
                }
                if not rtl:
                    font = _get_font(is_bold, is_italic)
                    fit_attempts = [
                        (source_rect, 0.55, 'source region'),
                        (_ltr_render_rect(page.rect, group, groups), 0.55, 'available page space'),
                        (source_rect, 0.32, 'emergency text scale'),
                    ]
                    last_error = None
                    fitted = None
                    for candidate_rect, minimum_scale, strategy in fit_attempts:
                        try:
                            fitted = (*_fit_text_to_rect(
                                translated_text,
                                font,
                                candidate_rect,
                                max(4.0, group['size']),
                                minimum_scale=minimum_scale,
                            ), candidate_rect, strategy)
                            break
                        except TranslationLayoutError as exc:
                            last_error = exc
                    if not fitted:
                        raise TranslationLayoutError(
                            f'Page {page_index + 1}: {last_error}'
                        ) from last_error
                    lines, size, line_height, rect, strategy = fitted
                    render_group['rect'] = rect
                    if strategy != 'source region':
                        logger.warning(
                            '[LAYOUT] Page %s used %s for a longer translation.',
                            page_index + 1,
                            strategy,
                        )
                    render_group.update({
                        'font_object': font,
                        'lines': lines,
                        'size': size,
                        'line_height': line_height,
                    })

                translated_groups.append(render_group)
                all_translated_text.append(translated_text)
                translated_group_count += 1
                if translated_text.casefold() != group['text'].strip().casefold():
                    changed_group_count += 1

            for group in translated_groups:
                page.add_redact_annot(
                    quad=group['source_rect'],
                    fill=None,
                    text='',
                    cross_out=False,
                )
            page.apply_redactions(
                images=(
                    fitz.PDF_REDACT_IMAGE_PIXELS
                    if any(group.get('ocr') for group in translated_groups)
                    else fitz.PDF_REDACT_IMAGE_NONE
                ),
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            )

            if rtl:
                for item in translated_groups:
                    try:
                        _render_rtl_item_with_fallbacks(
                            page,
                            item,
                            target_language,
                            allow_full_page=len(translated_groups) == 1,
                        )
                    except TranslationLayoutError as exc:
                        raise TranslationLayoutError(
                            f'Page {page_index + 1}: {exc}'
                        ) from exc
            else:
                color_buckets: dict[tuple, list] = {}
                for group in translated_groups:
                    color_buckets.setdefault(group['color'], []).append(group)

                for color, items in color_buckets.items():
                    writer = fitz.TextWriter(page.rect)
                    for item in items:
                        baseline_y = item['rect'].y0 + item['size']
                        for line in item['lines']:
                            try:
                                writer.append(
                                    fitz.Point(item['rect'].x0, baseline_y),
                                    line,
                                    font=item['font_object'],
                                    fontsize=item['size'],
                                )
                            except Exception as exc:
                                raise TranslationLayoutError(
                                    f'Could not render translated text on page {page_index + 1}: {exc}'
                                ) from exc
                            baseline_y += item['line_height']
                    try:
                        writer.write_text(page, color=color)
                    except Exception as exc:
                        raise TranslationLayoutError(
                            f'Could not write translated text on page {page_index + 1}: {exc}'
                        ) from exc

            if not literary:
                report_progress(
                    page_index + 1,
                    f'Translated page {page_index + 1} of {total_pages}.',
                )
            else:
                report_progress(
                    page_index + 1,
                    f'Rendered translated page {page_index + 1} of {total_pages}.',
                )
            _save_translation_checkpoint(
                checkpoint_path, translation_cache, checkpoint_metadata
            )

        if translated_group_count == 0:
            raise TranslationError(
                'No translatable text boxes were found. Install Tesseract with the source '
                'language pack for coordinate-aware scanned-PDF translation.'
            )
        if changed_group_count == 0:
            raise TranslationError(
                'Translation provider returned source text for every translatable block.'
            )
        if cancel_callback and cancel_callback():
            raise TranslationCancelled('Translation cancelled by user.')

        quality_report = _automated_quality_report(
            page_groups,
            translated_pages,
            target_language,
            literary,
            team_context,
        )
        checkpoint_metadata['quality_report'] = quality_report
        checkpoint_metadata['translation_mode'] = (
            'literary' if literary else 'document'
        )
        if team_context:
            checkpoint_metadata['detected_domain'] = team_context.detected_domain
        _save_translation_checkpoint(
            checkpoint_path, translation_cache, checkpoint_metadata
        )

        report_progress(total_pages, 'Optimizing and saving document...')
        doc.save(temp_output_path, garbage=4, deflate=True)
        doc.close()
        doc = None
        _validate_translated_pdf(temp_output_path, total_pages)
        os.replace(temp_output_path, output_path)
        if quality_callback:
            quality_callback(quality_report)
        if checkpoint_path and os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)
        return "\n".join(all_translated_text).strip()
    finally:
        if doc is not None:
            doc.close()
        if os.path.exists(temp_output_path):
            try:
                os.remove(temp_output_path)
            except OSError:
                logger.warning('Could not remove temporary translation file %s', temp_output_path)
# ---------------------------------------------------------------------------
# Plain-text translation (non-PDF documents)
# ---------------------------------------------------------------------------

def translate_document_text(source_text: str, source_language: str,
                             target_language: str, api_key=None) -> str:
    source = (source_text or '').strip()
    if not source:
        raise ValueError('Source text is empty.')
    if source_language.lower() == target_language.lower():
        return source

    cache: dict = {}
    paragraphs = re.split(r'\n\s*\n', source)
    translated_parts = _translate_batch(
        [p.strip() for p in paragraphs],
        source_language, target_language, api_key, cache,
    )
    return '\n\n'.join(t for t in translated_parts if t).strip()
