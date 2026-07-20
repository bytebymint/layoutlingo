import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Keep model caches and temporary files off the system drive. These variables
# are set before any translation libraries are imported so tempfile,
# Hugging Face, and Torch all use the portable D: installation by default.
LOCAL_RUNTIME_ROOT = os.path.abspath(
    os.environ.get('LOCAL_LLM_ROOT', r'D:\DocIntel-LocalAI')
)
LOCAL_RUNTIME_TEMP = os.path.abspath(
    os.environ.get('LOCAL_AI_TEMP_DIR', os.path.join(LOCAL_RUNTIME_ROOT, 'tmp'))
)
_PORTABLE_RUNTIME_PATHS = {
    'TEMP': LOCAL_RUNTIME_TEMP,
    'TMP': LOCAL_RUNTIME_TEMP,
    'HF_HOME': os.path.join(LOCAL_RUNTIME_ROOT, 'cache', 'huggingface'),
    'HF_HUB_CACHE': os.path.join(LOCAL_RUNTIME_ROOT, 'cache', 'huggingface', 'hub'),
    'TRANSFORMERS_CACHE': os.path.join(LOCAL_RUNTIME_ROOT, 'cache', 'transformers'),
    'TORCH_HOME': os.path.join(LOCAL_RUNTIME_ROOT, 'cache', 'torch'),
}
for _name, _path in _PORTABLE_RUNTIME_PATHS.items():
    os.makedirs(_path, exist_ok=True)
    os.environ[_name] = _path

