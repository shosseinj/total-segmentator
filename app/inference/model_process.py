"""Child process; network model downloads and telemetry are disabled."""
import json,sys,time
from pathlib import Path

def main():
    config=json.loads(Path(sys.argv[1]).read_text())
    error_path=Path(sys.argv[1]).parent/'model-error.json'
    try:
        import importlib.metadata
        import torch
        if importlib.metadata.version('TotalSegmentator')!=config['model_release']:raise ValueError('MODEL_VERSION_MISMATCH')
        if not torch.cuda.is_available():raise ValueError('GPU_UNAVAILABLE')
        from app.inference.totalseg_adapter import verify_model_cache
        verify_model_cache(config['model_cache'],config['checkpoint_digest'])
        from totalsegmentator import libs,python_api
        from totalsegmentator.config import get_weights_dir
        def offline_weights(task_id):
            roots=list(get_weights_dir().glob(f'Dataset{task_id}_*'))
            if not roots or not any(p.name=='checkpoint_final.pth' for root in roots for p in root.rglob('*.pth')):raise ValueError('CHECKPOINT_UNAVAILABLE')
        libs.download_pretrained_weights=offline_weights
        python_api.download_pretrained_weights=offline_weights
        python_api.send_usage_stats=lambda *args,**kwargs:None
        started=time.monotonic();torch.cuda.reset_peak_memory_stats()
        from app.inference.structures import TOTAL_STRUCTURES
        subset=None if set(config['structures'])==TOTAL_STRUCTURES else config['structures']
        python_api.totalsegmentator(config['input'],config['output'],task=config['task'],roi_subset=subset,ml=config.get('multilabel_output',False),fast=config['quality']=='fast',device='gpu',quiet=True,statistics=False,nr_thr_resamp=1,nr_thr_saving=1)
        (error_path.parent/'model-metrics.json').write_text(json.dumps({'model_wall_seconds':time.monotonic()-started,'vram_peak_bytes':torch.cuda.max_memory_allocated()}))
    except Exception as exc:
        allowed={'GPU_UNAVAILABLE','CHECKPOINT_UNAVAILABLE','MODEL_VERSION_MISMATCH'}
        code=str(exc) if str(exc) in allowed else 'GPU_OOM' if 'out of memory' in str(exc).lower() else 'MODEL_UNAVAILABLE' if isinstance(exc,ImportError) else 'MODEL_FAILED'
        error_path.write_text(json.dumps({'code':code}));sys.exit(1)

if __name__=='__main__':main()
