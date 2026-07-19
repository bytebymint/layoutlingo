import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    db.init_app(app)

    # Import models within app context to register tables
    from app.models.document import (
        ChatMessage,
        Document,
        DocumentChunk,
        DocumentComparison,
        DocumentTranslation,
        TranslationEntityMemory,
        TranslationGlossaryTerm,
        TranslationMemoryEntry,
        TranslationReviewIssue,
    )

    # Register Blueprints
    from app.routes.main import main_bp
    from app.routes.api import api_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    
    # Context processor to expose upload folder size or general variables if needed
    @app.context_processor
    def inject_now():
        from datetime import datetime
        return {'now': datetime.utcnow()}
        
    # Ensure database tables exist
    with app.app_context():
        db.create_all()
        
        # Lightweight additive migrations for existing installations.
        existing_columns = {
            column['name']
            for column in db.inspect(db.engine).get_columns('document_translations')
        }
        for col_def in [
            ("current_page", "INTEGER DEFAULT 0"),
            ("total_pages", "INTEGER DEFAULT 0"),
            ("status_message", "VARCHAR(255)"),
            ("checkpoint_path", "VARCHAR(512)"),
            ("attempt_count", "INTEGER DEFAULT 0 NOT NULL"),
            ("lease_owner", "VARCHAR(64)"),
            ("lease_expires_at", "TIMESTAMP"),
            ("last_heartbeat_at", "TIMESTAMP"),
            ("cancel_requested", "BOOLEAN DEFAULT FALSE NOT NULL"),
            ("started_at", "TIMESTAMP"),
            ("translation_mode", "VARCHAR(20) DEFAULT 'auto' NOT NULL"),
            ("provider_mode", "VARCHAR(20) DEFAULT 'online' NOT NULL"),
            ("provider_model", "VARCHAR(255)"),
            ("domain", "VARCHAR(30) DEFAULT 'auto' NOT NULL"),
            ("quality_level", "VARCHAR(20) DEFAULT 'professional' NOT NULL"),
            ("enable_back_translation", "BOOLEAN DEFAULT TRUE NOT NULL"),
            ("knowledge_signature", "VARCHAR(64)"),
            ("detected_mode", "VARCHAR(20)"),
            ("quality_score", "FLOAT"),
            ("quality_report", "TEXT"),
        ]:
            if col_def[0] not in existing_columns:
                db.session.execute(db.text(f"ALTER TABLE document_translations ADD COLUMN {col_def[0]} {col_def[1]}"))
        db.session.commit()
        
    return app
