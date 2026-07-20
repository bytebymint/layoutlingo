"""Download and convert the local NLLB model into the selected D: runtime."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


MODEL_ID = 'facebook/nllb-200-distilled-600M'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=r'C:\LayoutLingo-LocalAI')
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    output = root / 'models' / 'nllb-200-distilled-600m-ct2-int8'
    required = ('model.bin', 'config.json', 'tokenizer.json')
    if all((output / name).is_file() for name in required):
        print(f'NLLB fast translator is already installed at {output}.')
        return

    try:
        from huggingface_hub import snapshot_download
        from ctranslate2.converters import TransformersConverter
    except ImportError as exc:
        raise SystemExit(
            f'Missing required Python package: {exc.name}. Run pip install -r requirements.txt.'
        ) from exc

    cache_dir = root / 'cache' / 'huggingface'
    source = snapshot_download(
        repo_id=MODEL_ID,
        cache_dir=str(cache_dir),
        local_dir=str(root / 'packages' / 'nllb-200-distilled-600m'),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    TransformersConverter(source).convert(str(output), quantization='int8', force=True)
    # CTranslate2 converts weights only; the Hugging Face tokenizer must travel with it.
    for filename in (
        'config.json', 'tokenizer.json', 'tokenizer_config.json',
        'special_tokens_map.json', 'sentencepiece.bpe.model',
    ):
        candidate = Path(source) / filename
        if candidate.is_file():
            shutil.copy2(candidate, output / filename)
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise SystemExit(f'NLLB conversion did not create: {", ".join(missing)}')
    print(f'NLLB fast translator installed at {output}.')


if __name__ == '__main__':
    main()
