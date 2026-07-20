from datetime import datetime
import json
from app import db

class Document(db.Model):
    __tablename__ = 'documents'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(512), nullable=False)
    file_type = db.Column(db.String(10), nullable=False) # 'pdf', 'png', 'jpg', 'jpeg'
    status = db.Column(db.String(20), default='Pending') # 'Pending', 'Processing', 'Completed', 'Failed'
    ocr_text = db.Column(db.Text, nullable=True)
    doc_type = db.Column(db.String(50), nullable=True) # 'Invoice', 'Contract', 'Certificate', 'Identification document', 'Other'
    extracted_data = db.Column(db.Text, nullable=True) # JSON string representation
    confidence_score = db.Column(db.Float, default=0.0)
    storage_size = db.Column(db.Integer, default=0) # in bytes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    chunks = db.relationship('DocumentChunk', backref='document', lazy='dynamic', cascade='all, delete-orphan')
    chat_messages = db.relationship('ChatMessage', backref='document', lazy='dynamic', cascade='all, delete-orphan')
    translation_jobs = db.relationship('DocumentTranslation', backref='document', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def parsed_extracted_data(self):
        if self.extracted_data:
            try:
                return json.loads(self.extracted_data)
            except json.JSONDecodeError:
                return {}
        return {}

    @parsed_extracted_data.setter
    def parsed_extracted_data(self, value):
        self.extracted_data = json.dumps(value)

    def to_dict(self):
        return {
            'id': self.id,
            'filename': self.filename,
            'original_filename': self.original_filename,
            'file_type': self.file_type,
            'status': self.status,
            'doc_type': self.doc_type,
            'extracted_data': self.parsed_extracted_data,
            'confidence_score': self.confidence_score,
            'storage_size': self.storage_size,
            'created_at': self.created_at.isoformat()
        }

    def __repr__(self):
        return f'<Document {self.original_filename} ({self.status})>'


class DocumentChunk(db.Model):
    __tablename__ = 'document_chunks'
    
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False)
    chunk_index = db.Column(db.Integer, nullable=False)
    text_content = db.Column(db.Text, nullable=False)
    # Embedding stored as a JSON-serialized list of floats
    embedding = db.Column(db.Text, nullable=True)

    @property
    def parsed_embedding(self):
        if self.embedding:
            try:
                return json.loads(self.embedding)
            except json.JSONDecodeError:
                return []
        return []

    @parsed_embedding.setter
    def parsed_embedding(self, value):
        self.embedding = json.dumps(value)

    def __repr__(self):
        return f'<DocumentChunk {self.document_id} - Chunk {self.chunk_index}>'


class ChatMessage(db.Model):
    __tablename__ = 'chat_messages'
    
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    sender = db.Column(db.String(10), nullable=False) # 'user' or 'ai'
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'sender': self.sender,
            'message': self.message,
            'created_at': self.created_at.isoformat()
        }

    def __repr__(self):
        return f'<ChatMessage {self.sender}: {self.message[:20]}...>'


class DocumentComparison(db.Model):
    __tablename__ = 'document_comparisons'
    
    id = db.Column(db.Integer, primary_key=True)
    document_one_id = db.Column(db.Integer, db.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False)
    document_two_id = db.Column(db.Integer, db.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False)
    result_json = db.Column(db.Text, nullable=False) # JSON representation of comparison report
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    document_one = db.relationship('Document', foreign_keys=[document_one_id], backref=db.backref('comparisons_as_one', cascade='all, delete-orphan'))
    document_two = db.relationship('Document', foreign_keys=[document_two_id], backref=db.backref('comparisons_as_two', cascade='all, delete-orphan'))

    @property
    def parsed_result(self):
        if self.result_json:
            try:
                return json.loads(self.result_json)
            except json.JSONDecodeError:
                return {}
        return {}

    @parsed_result.setter
    def parsed_result(self, value):
        self.result_json = json.dumps(value)

    def to_dict(self):
        return {
            'id': self.id,
            'document_one_id': self.document_one_id,
            'document_two_id': self.document_two_id,
            'result': self.parsed_result,
            'created_at': self.created_at.isoformat()
        }

    def __repr__(self):
        return f'<DocumentComparison {self.document_one_id} vs {self.document_two_id}>'



