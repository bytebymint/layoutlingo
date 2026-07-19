# AI Document Intelligence Platform

A premium, startup-quality AI Document Intelligence SaaS platform built with **Python Flask** and **Google Gemini AI**. The platform allows users to securely ingest PDF and image documents, automatically perform OCR, classify document types, extract structured metadata, and interact with files in real-time through a Retrieval-Augmented Generation (RAG) conversational chat interface.

---

## 🚀 Key Features

* **Glassmorphic Responsive UI**: Sleek dark/light theme options with rich animations, custom layouts, hover effects, and CSS transitions.
* **Drag-and-Drop Ingestion**: Interactive drag-and-drop file upload with animated AJAX progress bars (handles PDFs, PNGs, and JPGs up to 500MB).
* **Asynchronous AI Processing**: Processes files in a background worker thread, ensuring the dashboard remains highly responsive while updating status in real-time via status polling.
* **Hybrid OCR System**: Extracts text directly from digital PDFs using `pypdf`, runs high-accuracy Gemini Vision OCR for image documents, and falls back to a clean mock content emulator when running entirely offline.
* **Smart Document Classifier**: Categorizes files as an *Invoice*, *Contract*, *Certificate*, *Identification Document*, or *Other* using AI or weighted keyword heuristic trees.
* **Structured Data Extractor**: Pulls key data (companies, dates, monetary totals, ID numbers) into beautiful card lists and valid JSON payloads.
* **Context-Aware Document Chat**: ChatGPT-style client featuring RAG. Chunks text, stores queryable Term-Frequency (TF) or Gemini embeddings, performs cosine-similarity matching, and answers questions using *only* the document's context.
* **Secure Storage & Sessions**: Implements secure UUID file saving to avoid collisions, private user accounts via Flask-Login, and owner-only resource isolation.

---

## 📐 System Architecture

The following flowchart shows how components interact when a user uploads a document:

```mermaid
graph TD
    User([User Browser]) -->|1. Drag & Drop File| WebRoute[Flask API Upload Route]
    WebRoute -->|2. Save Secure UUID File| Storage[(uploads/ File System)]
    WebRoute -->|3. Create Document 'Pending'| Database[(SQLite Database)]
    WebRoute -->|4. Launch Processing Thread| Worker[Async Background worker]
    
    subgraph AI Service Layer
        Worker -->|5. Run OCR| OCR[ocr_service.py]
        OCR -->|If PDF| PyPDF[pypdf text extraction]
        OCR -->|If Image / Scanned| GeminiVision[Gemini Vision SDK]
        Worker -->|6. Classify Doc Type| Classifier[document_classifier.py]
        Worker -->|7. Extract Metadata JSON| Extractor[information_extractor.py]
        Worker -->|8. Generate Embeddings| Embedder[embedding_service.py]
    end
    
    Worker -->|9. Save Extracted Text & JSON| Database
    Worker -->|10. Store Text Chunks & Embeddings| Database
    Worker -->|11. Set Status 'Completed'| Database
    
    User -->|12. Poll status every 2s| WebRoute
    WebRoute -->|13. Read status| Database
    Database -->|14. Return 'Completed'| User
```

---

## 🛠️ Technology Stack

* **Backend Framework**: Python Flask, Blueprints architecture, and Jinja2 templates.
* **Authentication**: Flask-Login session management with password hashing via `Werkzeug`.
* **Database & ORM**: SQLite with SQLAlchemy ORM (User, Document, DocumentChunk, ChatMessage models).
* **AI & LLM Services**: Google Gemini API (`gemini-1.5-flash` model for vision OCR, categorization, extraction, and RAG chat; `text-embedding-004` for vector space indexation).
* **Text Processing**: `pypdf` for digital PDF text extraction.
* **Frontend**: HTML5, Vanilla CSS3 (Custom SaaS styling), JavaScript (AJAX upload, dynamic UI theme, chat message formatting), and Bootstrap 5 layout grids.

---

## ⚙️ Installation & Setup

### Prerequisites
* Python 3.9, 3.10, 3.11, 3.12, or 3.13.

### 1. Clone the repository and navigate to the directory
```bash
git clone https://github.com/your-username/AI-Document-Intelligence.git
cd AI-Document-Intelligence
```

### 2. Create and activate a virtual environment (optional but recommended)
```bash
python -m venv venv
# On Windows
venv\Scripts\activate
# On macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
Copy the `.env.example` file to `.env`:
```bash
copy .env.example .env
```
Open `.env` and configure your settings:
* Adjust your `SECRET_KEY` if needed.
* Configure `FREEMODEL_API_KEY` for translation and text AI. For ordered
  quota/rate-limit failover, also configure `FREEMODEL_API_KEY_2` and
  `FREEMODEL_API_KEY_3` with distinct keys that have available quota.
* `GEMINI_API_KEY` is optional and is only used by features that explicitly
  call Gemini-based OCR/vision.

### 5. Run the application
```bash
python run.py
```
Open your browser and navigate to `http://127.0.0.1:5000`.

On Windows, `run.py` automatically relaunches itself with the project `.venv`
when needed. This ensures Offline Fast translation can load NLLB and CTranslate2.

### 6. Run automated tests
To execute the unit test suite and verify everything is working:
```bash
python -m unittest discover tests
```

---

## Long-document translation workers

Development defaults to an in-process translation worker. Pending and expired
jobs are re-leased automatically after an application restart.

For production, run the web application and translation worker as separate
processes:

```bash
set APP_ENV=production
set TRANSLATION_WORKER_MODE=external
set DATABASE_URL=postgresql+psycopg://user:password@database/document_intelligence
set SECRET_KEY=replace-with-a-random-secret
set FREEMODEL_API_KEY=replace-with-provider-key
set FREEMODEL_API_KEY_2=replace-with-first-backup-key
set FREEMODEL_API_KEY_3=replace-with-second-backup-key

waitress-serve --listen=0.0.0.0:5000 run:app
python translation_worker.py
```

