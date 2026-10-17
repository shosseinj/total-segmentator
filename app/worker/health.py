import importlib.metadata,socket,threading,time
from app.db.models import WorkerHealth

def start_heartbeat(repository,settings,role):
    ready=True;error=None
    if role=='gpu':
        try:
            import torch
            from app.inference.totalseg_adapter import verify_model_cache
            if not torch.cuda.is_available():raise ValueError('GPU_UNAVAILABLE')
            if importlib.metadata.version('TotalSegmentator')!=settings.model_release:raise ValueError('MODEL_VERSION_MISMATCH')
            verify_model_cache(settings.model_cache,settings.checkpoint_digest)
        except Exception as exc:ready=False;error=str(exc) if str(exc) in {'GPU_UNAVAILABLE','MODEL_VERSION_MISMATCH','CHECKPOINT_UNAVAILABLE'} else 'MODEL_UNAVAILABLE'
    def loop():
        while True:
            try:
                with repository.session.begin() as db:
                    id=socket.gethostname()+':'+role;record=db.get(WorkerHealth,id)
                    if record is None:record=WorkerHealth(id=id,role=role);db.add(record)
                    record.ready=ready;record.safe_error=error;record.heartbeat_at=time.time()
            except Exception:pass
            time.sleep(30)
    threading.Thread(target=loop,name='worker-health',daemon=True).start()
