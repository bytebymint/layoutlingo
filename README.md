# LayoutLingo

[![Tests](https://github.com/bytebymint/layoutlingo/actions/workflows/tests.yml/badge.svg)](https://github.com/bytebymint/layoutlingo/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/bytebymint/layoutlingo?include_prereleases&label=release)](https://github.com/bytebymint/layoutlingo/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.13-blue.svg)](https://www.python.org/)

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

## Quick start (Linux and macOS)

Install Python 3.11 or newer, Git, and the system packages required by your PDF/OCR dependencies. Then create an isolated environment and start LayoutLingo:

```bash
git clone https://github.com/bytebymint/layoutlingo.git
cd layoutlingo
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
python run.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000), create an account, and upload a document. To stop the application, press `Ctrl+C`. On macOS, Homebrew users can install Python and Git with `brew install python git`; on Debian or Ubuntu, install them with `sudo apt install python3 python3-venv python3-pip git`.

The application itself runs on Linux and macOS. The bundled one-click local-AI installer currently targets Windows because it downloads and launches the Windows `llama.cpp` server executable. On Linux and macOS, use online mode with a configured provider, or install a compatible local `llama.cpp` runtime and models manually, then point `LOCAL_LLM_ROOT` and `FAST_TRANSLATION_MODEL_PATH` in `.env` at those locations. Verify the runtime command and model format for your operating system before selecting offline mode.

## Screenshots

The screenshots below show the complete translation workflow, from choosing a document and local models to reviewing quality findings and exporting the finished PDF.

### 1. Configure the translation

Choose the source and target languages, the document profile, the subject domain, and the translation engine. In this example, the fast NLLB model drafts the translation and the local Aya model reviews it for literary quality.

![Translation settings](docs/screenshots/translation-settings.png)

### 2. Watch the local translation team work

The progress view reports the current stage, page, batch, elapsed time, and whether the translation worker is active. This makes long-document processing understandable instead of showing an unexplained percentage.

![Translation progress](docs/screenshots/translation-progress.png)

### 3. Review only what needs attention

If automated checks identify passages that require human judgment, the job pauses at a checkpoint. The rest of the document remains safely preserved while the reviewer sees exactly how many decisions are needed.

![Quality review needed](docs/screenshots/quality-review-needed.png)

The review cards show the original passage beside the proposed local-AI translation. A reviewer can correct the specific passage instead of reworking the entire document.

![Failed passage review](docs/screenshots/failed-passage-review.png)

If a final recovery attempt needs a decision, LayoutLingo presents it as a separate, focused review item with an explicit approval action.

![Final recovery review](docs/screenshots/final-recovery-review.png)

### 4. Export with a quality report

After the review workflow is complete, the job reaches 100%, reports the quality score, shows the quality dossier, and enables the translated PDF download.

![Translation completed](docs/screenshots/translation-completed.png)

### Before and after: local-AI PDF translation

This comparison shows the same story page before and after translation. The English source keeps its original literary layout, while the Arabic output is rendered with right-to-left text and preserved page geometry. The translation is produced locally by the configured NLLB plus Aya workflow; the models are installed and run on the user’s computer rather than sent to a third-party translation service.

| Original English page | Local-AI Arabic result |
| --- | --- |
| ![Original English PDF page](docs/screenshots/original-english-output.png) | ![Translated Arabic PDF page](docs/screenshots/translated-arabic-output.png) |

The before/after pair is illustrative: automated QA reduces common errors, but publication-critical translations should still receive qualified human review.

## Local AI setup

The first time you choose offline translation, the Quality Dashboard opens a guided setup screen. It checks available storage, lets you choose a local folder, shows the download estimate, asks you to accept the model terms, and installs the llama.cpp runtime, Aya reviewer, NLLB fast translator, caches, logs, and temporary files.

The default location is `C:\LayoutLingo-LocalAI`. You can change it in the setup screen or set `LOCAL_LLM_ROOT` in `.env` before starting the application. The installer also accepts an explicit `-Root` path:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\local_ai\install-local-ai.ps1 -Root "C:\LayoutLingo-LocalAI"
```

Use **Enable local AI** in the Quality Dashboard after installation. `Offline Fast NLLB` needs the NLLB model; `Offline Quality NLLB + Aya` needs both NLLB and the Aya server. To move the installation later, stop local AI, copy or reinstall the runtime into another folder, update `LOCAL_LLM_ROOT`, update `FAST_TRANSLATION_MODEL_PATH` if needed, and restart LayoutLingo.

The local models have separate licenses. Review the [NLLB model terms](https://huggingface.co/facebook/nllb-200-distilled-600M) and [Aya model terms](https://huggingface.co/CohereForAI/aya-expanse-8b), especially before commercial use.

## Configuration

Copy `.env.example` to `.env`. Never commit `.env`.

- `SECRET_KEY`: required when `APP_ENV=production`.
- `HOST`: `127.0.0.1` by default. Set `0.0.0.0` only behind authentication, HTTPS, and a trusted network boundary.
- `MAX_CONTENT_LENGTH`: `524288000` for a 500 MB upload limit.
- **FreeModel API key (recommended for maximum quality):** sign up at https://freemodel.dev/invite/FRE-f4f1f25c, copy your API key, and add `FREEMODEL_API_KEY=...` to `.env`. Without it, online translation and AI-assisted analysis may be unavailable.
- `LOCAL_LLM_ROOT`: local runtime root, default `C:\LayoutLingo-LocalAI`.

## Sample PDFs

The `examples/` folder contains small documents that are safe to use for smoke testing:

- `layoutlingo-sample-invoice-3-pages.pdf`: a short business invoice and service summary.
- `layoutlingo-sample-proposal-5-pages.pdf`: a compact website proposal with structured business language.
- `layoutlingo-sample-ebook-20-pages.pdf`: a longer story-style ebook sample for translation workflow testing.

Use the 3-page file first to confirm uploads and basic translation. Use the 20-page story when testing checkpointing, review flow, and long-document progress behavior.

## Known limits

- Local translation speed depends heavily on CPU, GPU, RAM, and model size.
- Layout preservation is strongest on text-based PDFs with normal page structure. Scanned PDFs, complex tables, layered graphics, and dense columns may need review.
- Automatic QA catches common issues, but legal, medical, financial, and publication-critical documents still need a human reviewer.
- Very long ebooks should be translated with worker mode enabled and enough free disk space for checkpoints, logs, model caches, and generated PDFs.
- Online quality mode requires a provider API key and may send selected text to the configured provider.

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
