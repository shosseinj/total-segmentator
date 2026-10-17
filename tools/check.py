"""GPU-side checks: no scan, batch, patient download or model inference."""
import argparse,errno,json,os,socket,sys,tempfile,uuid
from pathlib import Path
from .common import verify_handoff,read_connection

def handoff():
    values=read_connection('connection.env')
    verify_handoff(json.loads(Path('handoff.json').read_text()),values)
    print('HANDOFF_VALID')

def check_connections(settings):
    import redis
    from sqlalchemy import text
    from app.db.repository import Repository
    from app.storage.minio_store import MinioStore
    repo=Repository(settings.database_url)
    with repo.engine.connect() as db:db.execute(text('SELECT 1'))
    print('POSTGRES_OK')
    redis.Redis.from_url(settings.redis_url,socket_connect_timeout=10,socket_timeout=10).ping();print('REDIS_OK')
    store=MinioStore(settings)
    store.client.head_bucket(Bucket=settings.input_bucket);print('INPUT_BUCKET_READ_ACCESS_OK')
    key='segmentation/'+settings.tenant_id+'/diagnostics/'+uuid.uuid4().hex+'/connectivity.txt'
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'probe';path.write_bytes(b'segmentation connectivity probe; no patient data')
        from app.storage.minio_store import file_sha
        try:
            store.put(key,path)
            if not store.verify_output(key,path.stat().st_size,file_sha(path)):raise ValueError('OUTPUT_VERIFY_FAILED')
        finally:store.delete_output(key)
    print('OUTPUT_PUT_GET_VERIFY_DELETE_OK')
    from app.inference.totalseg_adapter import verify_model_cache
    entries=verify_model_cache(settings.model_cache,settings.checkpoint_digest)
    print('CHECKPOINTS_VERIFIED_FILES='+str(len(entries)))
    settings.scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.scratch) as tmp:Path(tmp,'write-check').write_text('ok')
    print('SCRATCH_WRITABLE_OK')

def check_gpu(settings):
    import importlib.metadata
    import torch
    if importlib.metadata.version('TotalSegmentator')!=settings.model_release:raise ValueError('MODEL_VERSION_MISMATCH')
    if not torch.cuda.is_available():raise ValueError('GPU_UNAVAILABLE')
    # Exercise a real kernel, not just the presence of device nodes.
    probe=torch.ones((16,16),device='cuda');assert int((probe@probe).sum().item())==4096
    torch.cuda.synchronize()
    print('CUDA_KERNEL_OK');print('GPU_NAME='+torch.cuda.get_device_name(0))
    print('GPU_VRAM_BYTES='+str(torch.cuda.get_device_properties(0).total_memory))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['connections','gpu','handoff','ready','port'])
    parser.add_argument('--port',type=int,default=8096);args=parser.parse_args()
    try:
        if args.mode=='handoff':handoff();return
        if args.mode=='port':
            try:
                with socket.socket() as probe:probe.bind(('127.0.0.1',args.port))
            except OSError as exc:
                if exc.errno==errno.EADDRINUSE:raise ValueError('API_PORT_IN_USE') from None
                raise
            print('API_PORT_AVAILABLE');return
        if args.mode=='ready':
            import httpx,time
            for _ in range(90):
                try:
                    response=httpx.get(f'http://127.0.0.1:{args.port}/health/ready',timeout=5,trust_env=False)
                    if response.status_code==200 and response.json().get('worker_model_ready'):
                        print('API_READY_GPU_WORKER_READY');return
                except httpx.HTTPError:pass
                time.sleep(1)
            raise ValueError('GPU_WORKER_NOT_READY')
        from app.config import Settings
        settings=Settings.from_env()
        if args.mode=='connections':check_connections(settings)
        else:check_gpu(settings)
    except Exception as exc:
        safe={'API_PORT_IN_USE','OUTPUT_VERIFY_FAILED','GPU_UNAVAILABLE','MODEL_VERSION_MISMATCH','CHECKPOINT_UNAVAILABLE','GPU_WORKER_NOT_READY','HANDOFF_INVALID'}
        print('CHECK_FAILED: '+(str(exc) if str(exc) in safe else 'CHECK_DEPENDENCIES_CONFIG_OR_NETWORK'),file=sys.stderr)
        sys.exit(1)

if __name__=='__main__':main()
