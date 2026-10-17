#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
# Edit these defaults when moving the clinic to another machine.
# SSH uses ~/.ssh/id_ed25519; install its public key with ssh-copy-id once.
export CLINIC_SSH_TARGET="${CLINIC_SSH_TARGET:-newuser@192.168.100.20}"
export CLINIC_ROOT="${CLINIC_ROOT:-/home/newuser/Videos/SharifClinic}"
seg_python="${SEG_PYTHON:-$HOME/Music/whisper/.venv/bin/python}"
if [[ ! -x "$seg_python" ]]; then
  echo 'Python not found. Set SEG_PYTHON to your virtualenv/bin/python.' >&2
  exit 1
fi
exec "$seg_python" -m tools.native "$@"
