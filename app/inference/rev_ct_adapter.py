"""Run pinned, local RevCT weights in a cancellable GPU subprocess."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from app.inference.ct_clip_adapter import ABNORMALITIES
from app.inference.totalseg_adapter import model_temporary_directory,terminate

MODEL_REPO='hcltech-robotics/rev-ct'
MODEL_REVISION='706dbf5f1f9fee7f332955d77cab15bef24409ea'
CODE_REVISION='116686e77ea857dfc4d93fe366e7da04b4214b92'
MODEL_VERSION='0.3.2'
ERRORS={'REV_CT_CHECKPOINT_UNAVAILABLE','REV_CT_CHECKPOINT_MISMATCH','REV_CT_MODEL_UNAVAILABLE',
        'REV_CT_MODEL_FAILED','REV_CT_INVALID_OUTPUT','REV_CT_INVALID_HEATMAP','REV_CT_MODEL_VERSION_MISMATCH','GPU_UNAVAILABLE','GPU_OOM'}


def classification_config(settings):
    cache=Path(settings.rev_ct_model_cache)
    if not re.fullmatch('[a-f0-9]{64}',settings.rev_ct_manifest_digest) or not all(
        (cache/name).is_file() for name in ('checkpoint-manifest.json','config.json','processor_config.json','model.safetensors')):
        raise ValueError('REV_CT_CHECKPOINT_UNAVAILABLE')
    python=Path(settings.rev_ct_python or sys.executable)
    if not python.is_file() or not os.access(python,os.X_OK):raise ValueError('REV_CT_MODEL_UNAVAILABLE')
    return {'backend':'rev_ct','model_cache':str(cache.resolve()),'manifest_digest':settings.rev_ct_manifest_digest,
            'python':str(python.absolute()),'timeout_seconds':settings.timeout_seconds,'include_heatmaps':True}


def classification_result(payload,config):
    try:
        scores=payload['scores'];decisions=payload['decisions'];logits=payload['decision_logits'];raw=payload['raw_scores']
        if tuple(payload['labels'])!=ABNORMALITIES or any(len(rows)!=18 for rows in (scores,decisions,logits,raw)):
            raise ValueError
        if any(type(value) is not bool for value in decisions):raise ValueError
        if any(not math.isfinite(value) or not 0<=value<=1 for value in [*scores,*raw]):raise ValueError
        if any(not math.isfinite(value) for value in logits):raise ValueError
        if decisions!=[value>=0 for value in logits]:raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('REV_CT_INVALID_OUTPUT') from None
    return {'schema_version':1,'model':'RevCT','model_version':MODEL_VERSION,'model_repo':MODEL_REPO,
            'model_revision':MODEL_REVISION,'code_revision':CODE_REVISION,
            'checkpoint_manifest_sha256':config['manifest_digest'],
            'scope':'chest CT','review_status':'unreviewed','threshold':None,
            'flag_rule':'adjusted_logit >= 0; model-trained per-finding decision biases',
            'score_type':'model-calibrated score; flags use adjusted logits, not a 0.5 score cutoff',
            'abnormalities':[{'name':name,'score':float(score),'raw_score':float(raw_score),
                              'decision_logit':float(logit),'flagged':decision}
                             for name,score,raw_score,logit,decision in zip(ABNORMALITIES,scores,raw,logits,decisions)]}


def run_classification(native_path,config,workdir,lease_guard=None,gpu_lock_fd=None):
    workdir=Path(workdir).resolve();workdir.mkdir(parents=True,exist_ok=True,mode=0o700)
    config_path=workdir/'config.json'
    config_path.write_text(json.dumps({**config,'input':str(Path(native_path).resolve()),
        'heatmap_output':str(workdir/'heatmaps.npz')}));config_path.chmod(0o600)
    if lease_guard is None or not lease_guard():raise ValueError('LEASE_LOST')
    with model_temporary_directory(workdir) as temporary:
        environment=dict(os.environ,TMPDIR=temporary,TMP=temporary,TEMP=temporary,
                         HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        child=subprocess.Popen([config['python'],'-m','app.inference.rev_ct_process',str(config_path)],
            env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,
            pass_fds=() if gpu_lock_fd is None else (gpu_lock_fd,))
        started=time.monotonic()
        try:
            while child.poll() is None:
                if time.monotonic()-started>config.get('timeout_seconds',3600):raise ValueError('REV_CT_TIMEOUT')
                if not lease_guard():raise ValueError('LEASE_LOST')
                time.sleep(.5)
            if child.returncode:
                path=workdir/'error.json'
                code=json.loads(path.read_text()).get('code') if path.is_file() else None
                raise ValueError(code if code in ERRORS else 'REV_CT_MODEL_FAILED')
        finally:terminate(child)
    if config.get('include_heatmaps',True) and not (workdir/'heatmaps.npz').is_file():raise ValueError('REV_CT_INVALID_HEATMAP')
    try:
        return classification_result(json.loads((workdir/'predictions.json').read_text()),config)
    except (OSError,json.JSONDecodeError):raise ValueError('REV_CT_INVALID_OUTPUT') from None