class Config:
    APP_ENV = os.environ.get('APP_ENV', 'development').lower()
    IS_PRODUCTION = APP_ENV == 'production'

    # Flask app configuration
    SECRET_KEY = os.environ.get('SECRET_KEY') or (
        'development-only-layoutlingo-key' if not IS_PRODUCTION else ''
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    SESSION_COOKIE_NAME = 'layoutlingo_session'
    
    # Database config
    # Ensure database folder exists
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///database/app.db')
    # If using relative sqlite path, make it absolute from base dir to avoid issues with subfolders
    if SQLALCHEMY_DATABASE_URI.startswith('sqlite:///'):
        db_path = SQLALCHEMY_DATABASE_URI.replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            # Resolve relative path from base dir
            db_abs_path = os.path.abspath(os.path.join(BASE_DIR, db_path))
            # Ensure the database directory exists
            os.makedirs(os.path.dirname(db_abs_path), exist_ok=True)
            SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_abs_path}'
            
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {'pool_pre_ping': True, 'pool_recycle': 300}
    
    # Storage settings
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
    if not os.path.isabs(UPLOAD_FOLDER):
        UPLOAD_FOLDER = os.path.abspath(os.path.join(BASE_DIR, UPLOAD_FOLDER))
    # Ensure upload directory exists
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 500 * 1024 * 1024)) # 500MB
    ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
    
    # AI Engine settings
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
    FREEMODEL_API_KEY = os.environ.get('FREEMODEL_API_KEY', '')
    FREEMODEL_API_KEY_2 = os.environ.get('FREEMODEL_API_KEY_2', '')
    FREEMODEL_API_KEY_3 = os.environ.get('FREEMODEL_API_KEY_3', '')
    FREEMODEL_MODEL = os.environ.get('FREEMODEL_MODEL', 'openai-t0')
    FREEMODEL_API_KEYS = tuple(dict.fromkeys(filter(None, (
        FREEMODEL_API_KEY.strip(),
        FREEMODEL_API_KEY_2.strip(),
        FREEMODEL_API_KEY_3.strip(),
    ))))

    # Durable translation workers
    TRANSLATION_WORKER_MODE = os.environ.get('TRANSLATION_WORKER_MODE', 'inline').lower()
    TRANSLATION_LEASE_SECONDS = int(os.environ.get('TRANSLATION_LEASE_SECONDS', '300'))
    TRANSLATION_POLL_SECONDS = float(os.environ.get('TRANSLATION_POLL_SECONDS', '2'))
    TRANSLATION_PAGE_WORKERS = int(os.environ.get('TRANSLATION_PAGE_WORKERS', '4'))
    TRANSLATION_LITERARY_MIN_PAGES = int(
        os.environ.get('TRANSLATION_LITERARY_MIN_PAGES', '80')
    )
    TRANSLATION_QUALITY_GATE_SCORE = float(
        os.environ.get('TRANSLATION_QUALITY_GATE_SCORE', '90')
    )
    TRANSLATION_REVIEWER_MODEL = os.environ.get('TRANSLATION_REVIEWER_MODEL', '')
    GEMINI_VISION_MODEL = os.environ.get('GEMINI_VISION_MODEL', 'gemini-1.5-flash')
    DOCUMENT_PROCESSING_TIMEOUT_SECONDS = int(
        os.environ.get('DOCUMENT_PROCESSING_TIMEOUT_SECONDS', '600')
    )

    # Portable local translation engine. The default installation is entirely on D:.
    LOCAL_LLM_ROOT = LOCAL_RUNTIME_ROOT
    LOCAL_AI_TEMP_DIR = LOCAL_RUNTIME_TEMP
    LOCAL_LLM_ENDPOINT = os.environ.get(
        'LOCAL_LLM_ENDPOINT',
        'http://127.0.0.1:8080/v1/chat/completions',
    )
    LOCAL_LLM_MODEL = os.environ.get('LOCAL_LLM_MODEL', 'aya-expanse-8b-local')
    LOCAL_LLM_API_KEY = os.environ.get('LOCAL_LLM_API_KEY', 'local-private-key')
    LOCAL_LLM_TIMEOUT_SECONDS = int(
        os.environ.get('LOCAL_LLM_TIMEOUT_SECONDS', '900')
    )
    FAST_TRANSLATION_MODEL_PATH = os.environ.get(
        'FAST_TRANSLATION_MODEL_PATH',
        r'D:\DocIntel-LocalAI\models\nllb-200-distilled-600m-ct2-int8',
    )
    FAST_TRANSLATION_DEVICE = os.environ.get('FAST_TRANSLATION_DEVICE', 'cpu')
    FAST_TRANSLATION_COMPUTE_TYPE = os.environ.get('FAST_TRANSLATION_COMPUTE_TYPE', 'int8')
    FAST_TRANSLATION_CPU_THREADS = int(os.environ.get('FAST_TRANSLATION_CPU_THREADS', '4'))
    FAST_TRANSLATION_BEAM_SIZE = int(os.environ.get('FAST_TRANSLATION_BEAM_SIZE', '2'))
    FAST_QUALITY_DOCUMENT_REVIEW_BUDGET = int(
        os.environ.get('FAST_QUALITY_DOCUMENT_REVIEW_BUDGET', '2')
    )
    FAST_QUALITY_LITERARY_REVIEW_BUDGET = int(
        os.environ.get('FAST_QUALITY_LITERARY_REVIEW_BUDGET', '8')
    )

    if IS_PRODUCTION:
        if SECRET_KEY == 'dev-secret-key-ai-doc-intel-12984712':
            raise RuntimeError('SECRET_KEY must be configured in production.')
        if SQLALCHEMY_DATABASE_URI.startswith('sqlite:'):
            raise RuntimeError('Production requires PostgreSQL; SQLite is not supported.')
        if not FREEMODEL_API_KEYS:
            raise RuntimeError('At least one FREEMODEL_API_KEY must be configured in production.')
        if TRANSLATION_WORKER_MODE != 'external':
            raise RuntimeError('Production requires TRANSLATION_WORKER_MODE=external.')
        if TRANSLATION_PAGE_WORKERS < 1:
            raise RuntimeError('TRANSLATION_PAGE_WORKERS must be at least 1.')
