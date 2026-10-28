"""Repackage an existing server-generated HTML result without running inference.

python3 -m tools.repackage_viewer exports/dicom-viewer.html --output exports
The input is read as data, never executed or modified.
"""
import argparse
import json
from pathlib import Path

from app.export.viewer_package import build_viewer_package


def repackage(source, output):
    text = Path(source).read_text(encoding='utf-8')
    marker = 'const data='
    if marker not in text:
        raise ValueError('VIEWER_PAYLOAD_NOT_FOUND')
    payload, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])
    if not isinstance(payload.get('frames'), list) or not payload.get('overlays'):
        raise ValueError('INVALID_LEGACY_VIEWER')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    return build_viewer_package(payload, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, default=Path('exports'))
    args = parser.parse_args()
    print(repackage(args.source, args.output).resolve())


if __name__ == '__main__':
    main()
