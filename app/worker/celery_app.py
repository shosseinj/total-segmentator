import socket
from celery import Celery
from app.config import Settings
from app.db.repository import Repository
from app.storage.minio_store import MinioStore
from app.inventory.scanner import run_scan
from app.worker.tasks import execute_job

settings=Settings.from_env()
celery=Celery('segmentation',broker=settings.redis_url)
celery.conf.update(task_acks_late=True,task_reject_on_worker_lost=True,worker_prefetch_multiplier=1,task_serializer='json',accept_content=['json'],task_routes={'inventory':{'queue':'inventory'},'inference':{'queue':'gpu'}},broker_transport_options={'visibility_timeout':settings.timeout_seconds+300})

@celery.task(name='inventory',ignore_result=True)
def inventory(id):
    try:run_scan(id,Repository(settings.database_url),MinioStore(settings),settings)
    except Exception:
        # Durable approval remains queued/scanning for dispatcher redelivery.
        # Never let Celery log SQL parameters, credentials or source metadata.
        print('stage=inventory error=DEPENDENCY_UNAVAILABLE',flush=True)

@celery.task(name='inference',ignore_result=True)
def inference(id):
    return execute_job(Repository(settings.database_url),MinioStore(settings),settings,id,socket.gethostname())

from celery.signals import worker_ready
@worker_ready.connect
def register_worker(sender=None,**kwargs):
    from app.worker.health import start_heartbeat
    queues=[q.name for q in sender.task_consumer.queues]
    start_heartbeat(Repository(settings.database_url),settings,'gpu' if 'gpu' in queues else 'inventory')