class DocumentTranslation(db.Model):
    __tablename__ = 'document_translations'

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False)
    source_language = db.Column(db.String(50), nullable=False)
    target_language = db.Column(db.String(50), nullable=False)
    translation_mode = db.Column(db.String(20), default='auto', nullable=False)
    provider_mode = db.Column(db.String(20), default='online', nullable=False)
    provider_model = db.Column(db.String(255), nullable=True)
    domain = db.Column(db.String(30), default='auto', nullable=False)
    quality_level = db.Column(db.String(20), default='professional', nullable=False)
    enable_back_translation = db.Column(db.Boolean, default=True, nullable=False)
    knowledge_signature = db.Column(db.String(64), nullable=True)
    detected_mode = db.Column(db.String(20), nullable=True)
    status = db.Column(db.String(20), default='Pending')
    translated_text = db.Column(db.Text, nullable=True)
    translated_pdf_filename = db.Column(db.String(255), nullable=True)
    translated_pdf_path = db.Column(db.String(512), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    current_page = db.Column(db.Integer, default=0, nullable=True)
    total_pages = db.Column(db.Integer, default=0, nullable=True)
    status_message = db.Column(db.String(255), nullable=True)
    checkpoint_path = db.Column(db.String(512), nullable=True)
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    lease_owner = db.Column(db.String(64), nullable=True)
    lease_expires_at = db.Column(db.DateTime, nullable=True)
    last_heartbeat_at = db.Column(db.DateTime, nullable=True)
    cancel_requested = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    quality_score = db.Column(db.Float, nullable=True)
    quality_report = db.Column(db.Text, nullable=True)

    review_issues = db.relationship(
        'TranslationReviewIssue',
        backref='translation_job',
        lazy='dynamic',
        cascade='all, delete-orphan',
    )

    @property
    def parsed_quality_report(self):
        if not self.quality_report:
            return {}
        try:
            value = json.loads(self.quality_report)
            return value if isinstance(value, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @parsed_quality_report.setter
    def parsed_quality_report(self, value):
        self.quality_report = json.dumps(value, ensure_ascii=False)

    def to_dict(self):
        return {
            'id': self.id,
            'document_id': self.document_id,
            'source_language': self.source_language,
            'target_language': self.target_language,
            'translation_mode': self.translation_mode or 'auto',
            'provider_mode': self.provider_mode or 'online',
            'provider_model': self.provider_model,
            'domain': self.domain or 'auto',
            'quality_level': self.quality_level or 'professional',
            'enable_back_translation': bool(self.enable_back_translation),
            'knowledge_signature': self.knowledge_signature,
            'detected_mode': self.detected_mode,
            'status': self.status,
            'translated_pdf_filename': self.translated_pdf_filename,
            'current_page': self.current_page or 0,
            'total_pages': self.total_pages or 0,
            'status_message': self.status_message or '',
            'attempt_count': self.attempt_count or 0,
            'cancel_requested': bool(self.cancel_requested),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'error_message': self.error_message,
            'quality_score': self.quality_score,
            'quality_report': self.parsed_quality_report,
        }

    def __repr__(self):
        return f'<DocumentTranslation {self.document_id} {self.source_language}->{self.target_language} ({self.status})>'


class TranslationGlossaryTerm(db.Model):
    __tablename__ = 'translation_glossary_terms'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    source_language = db.Column(db.String(50), nullable=False, index=True)
    target_language = db.Column(db.String(50), nullable=False, index=True)
    domain = db.Column(db.String(30), default='general', nullable=False, index=True)
    source_term = db.Column(db.String(500), nullable=False)
    target_term = db.Column(db.String(500), nullable=False)
    authority = db.Column(db.String(20), default='preferred', nullable=False)
    notes = db.Column(db.Text, nullable=True)
    case_sensitive = db.Column(db.Boolean, default=False, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'source_language',
            'target_language',
            'domain',
            'source_term',
            'target_term',
            name='uq_translation_glossary_term',
        ),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'source_language': self.source_language,
            'target_language': self.target_language,
            'domain': self.domain,
            'source_term': self.source_term,
            'target_term': self.target_term,
            'authority': self.authority,
            'notes': self.notes or '',
            'case_sensitive': bool(self.case_sensitive),
            'active': bool(self.active),
        }


class TranslationMemoryEntry(db.Model):
    __tablename__ = 'translation_memory_entries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    source_language = db.Column(db.String(50), nullable=False, index=True)
    target_language = db.Column(db.String(50), nullable=False, index=True)
    domain = db.Column(db.String(30), default='general', nullable=False, index=True)
    source_hash = db.Column(db.String(64), nullable=False, index=True)
    source_text = db.Column(db.Text, nullable=False)
    target_text = db.Column(db.Text, nullable=False)
    approved = db.Column(db.Boolean, default=True, nullable=False)
    quality_score = db.Column(db.Float, nullable=True)
    usage_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    last_used_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'source_language',
            'target_language',
            'domain',
            'source_hash',
            name='uq_translation_memory_source',
        ),
    )


class TranslationEntityMemory(db.Model):
    __tablename__ = 'translation_entity_memory'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    source_language = db.Column(db.String(50), nullable=False, index=True)
    target_language = db.Column(db.String(50), nullable=False, index=True)
    domain = db.Column(db.String(30), default='general', nullable=False, index=True)
    source_entity = db.Column(db.String(500), nullable=False)
    target_entity = db.Column(db.String(500), nullable=False)
    entity_type = db.Column(db.String(50), default='other', nullable=False)
    notes = db.Column(db.Text, nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        db.UniqueConstraint(
            'user_id',
            'source_language',
            'target_language',
            'domain',
            'source_entity',
            name='uq_translation_entity',
        ),
    )


class TranslationReviewIssue(db.Model):
    __tablename__ = 'translation_review_issues'

    id = db.Column(db.Integer, primary_key=True)
    translation_id = db.Column(
        db.Integer,
        db.ForeignKey('document_translations.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    category = db.Column(db.String(50), nullable=False)
    severity = db.Column(db.String(20), default='warning', nullable=False)
    message = db.Column(db.Text, nullable=False)
    page_number = db.Column(db.Integer, nullable=True)
    block_number = db.Column(db.Integer, nullable=True)
    source_excerpt = db.Column(db.Text, nullable=True)
    target_excerpt = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='open', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'category': self.category,
            'severity': self.severity,
            'message': self.message,
            'page_number': self.page_number,
            'block_number': self.block_number,
            'source_excerpt': self.source_excerpt or '',
            'target_excerpt': self.target_excerpt or '',
            'status': self.status,
        }
