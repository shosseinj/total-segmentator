"""Native GPU runtime using the existing Python environment and remote services."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

from .common import atomic_write, read_connection

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'sharif-segmentation-gpu'
TARGET = PREFIX + '.target'
ROLES = ('api', 'inventory', 'dispatcher', 'gpu-worker', 'local-worker')


def options():
    return read_connection(ROOT / '.env') if (ROOT / '.env').exists() else {}


def port():
    value = options().get('SEG_API_PORT', '8096')
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError('INVALID_API_PORT')
    return value


def environment():
    path = ROOT / 'connection.env'
    if not path.is_file():
        raise ValueError('CONNECTION_MISSING: copy the clinic gpu-connection.env here as connection.env')
    path.chmod(0o600)
    env = dict(os.environ)
    # Parse literal values; never source credentials through a shell.
    env.update(read_connection(path))
    env.update(
        SEG_SCRATCH=str(ROOT / 'work'), SEG_MODEL_CACHE=str(ROOT / 'models'),
        SEG_MR_ENABLED='false', TOTALSEG_DISABLE_USAGE_STATS='1',
        CUDA_VISIBLE_DEVICES=options().get('SEG_GPU_DEVICE', '0'),
        PATH=str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', ''),
    )
    return env


def run(*args, env=None):
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def systemctl(*args):
    run('systemctl', '--user', *args)


def runtime_active():
    result = subprocess.run(
        ['systemctl', '--user', 'is-active', *map(unit, ROLES)],
        cwd=ROOT, capture_output=True, text=True,
    )
    # is-active returns success when ANY requested unit is active.
    return result.stdout.splitlines() == ['active'] * len(ROLES)


def clinic():
    values = options()
    target = values.get('CLINIC_SSH_TARGET', os.environ.get('CLINIC_SSH_TARGET', ''))
    root = values.get('CLINIC_ROOT', os.environ.get('CLINIC_ROOT', ''))
    if not re.fullmatch(r'[A-Za-z0-9._-]+@[A-Za-z0-9._-]+', target):
        raise ValueError('Set CLINIC_SSH_TARGET=user@IP in run-native.sh or .env')
    if not re.fullmatch(r'/[A-Za-z0-9/._-]+', root):
        raise ValueError('Set CLINIC_ROOT to the absolute clinic project path in run-native.sh or .env')
    return target, root.rstrip('/')


def fetch(handoff=False):
    target, root = clinic()
    source = 'gpu-handoff.json' if handoff else 'gpu-connection.env'
    destination = ROOT / ('handoff.json' if handoff else 'connection.env')
    # Download privately before replacing a working configuration.
    with tempfile.TemporaryDirectory(prefix='.clinic-', dir=ROOT) as directory:
        path = Path(directory) / source
        run('scp', f'{target}:{root}/local-backend/segmentation/{source}', str(path))
        if handoff:
            import json
            from .common import verify_handoff
            verify_handoff(json.loads(path.read_text()), read_connection(ROOT / 'connection.env'))
        else:
            from app.config import Settings
            Settings.from_env(read_connection(path))
        atomic_write(destination, path.read_text())
    print('SAVED: ' + destination.name)


def unit(role):
    return f'{PREFIX}-{role}.service'


def systemd_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def install_units():
    directory = Path.home() / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    for role in ROLES:
        content = f'''[Unit]
Description=Sharif segmentation {role}
PartOf={TARGET}
After=network.target

[Service]
Type=simple
WorkingDirectory={str(ROOT).replace('%', '%%')}
ExecStart={systemd_quote(sys.executable)} -m tools.native serve {role}
Restart=on-failure
RestartSec=5
UMask=0077
KillMode={'control-group' if role=='local-worker' else 'mixed'}
TimeoutStopSec={30 if role=='local-worker' else 3700}
'''
        atomic_write(directory / unit(role), content)
    atomic_write(directory / TARGET, '[Unit]\nDescription=Sharif segmentation GPU runtime\nWants=' + ' '.join(map(unit, ROLES)) + '\n')
    systemctl('daemon-reload')


def start_tunnel():
    target, _ = clinic()
    directory = Path.home() / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write(directory / unit('tunnel'), f'''[Unit]
Description=Sharif segmentation private SSH tunnel
After=network.target

[Service]
Type=simple
WorkingDirectory={str(ROOT).replace('%', '%%')}
ExecStart={systemd_quote(sys.executable)} -m tools.connect {systemd_quote(target)}
Restart=always
RestartSec=5
UMask=0077
''')
    systemctl('daemon-reload')
    systemctl('start', unit('tunnel'))
    for _ in range(40):
        try:
            for forwarded in (15432, 16379, 19000):
                with socket.create_connection(('127.0.0.1', forwarded), timeout=0.2):
                    pass
            print('SSH_TUNNEL_READY', flush=True)
            return
        except OSError:
            time.sleep(0.25)
    raise ValueError('SSH_TUNNEL_NOT_READY: inspect run-native.sh logs')


def local_check():
    os.environ['CUDA_VISIBLE_DEVICES'] = options().get('SEG_GPU_DEVICE', '0')
    os.environ['TOTALSEG_DISABLE_USAGE_STATS'] = '1'
    from app.inference.totalseg_adapter import verify_model_cache
    from tools.check import check_gpu
    from types import SimpleNamespace
    cache = ROOT / 'models'
    digest = hashlib.sha256((cache / 'checkpoint-manifest.json').read_bytes()).hexdigest()
    entries = verify_model_cache(cache, digest)
    print('CHECKPOINTS_VERIFIED_FILES=' + str(len(entries)), flush=True)
    check_gpu(SimpleNamespace(model_release='2.11.0'))
    # Import the actual inference and DICOM export paths, not just package metadata.
    from totalsegmentator.python_api import totalsegmentator
    from app.export.dicom_seg import export_seg
    print('MODEL_AND_DICOM_IMPORTS_OK', flush=True)


def check(env):
    for mode in ('connections', 'gpu'):
        run(sys.executable, '-m', 'tools.check', mode, env=env)


def serve(role):
    env = environment()
    commands = {
        'api': ['uvicorn', 'app.main:from_env', '--factory', '--host', options().get('SEG_API_HOST','127.0.0.1'), '--port', port(), '--no-access-log'],
        'inventory': ['celery', '-A', 'app.worker.celery_app', 'worker', '-Q', 'inventory', '--concurrency=1', '--loglevel=WARNING', '--hostname=inventory@%h', '--without-gossip', '--without-mingle'],
        'dispatcher': ['app.worker.dispatcher'],
        'local-worker': ['app.worker.local_worker'],
        'gpu-worker': ['celery', '-A', 'app.worker.celery_app', 'worker', '-Q', 'gpu', '--concurrency=1', '--loglevel=WARNING', '--hostname=gpu@%h', '--without-gossip', '--without-mingle'],
    }
    os.execve(sys.executable, [sys.executable, '-m', *commands[role]], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'fetch-connection', 'fetch-handoff', 'connect', 'tunnel-start', 'tunnel-stop', 'check-local', 'check', 'start', 'start-local', 'status', 'logs', 'stop', 'serve'])
    parser.add_argument('role', nargs='?', choices=ROLES)
    args = parser.parse_args()
    os.chdir(ROOT)
    if args.action == 'init':
        if not (ROOT / '.env').exists():
            atomic_write(ROOT / '.env', (ROOT / '.env.example').read_text())
        (ROOT / 'work').mkdir(mode=0o700, exist_ok=True)
        print('NATIVE_INITIALIZED: ' + sys.executable)
    elif args.action in ('fetch-connection', 'fetch-handoff'):
        fetch(handoff=args.action == 'fetch-handoff')
    elif args.action == 'connect':
        from .connect import command
        target, _ = clinic()
        run(*command(target, read_connection(ROOT / 'connection.env'), options()))
    elif args.action == 'tunnel-start':
        environment()  # Refuse to start a restart loop without connection settings.
        start_tunnel()
    elif args.action == 'tunnel-stop':
        systemctl('stop', unit('tunnel'))
    elif args.action == 'check-local':
        local_check()
    elif args.action == 'check':
        check(environment())
    elif args.action == 'start-local':
        env=environment()
        run(sys.executable,'-m','tools.check','gpu',env=env)
        install_units()
        systemctl('start',unit('api'),unit('local-worker'))
        print('LOCAL_API_AND_GPU_WORKER_STARTED')
    elif args.action == 'start':
        env = environment()
        from tools.check import handoff
        handoff()
        start_tunnel()
        if runtime_active():
            run(sys.executable, '-m', 'tools.check', 'ready', '--port', port(), env=env)
            print('ALREADY_RUNNING', flush=True)
            return
        # Check every dependency before migrations or queue consumers start.
        run(sys.executable, '-m', 'tools.check', 'port', '--port', port(), env=env)
        check(env)
        run(sys.executable, '-m', 'alembic', 'upgrade', 'head', env=env)
        install_units()
        systemctl('start', TARGET)
        run(sys.executable, '-m', 'tools.check', 'ready', '--port', port(), env=env)
    elif args.action == 'stop':
        systemctl('stop', TARGET, *map(unit, ROLES))
        if (Path.home() / '.config/systemd/user' / unit('tunnel')).is_file():
            systemctl('stop', unit('tunnel'))
    elif args.action == 'status':
        systemctl('--no-pager', 'is-active', *map(unit, ROLES))
        import httpx
        response = httpx.get(f'http://127.0.0.1:{port()}/health/ready', timeout=5, trust_env=False)
        print('API_HEALTH', response.status_code, response.text)
    elif args.action == 'logs':
        run('journalctl', '--user', '--no-pager', '-n', '100', *[arg for role in (*ROLES, 'tunnel') for arg in ('-u', unit(role))])
    elif args.action == 'serve':
        if args.role is None:
            parser.error('serve requires a role')
        serve(args.role)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, FileNotFoundError) as exc:
        # Only launcher errors reach this boundary; connection checker sanitizes upstream errors.
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
