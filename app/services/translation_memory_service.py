import hashlib
import json
import re
from datetime import datetime

from app import db
from app.models.document import (
    TranslationEntityMemory,
    TranslationGlossaryTerm,
    TranslationMemoryEntry,
    TranslationReviewIssue,
)


GLOSSARY_AUTHORITIES = {'locked', 'preferred', 'allowed', 'forbidden'}


def normalize_segment(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip().casefold()


def segment_hash(text: str) -> str:
    return hashlib.sha256(normalize_segment(text).encode('utf-8')).hexdigest()


def save_glossary_entries(user_id, source_language: str, target_language: str,
                          domain: str, entries: list[dict]) -> int:
    saved = 0
    selected_domain = domain if domain and domain != 'auto' else 'general'
    for raw in entries or []:
        source_term = str(raw.get('source_term') or '').strip()
        target_term = str(raw.get('target_term') or '').strip()
        authority = str(raw.get('authority') or 'locked').strip().lower()
        if not source_term or not target_term or authority not in GLOSSARY_AUTHORITIES:
            continue
        existing = TranslationGlossaryTerm.query.filter_by(
            user_id=user_id,
            source_language=source_language,
            target_language=target_language,
            domain=selected_domain,
            source_term=source_term,
            target_term=target_term,
        ).first()
        if existing:
            existing.authority = authority
            existing.notes = str(raw.get('notes') or '').strip() or None
            existing.case_sensitive = bool(raw.get('case_sensitive'))
            existing.active = True
        else:
            db.session.add(TranslationGlossaryTerm(
                user_id=user_id,
                source_language=source_language,
                target_language=target_language,
                domain=selected_domain,
                source_term=source_term,
                target_term=target_term,
                authority=authority,
                notes=str(raw.get('notes') or '').strip() or None,
                case_sensitive=bool(raw.get('case_sensitive')),
            ))
        saved += 1
    if saved:
        db.session.commit()
    return saved


def load_translation_resources(user_id, source_language: str,
                               target_language: str, domain: str) -> dict:
    selected_domains = None if domain == 'auto' else {'general', domain or 'general'}

    glossary_query = TranslationGlossaryTerm.query.filter(
        TranslationGlossaryTerm.user_id == user_id,
        TranslationGlossaryTerm.source_language == source_language,
        TranslationGlossaryTerm.target_language == target_language,
        TranslationGlossaryTerm.active.is_(True),
    )
    entity_query = TranslationEntityMemory.query.filter(
        TranslationEntityMemory.user_id == user_id,
        TranslationEntityMemory.source_language == source_language,
        TranslationEntityMemory.target_language == target_language,
        TranslationEntityMemory.active.is_(True),
    )
    memory_query = TranslationMemoryEntry.query.filter(
        TranslationMemoryEntry.user_id == user_id,
        TranslationMemoryEntry.source_language == source_language,
        TranslationMemoryEntry.target_language == target_language,
        TranslationMemoryEntry.approved.is_(True),
    )
    if selected_domains:
        glossary_query = glossary_query.filter(
            TranslationGlossaryTerm.domain.in_(selected_domains)
        )
        entity_query = entity_query.filter(
            TranslationEntityMemory.domain.in_(selected_domains)
        )
        memory_query = memory_query.filter(
            TranslationMemoryEntry.domain.in_(selected_domains)
        )

    glossary = glossary_query.order_by(
        TranslationGlossaryTerm.authority,
        TranslationGlossaryTerm.id,
    ).all()
    entities = entity_query.order_by(TranslationEntityMemory.id).all()
    memory = memory_query.order_by(
        TranslationMemoryEntry.quality_score.desc(),
        TranslationMemoryEntry.updated_at.desc(),
    ).limit(5000).all()

    return {
        'glossary': [item.to_dict() for item in glossary],
        'entities': [{
            'domain': item.domain,
            'source_entity': item.source_entity,
            'target_entity': item.target_entity,
            'entity_type': item.entity_type,
            'notes': item.notes or '',
        } for item in entities],
        'memory': [{
            'domain': item.domain,
            'source_hash': item.source_hash,
            'source_text': item.source_text,
            'target_text': item.target_text,
            'quality_score': item.quality_score,
        } for item in memory],
    }


def knowledge_signature(resources: dict) -> str:
    stable = json.dumps(resources or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(stable.encode('utf-8')).hexdigest()


def persist_translation_memory(user_id, source_language: str,
                               target_language: str, domain: str,
                               segments: list[dict], quality_score: float | None):
    selected_domain = domain if domain and domain != 'auto' else 'general'
    now = datetime.utcnow()
    for item in segments or []:
        source_text = str(item.get('source_text') or '').strip()
        target_text = str(item.get('target_text') or '').strip()
        if not source_text or not target_text or source_text.casefold() == target_text.casefold():
            continue
        source_digest = segment_hash(source_text)
        existing = TranslationMemoryEntry.query.filter_by(
            user_id=user_id,
            source_language=source_language,
            target_language=target_language,
            domain=selected_domain,
            source_hash=source_digest,
        ).first()
        if existing:
            if quality_score is None or existing.quality_score is None \
                    or quality_score >= existing.quality_score:
                existing.source_text = source_text
                existing.target_text = target_text
                existing.quality_score = quality_score
                existing.approved = True
            existing.usage_count = (existing.usage_count or 0) + 1
            existing.last_used_at = now
        else:
            db.session.add(TranslationMemoryEntry(
                user_id=user_id,
                source_language=source_language,
                target_language=target_language,
                domain=selected_domain,
                source_hash=source_digest,
                source_text=source_text,
                target_text=target_text,
                approved=True,
                quality_score=quality_score,
                usage_count=1,
                last_used_at=now,
            ))
    db.session.commit()


def persist_discovered_entities(user_id, source_language: str,
                                target_language: str, domain: str,
                                entities: list[dict]):
    selected_domain = domain if domain and domain != 'auto' else 'general'
    for item in entities or []:
        source_entity = str(item.get('source') or item.get('source_entity') or '').strip()
        target_entity = str(item.get('target') or item.get('target_entity') or '').strip()
        if not source_entity or not target_entity:
            continue
        existing = TranslationEntityMemory.query.filter_by(
            user_id=user_id,
            source_language=source_language,
            target_language=target_language,
            domain=selected_domain,
            source_entity=source_entity,
        ).first()
        if existing:
            existing.target_entity = target_entity
            existing.entity_type = str(item.get('type') or item.get('entity_type') or 'other')[:50]
            existing.notes = str(item.get('notes') or '').strip() or None
            existing.active = True
        else:
            db.session.add(TranslationEntityMemory(
                user_id=user_id,
                source_language=source_language,
                target_language=target_language,
                domain=selected_domain,
                source_entity=source_entity,
                target_entity=target_entity,
                entity_type=str(item.get('type') or item.get('entity_type') or 'other')[:50],
                notes=str(item.get('notes') or '').strip() or None,
            ))
    db.session.commit()


def replace_review_issues(translation_id: int, issues: list[dict]):
    TranslationReviewIssue.query.filter_by(translation_id=translation_id).delete()
    for item in issues or []:
        db.session.add(TranslationReviewIssue(
            translation_id=translation_id,
            category=str(item.get('category') or 'quality')[:50],
            severity=str(item.get('severity') or 'warning')[:20],
            message=str(item.get('message') or 'Translation quality issue.'),
            page_number=item.get('page_number'),
            block_number=item.get('block_number'),
            source_excerpt=str(item.get('source_excerpt') or '')[:1000] or None,
            target_excerpt=str(item.get('target_excerpt') or '')[:1000] or None,
            status=str(item.get('status') or 'open')[:20],
        ))
    db.session.commit()
