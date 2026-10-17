#!/usr/bin/env bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ $# -ne 2 ]]; then
  echo 'Run ON THE CLINIC HOST: bash clinic-control.sh /absolute/path/to/SharifClinic export|activate|rollback' >&2
  exit 1
fi
clinic_root="$1"
export PYTHONPATH="$clinic_root/services/dicom-segmentation:$bundle_dir"
exec "$clinic_root/services/dicom-segmentation/.venv/bin/python" -m tools.clinic_control "$2" --root "$clinic_root"
