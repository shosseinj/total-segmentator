"""Run a pinned model in an interruptible subprocess; never silently fall back to CPU."""
from contextlib import contextmanager
import hashlib,json,os,signal,subprocess,sys,tempfile,time
from pathlib import Path
import nibabel as nib
from app.storage.minio_store import file_sha

def verify_model_cache(cache,expected_digest):
    cache=Path(cache);manifest=cache/'checkpoint-manifest.json'
    if not manifest.exists():raise ValueError('CHECKPOINT_UNAVAILABLE')
    payload=manifest.read_bytes()
    if hashlib.sha256(payload).hexdigest()!=expected_digest:raise ValueError('CHECKPOINT_UNAVAILABLE')
    entries=json.loads(payload)
    if not entries:raise ValueError('CHECKPOINT_UNAVAILABLE')
    for name,entry in entries.items():
        p=(cache/name).resolve()
        if not p.is_relative_to(cache.resolve()) or not p.is_file() or file_sha(p)!=entry:raise ValueError('CHECKPOINT_UNAVAILABLE')
    return entries

def terminate(process):
    if process.poll() is not None:return
    os.killpg(process.pid,signal.SIGTERM)
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=10)

@contextmanager
def model_temporary_directory(workdir):
    # Python multiprocessing creates Unix sockets inside TMPDIR (108-byte path
    # limit on Linux). A short private alias keeps socket names short while all
    # actual temporary files remain under the request's cleanup directory.
    temporary=(Path(workdir)/'tmp').resolve()
    temporary.mkdir(exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='sg-',dir='/tmp') as directory:
        alias=Path(directory)/'t'
        alias.symlink_to(temporary,target_is_directory=True)
        yield str(alias)

def run_segmentation(volume,config,workdir,lease_guard=None,gpu_lock_fd=None):
    workdir=Path(workdir);workdir.mkdir(parents=True,exist_ok=True)
    verify_model_cache(config['model_cache'],config['checkpoint_digest'])
    input_path=workdir/'native.nii.gz';nib.save(nib.Nifti1Image(volume.array,volume.affine_ras),input_path)
    output=workdir/('model-labels.nii.gz' if config.get('multilabel_output') else 'model-masks')
    request={**config,'input':str(input_path),'output':str(output)}
    config_path=workdir/'model-config.json';config_path.write_text(json.dumps(request));config_path.chmod(0o600)
    state=workdir/'model-state';state.mkdir(exist_ok=True)
    with model_temporary_directory(workdir) as temporary:
        environment=dict(os.environ,TMPDIR=temporary,TMP=temporary,TEMP=temporary,
                         TOTALSEG_HOME_DIR=str(state),TOTALSEG_WEIGHTS_PATH=str(Path(config['model_cache'])/'nnunet/results'),TOTALSEG_DISABLE_USAGE_STATS='1')
        child=subprocess.Popen([sys.executable,'-m','app.inference.model_process',str(config_path)],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,pass_fds=() if gpu_lock_fd is None else (gpu_lock_fd,))
        started=time.monotonic();heartbeat_at=0
        try:
            while child.poll() is None:
                now=time.monotonic()
                if now-started>config.get('timeout_seconds',3600):raise ValueError('MODEL_TIMEOUT')
                if now-heartbeat_at>=30:
                    if lease_guard is None or not lease_guard():raise ValueError('LEASE_LOST')
                    heartbeat_at=now
                time.sleep(.5)
            if child.returncode:
                error_path=workdir/'model-error.json'
                code=json.loads(error_path.read_text()).get('code') if error_path.exists() else 'MODEL_FAILED'
                raise ValueError(code)
        finally:terminate(child)
    if config.get('multilabel_output'):return nib.load(output)
    return {label:nib.load(workdir/'model-masks'/f'{label}.nii.gz') for label in config['structures']}
