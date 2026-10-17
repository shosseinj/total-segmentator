#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
action="${1:-help}"
if [[ "$action" == init ]]; then
  if [[ ! -e .env ]]; then cp .env.example .env; chmod 600 .env; fi
  echo 'Created non-secret .env. Follow README-FA.md for the private connection export and SSH tunnel.'
  exit 0
fi
if [[ "$action" == help ]]; then
  echo 'Usage: bash run.sh init|build|check|start|status|logs|stop'
  exit 0
fi
if [[ ! -f .env || ! -f connection.env ]]; then
  echo 'Missing .env or connection.env. Read README-FA.md; do not source connection.env.' >&2; exit 1
fi
if [[ "$(uname -s)" != Linux ]]; then echo 'Run on the Ubuntu GPU host.' >&2; exit 1; fi
python3 - <<'PY'
import json,os,platform,subprocess
from tools.common import validate_docker_host
info=json.loads(subprocess.check_output(['docker','context','inspect'],text=True))
host=info[0]['Endpoints']['docker']['Host']
server_os=subprocess.check_output(['docker','info','--format','{{.OperatingSystem}}'],text=True).strip()
validate_docker_host(os.environ.get('DOCKER_HOST',''),host,server_os,platform.machine())
version=subprocess.check_output(['docker','compose','version','--short'],text=True).strip().lstrip('v')
parts=version.split('.')
if tuple(map(int,parts[:2]))<(2,30):raise SystemExit('Docker Compose >= 2.30 is required for literal connection.env values.')
PY
compose=(docker compose --env-file .env -f compose.gpu.yaml)
api_port="$(python3 -c 'from tools.common import read_connection; print(read_connection(".env").get("SEG_API_PORT","8096"))')"
case "$action" in
  build) "${compose[@]}" build api ;;
  check)
    "${compose[@]}" run --rm --no-deps check
    "${compose[@]}" run --rm --no-deps check-gpu ;;
  start)
    python3 -m tools.check handoff
    "${compose[@]}" run --rm --no-deps check python -m tools.check port --port "$api_port"
    "${compose[@]}" run --rm --no-deps check
    "${compose[@]}" run --rm --no-deps check-gpu
    "${compose[@]}" up -d migrate api inventory dispatcher gpu-worker
    "${compose[@]}" run --rm --no-deps check python -m tools.check ready --port "$api_port" ;;
  status) "${compose[@]}" ps ;;
  logs) "${compose[@]}" logs --tail 100 api inventory dispatcher gpu-worker ;;
  stop) "${compose[@]}" stop api inventory dispatcher gpu-worker ;;
  *) echo 'Unknown action; run: bash run.sh help' >&2; exit 1 ;;
esac
