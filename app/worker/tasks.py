"""One GPU slot, operator-approved series only; subprocess death precedes retries."""
import errno,fcntl,json,os,resource,shutil,time,warnings,threading
from contextlib import contextmanager
import nibabel as nib
import numpy as np
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from botocore.exceptions import BotoCoreError,ClientError
from app.contracts import ObjectSnapshot,SeriesItem
from app.db.models import Job,Series,ScanObject
from app.jobs.leases import claim_job,heartbeat,transition
from app.jobs.recovery import fail_attempt
from app.storage.minio_store import download_snapshot
from app.inventory.archive import extract_zip
from app.inventory.dicom_series import inventory_paths
from app.geometry.native_grid import build_native_volume,resample_masks_to_native
from app.inference.totalseg_adapter import run_segmentation
from app.export.measurements import measure
from app.export.dicom_seg import export_seg
from app.export.publisher import publish_result,PublicationPending

class LeaseMonitor:
    """Renew throughout validation, model execution, export and publication."""
    def __init__(self,repository,lease,seconds):
        self.repository=repository;self.lease=lease;self.seconds=seconds
        self.stopped=threading.Event();self.lost=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True)
    def run(self):
        while not self.stopped.wait(min(30,self.seconds/3)):
            try:
                if not self.check():return
            except SQLAlchemyError:
                self.lost.set();return
    def check(self):
        if self.lost.is_set():return False
        valid=heartbeat(self.repository,self.lease,self.seconds)
        if not valid:self.lost.set()
        return valid
    def close(self):self.stopped.set();self.thread.join()

@contextmanager
def gpu_slot(scratch):
    scratch.mkdir(parents=True,exist_ok=True)
    with (scratch/'gpu0.lock').open('a') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('GPU_BUSY') from None
        try:yield f.fileno()
        finally:fcntl.flock(f,fcntl.LOCK_UN)

def termination_confirmed(settings,job):
    try:
        with gpu_slot(settings.scratch):return True
    except ValueError:return False

def execute_job(repository,store,settings,job_id,worker_id):
    with gpu_slot(settings.scratch) as lock_fd:
        lease=claim_job(repository,job_id,worker_id,settings.lease_seconds)
        if lease is None:return 'not_claimed'
        started=time.monotonic();work=settings.scratch/'jobs'/job_id/lease.token;work.mkdir(parents=True,exist_ok=True);work.chmod(0o700)
        monitor=LeaseMonitor(repository,lease,settings.lease_seconds);monitor.thread.start()
        try:
            with repository.session() as db:
                job=db.get(Job,job_id);item=db.get(Series,job.series_item_id);obj=db.get(ScanObject,item.scan_object_id);snapshot=ObjectSnapshot(**obj.snapshot);config=job.config
                study_uid,series_uid,sops=item.study_uid,item.series_uid,item.source_instances
            downloaded=download_snapshot(store,snapshot,work/'input.zip',settings.max_archive)
            files=extract_zip(downloaded.path,work/'extracted',settings.max_expanded,settings.max_entries,settings.max_ratio)
            # Inventory may contain other series; only the approved series reaches the model.
            inventory=inventory_paths(files)
            matching=[i for i in inventory if i.study_uid==study_uid and i.series_uid==series_uid and i.eligibility=='eligible']
            if len(matching)!=1 or matching[0].source_instances!=sops:raise ValueError('INPUT_CHANGED')
            volume=build_native_volume(matching[0])
            if not monitor.check() or not transition(repository,lease,'running'):raise ValueError('LEASE_LOST')
            masks=run_segmentation(volume,{**config,'model_cache':str(settings.model_cache),'timeout_seconds':settings.timeout_seconds},work,lease_guard=monitor.check,gpu_lock_fd=lock_fd)
            if not transition(repository,lease,'exporting'):raise ValueError('LEASE_LOST')
            masks=resample_masks_to_native(masks,volume);measurements=measure(masks,volume)
            artifacts={id:work/name for id,name in [('seg','segmentation.dcm'),('masks','masks.nii.gz'),('measurements','measurements.json')]}
            # Avoid highdicom name warnings printing source PHI to logs.
            with warnings.catch_warnings():
                warnings.simplefilter('ignore',UserWarning);export_seg(masks,volume,config,artifacts['seg'],max_bytes=settings.max_artifact)
            labelmap=np.zeros(volume.array.shape,np.uint8)
            for number,label in enumerate(sorted(masks),1):labelmap[masks[label]>0]=number
            nib.save(nib.Nifti1Image(labelmap,volume.affine_ras),artifacts['masks'])
            artifacts['measurements'].write_text(json.dumps(measurements,sort_keys=True))
            metrics_path=work/'model-metrics.json'
            metrics=json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
            result={**measurements,'study_instance_uid':study_uid,'series_instance_uid':series_uid,'frame_of_reference_uid':str(volume.datasets[0].FrameOfReferenceUID),'source_sop_instance_uids':sops,'source':obj.snapshot,'geometry':matching[0].geometry_summary,'geometry_transforms':['DICOM LPS native to NIfTI RAS','model affine nearest-neighbor resampled to original RAS grid'],'warnings':matching[0].warnings,'benchmark':{'zip_size_bytes':snapshot.size,'slices':len(sops),'wall_seconds':time.monotonic()-started,'worker_ram_peak_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,**metrics}}
            if not monitor.check() or not transition(repository,lease,'uploading'):raise ValueError('LEASE_LOST')
            publish_result(repository,store,lease,artifacts,result,lease_guard=monitor.check)
            return 'succeeded'
        except Exception as exc:
            if isinstance(exc,PublicationPending):return 'DB_PUBLICATION_PENDING'
            if isinstance(exc,SQLAlchemyError):return 'DB_UNAVAILABLE'
            if isinstance(exc,(BotoCoreError,ClientError,ConnectionError)):code='STORAGE_UNAVAILABLE'
            elif isinstance(exc,OSError) and exc.errno==errno.ENOSPC:code='DISK_FULL'
            elif isinstance(exc,ValueError):code=str(exc) if str(exc).isupper() and len(str(exc))<80 else 'PIPELINE_FAILED'
            else:code='PIPELINE_FAILED'
            try:fail_attempt(repository,lease,code)
            except SQLAlchemyError:return 'DB_UNAVAILABLE'
            return code
        finally:monitor.close();shutil.rmtree(work,ignore_errors=True)