Multiple `translation_worker.py` processes may be run for capacity. Database
leases prevent duplicate processing. Workers update their lease after every
page, resume provider translations from an atomic checkpoint, and publish the
PDF only after structural validation succeeds. HTTP 401, 402, 403, 429,
network, and provider-server failures move to the next configured key. A
failed job with a checkpoint resumes under the same job ID when submitted
again.

### Professional translation-team mode

Professional quality is the default. Each job is routed through an auditable
translation team:

1. An intake analyst detects the document domain, purpose, audience, register,
   risk flags, recurring entities, and terminology.
2. A terminology manager combines the document brief with persistent glossary
   entries. Terms can be locked, preferred, allowed, or forbidden.
3. Approved translation memory is applied exactly and included as team knowledge
   for future documents in the same language pair and domain.
4. A draft translator protects numbers, dates, currencies, URLs, identifiers,
   and other business facts with exact round-trip placeholders.
5. A target-language editor improves grammar, fluency, register, and domain style.
6. An independent bilingual reviewer compares source and target for omissions,
   additions, ambiguity, polarity, scope, tone, entities, and terminology.
7. Legal, financial, and medical jobs optionally run literal back-translation
   followed by a separate reconciliation pass.
8. Deterministic fact, script, glossary, entity, repeated-segment, font, layout,
   page-count, and PDF-integrity checks feed a fail-closed publication gate.
9. Only a translation that passes the gate is published and learned by the
   persistent translation memory.

Literary documents additionally use paragraph extraction, neighboring-page
context, chapter detection, and a story bible for character names, places,
voice, tone, and style. Translation and editing remain concurrent and resumable.

The quality dossier records every team stage, memory hit, terminology/entity
check, back-translation count, and actionable reviewer issue. This substantially
reduces risk, but an automated score is not a guarantee that a probabilistic
language model made no subjective or domain-expert error. Human sign-off remains
recommended for consequential legal and medical publication.

Recommended production settings for 100-200 page books:

```bash
set TRANSLATION_WORKER_MODE=external
set TRANSLATION_PAGE_WORKERS=4
set TRANSLATION_API_RETRIES=3
set FREEMODEL_TIMEOUT_SECONDS=180
python translation_worker.py
```

Increase `TRANSLATION_PAGE_WORKERS` only when the provider account supports the
resulting request rate. Use a strong translation model through
`TRANSLATION_DRAFT_MODEL` and a strong instruction-following model through
`TRANSLATION_EDITOR_MODEL`. Use `TRANSLATION_REVIEWER_MODEL` for an independent
review model where available; blank overrides use `FREEMODEL_MODEL`. Set
`TRANSLATION_QUALITY_GATE_SCORE` to the minimum publishable automated score.

RTL output uses bundled Noto fonts and MuPDF complex-script layout for Arabic,
Farsi, Hebrew, and Urdu. Logical Unicode is passed directly to the layout
engine, preserving connected glyphs and mixed-direction numbers and names.
If translated RTL text is longer than the source region, rendering now retries
with expanded page-safe regions and an emergency scale before failing. Tune
`RTL_MIN_FONT_SCALE`, `RTL_EMERGENCY_MIN_FONT_SCALE`, and
`RTL_PAGE_MARGIN_POINTS` for unusually dense PDFs.

Scanned or image-only PDFs use PyMuPDF's Tesseract-backed coordinate OCR when
Tesseract and the source-language data pack are installed. Gemini vision OCR is
also available for ingestion when `GEMINI_API_KEY` is configured, but
layout-preserving PDF translation still requires coordinate boxes. The system
fails clearly instead of publishing simulated OCR as a translation.

## 🗺️ Future Roadmap

- [ ] **Vector Database Migration**: Transition from direct database/in-memory cosine similarity to FAISS or ChromaDB for large-scale enterprise performance.
- [ ] **Multi-Page Image OCR**: Implement file splitting for multi-page TIFFs or image batches.
- [ ] **Interactive Bounding Boxes**: Draw highlight overlays on the document preview matching the sentences retrieved in the chat responses.
- [ ] **Data Export Integration**: Add buttons to export extracted metadata schemas directly to CSV or JSON files.
- [ ] **PostgreSQL Database Support**: Transition the SQLAlchemy database URI to PostgreSQL for production hosting compatibility.

## Offline translation on D:

The Offline engine is installed under `D:\DocIntel-LocalAI` and exposes an
OpenAI-compatible API on `127.0.0.1:8080`. Start it before selecting Offline in
the translation screen:

```powershell
powershell -ExecutionPolicy Bypass -File D:\DocIntel-LocalAI\start-local-ai.ps1
powershell -ExecutionPolicy Bypass -File D:\DocIntel-LocalAI\verify-local-ai.ps1
```

Stop it with `D:\DocIntel-LocalAI\stop-local-ai.ps1`. All model files, runtime
binaries, logs, temporary files, Hugging Face caches, Transformer caches, and
GPU caches controlled by the launcher are on D:. The server binds only to
localhost and the application deliberately has no Online fallback when an
Offline job fails. Online and Offline results use separate job cache identities.
Windows itself may still write small amounts of system data to C:, which an
application cannot guarantee against.

Offline mode applies to the translation pipeline only. Document upload
classification and structured metadata extraction still use the configured
FreeModel ingestion provider; OCR and term-frequency embeddings run locally.

The active Aya Expanse model is licensed under CC-BY-NC-4.0 and the Cohere Labs
Acceptable Use Policy. It is suitable for this academic/non-commercial setup;
replace it with a commercially licensed local model before commercial use.
