import hmac
import os
import secrets

from flask import Flask, abort, request, session
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()
login_manager = LoginManager()

def create_app(config_class='config.Config'):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if app.config.get('IS_PRODUCTION') and not app.config.get('SECRET_KEY'):
        raise RuntimeError('SECRET_KEY must be configured when APP_ENV=production.')
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'

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
    from app.models.user import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # Register Blueprints
    from app.routes.main import main_bp
    from app.routes.api import api_bp
    from app.routes.auth import auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(auth_bp)

    @app.before_request
    def protect_state_changing_requests():
        """Reject cross-site writes while allowing the test suite to stay lightweight."""
        if app.testing or request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return None
        expected = session.get('_csrf_token')
        provided = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if not expected or not provided or not hmac.compare_digest(expected, provided):
            abort(400, description='Invalid or missing CSRF token.')

    # Context processor to expose upload folder size or general variables if needed
    @app.context_processor
    def inject_now():
        from datetime import datetime
        token = session.get('_csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['_csrf_token'] = token
        return {'now': datetime.utcnow(), 'csrf_token': token}
        
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
