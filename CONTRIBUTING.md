# Contributing

## Local setup

Create a virtual environment, install `requirements.txt`, copy `.env.example` to `.env`, and run `python run.py`.

## Before opening a pull request

- Keep changes focused and explain the user-visible outcome.
- Do not commit `.env`, uploaded documents, generated PDFs, model files, or database files.
- Run `python -m unittest discover tests` and `pip check`.
- Add a focused test for behavior changes, especially authentication, ownership, translation recovery, and PDF rendering.

## Design principle

Protect the source PDF geometry. Translation improvements must not silently sacrifice layout, checkpoint recovery, or RTL rendering.
