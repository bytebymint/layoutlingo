# LayoutLingo

**Private, layout-preserving PDF translation with local AI.**

LayoutLingo is a local-first Flask application for translating PDFs while retaining the original document geometry. It supports document analysis, persistent glossaries, translation memory, RTL languages, resumable long-document jobs, and a clear quality-review workflow.

## What it does

- Translates PDFs in online, local-quality, and local-fast modes.
- Uses NLLB for a fast offline first pass and Aya Expanse for optional local review.
- Preserves source pages and overlays translated text into the original layout.
- Handles Arabic, Farsi, Hebrew, and Urdu with RTL-aware output.
- Checkpoints long translations and resumes safely after a restart.
- Shows live translation stages, model health, and human-readable quality findings.
- Lets a user approve only the passages that need a decision.
- Analyses uploaded PDFs and images, extracts structured details, and supports document chat.

## Privacy and limits

Each account can access only its own documents, translations, glossary entries, comparisons, and uploaded files. By default the app runs on `127.0.0.1`, not the LAN.

Offline translation stays on the computer after the local models are installed. Online translation and selected document-analysis features send text to the provider configured in `.env`.

Automated quality checks reduce common failures; they are not a substitute for a qualified human reviewer for legal, medical, financial, or publication-critical work.

## Quick start (Windows)

```powershell
git clone https://github.com/bytebymint/layoutlingo.git
cd layoutlingo
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe run.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000), create an account, and upload a document. The upload limit is 500 MB by default.

## Local AI on D:

The installer keeps the llama.cpp runtime, Aya reviewer, NLLB fast translator, model caches, logs, and temporary files under `D:\DocIntel-LocalAI` by default.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\local_ai\install-local-ai.ps1
```

The NLLB download and conversion can take time and several GB of D: space. To install only Aya first, add `-SkipFastTranslator`.

```powershell
# Start or verify the local Aya reviewer
powershell -ExecutionPolicy Bypass -File D:\DocIntel-LocalAI\start-local-ai.ps1
powershell -ExecutionPolicy Bypass -File D:\DocIntel-LocalAI\verify-local-ai.ps1

# Stop it when you are finished
powershell -ExecutionPolicy Bypass -File D:\DocIntel-LocalAI\stop-local-ai.ps1
```

Use **Enable local AI** in the Quality Dashboard after installation. `Offline Fast NLLB` needs the NLLB model; `Offline Quality NLLB + Aya` needs both NLLB and the Aya server.

## Configuration

Copy `.env.example` to `.env`. Never commit `.env`.

- `SECRET_KEY`: required when `APP_ENV=production`.
- `HOST`: `127.0.0.1` by default. Set `0.0.0.0` only behind authentication, HTTPS, and a trusted network boundary.
- `MAX_CONTENT_LENGTH`: `524288000` for a 500 MB upload limit.
- `FREEMODEL_API_KEY`: optional provider key for online translation and configured analysis features.
- `LOCAL_LLM_ROOT`: local runtime root, default `D:\DocIntel-LocalAI`.

## Long documents

For books, use an external worker and a production database. Translation jobs have database leases and page-level checkpoints, so an interrupted job can resume.

```powershell
$env:APP_ENV='production'
$env:TRANSLATION_WORKER_MODE='external'
$env:DATABASE_URL='postgresql+psycopg://user:password@host/layoutlingo'
$env:SECRET_KEY='<long-random-value>'
waitress-serve --listen=127.0.0.1:5000 run:app
python translation_worker.py
```

Run more workers only after measuring the available CPU/GPU memory and translation quality. Parallel workers improve throughput, but they do not make a single local model generate faster.

## Development

```powershell
python -m unittest discover tests
pip check
```

## Security

Read [SECURITY.md](SECURITY.md) before exposing LayoutLingo beyond localhost. The app is designed for local use by default. For a shared deployment, use TLS, PostgreSQL, a production session secret, backups, process isolation, and a real reverse proxy.

## License

LayoutLingo is available under the [MIT License](LICENSE). Local model licenses are separate: review the Aya and NLLB model terms before use, especially for commercial work.

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and the scope expected for changes.
