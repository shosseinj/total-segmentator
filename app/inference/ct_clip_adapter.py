"""Optional, offline CT-CLIP ClassFine inference under the shared GPU lock."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from app.inference.totalseg_adapter import model_temporary_directory,terminate

# Checkpoint output order from the official scripts/ct_lipro_inference.py.
ABNORMALITIES = (
    'Medical material', 'Arterial wall calcification', 'Cardiomegaly',
    'Pericardial effusion', 'Coronary artery wall calcification', 'Hiatal hernia',
    'Lymphadenopathy', 'Emphysema', 'Atelectasis', 'Lung nodule', 'Lung opacity',
    'Pulmonary fibrotic sequela', 'Pleural effusion', 'Mosaic attenuation pattern',
    'Peribronchial thickening', 'Consolidation', 'Bronchiectasis',
    'Interlobular septal thickening',
)
UPSTREAM_REVISION = 'a2a155c601987820433c01db69b64d701d3d229d'
ERRORS = {'CT_CLIP_CHECKPOINT_UNAVAILABLE', 'CT_CLIP_CHECKPOINT_MISMATCH',
          'CT_CLIP_MODEL_UNAVAILABLE', 'CT_CLIP_MODEL_FAILED', 'CT_CLIP_INVALID_OUTPUT',
          'CT_CLIP_INVALID_INPUT', 'GPU_UNAVAILABLE', 'GPU_OOM'}


def classification_config(settings):
    checkpoint=settings.ct_clip_checkpoint
    if not checkpoint or not Path(checkpoint).is_file() or not re.fullmatch('[a-f0-9]{64}',settings.ct_clip_checkpoint_digest):
        raise ValueError('CT_CLIP_CHECKPOINT_UNAVAILABLE')
    if not math.isfinite(settings.ct_clip_threshold) or not 0<settings.ct_clip_threshold<1:
        raise ValueError('CT_CLIP_INVALID_CONFIG')
    return {'checkpoint':str(Path(checkpoint).resolve()),
            'checkpoint_digest':settings.ct_clip_checkpoint_digest,
            'threshold':settings.ct_clip_threshold,'timeout_seconds':settings.timeout_seconds}


def classification_result(scores,config):
    if len(scores)!=len(ABNORMALITIES) or any(not math.isfinite(s) or not 0<=s<=1 for s in scores):
        raise ValueError('CT_CLIP_INVALID_OUTPUT')
    return {'schema_version':1,'model':'CT-CLIP (ClassFine)',
            'checkpoint_sha256':config['checkpoint_digest'],'upstream_revision':UPSTREAM_REVISION,
            'scope':'non-contrast chest CT','review_status':'unreviewed',
            'threshold':config['threshold'],'flag_rule':'score >= threshold',
            'score_type':'sigmoid model score; not a calibrated probability',
            'preprocessing':{'orientation':'LAS','spacing_mm':[0.75,0.75,1.5],
                             'shape_dhw':[240,480,480],'hu_clip':[-1000,1000]},
            'abnormalities':[{'name':name,'score':float(score),'flagged':bool(score>=config['threshold'])}
                             for name,score in zip(ABNORMALITIES,scores)]}


def run_classification(native_path,config,workdir,lease_guard=None,gpu_lock_fd=None):
    workdir=Path(workdir).resolve();workdir.mkdir(parents=True,exist_ok=True,mode=0o700)
    request={**config,'input':str(Path(native_path).resolve())}
    config_path=workdir/'config.json'
    config_path.write_text(json.dumps(request));config_path.chmod(0o600)
    if lease_guard is None or not lease_guard():raise ValueError('LEASE_LOST')
    with model_temporary_directory(workdir) as temporary:
        environment=dict(os.environ,TMPDIR=temporary,TMP=temporary,TEMP=temporary,
                         HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        child=subprocess.Popen([sys.executable,'-m','app.inference.ct_clip_process',str(config_path)],
            env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,
            pass_fds=() if gpu_lock_fd is None else (gpu_lock_fd,))
        started=time.monotonic()
        try:
            while child.poll() is None:
                if time.monotonic()-started>config.get('timeout_seconds',3600):raise ValueError('CT_CLIP_TIMEOUT')
                if not lease_guard():raise ValueError('LEASE_LOST')
                time.sleep(.5)
            if child.returncode:
                error_path=workdir/'error.json'
                code=json.loads(error_path.read_text()).get('code') if error_path.exists() else None
                raise ValueError(code if code in ERRORS else 'CT_CLIP_MODEL_FAILED')
        finally:terminate(child)
    try:
        scores=json.loads((workdir/'scores.json').read_text())
        return classification_result(scores,config)
    except (OSError,TypeError,json.JSONDecodeError):
        raise ValueError('CT_CLIP_INVALID_OUTPUT') from None
