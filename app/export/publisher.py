import json,time
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.db.models import Job,Attempt,AuditEvent
from app.jobs.leases import owned
from app.storage.minio_store import file_sha

FORMATS={'seg':'dicom_seg_binary','masks':'nifti','measurements':'measurements_json','manifest':'manifest_json'}
NAMES={'seg':'segmentation.dcm','masks':'masks.nii.gz','measurements':'measurements.json','manifest':'manifest.json'}

class PublicationPending(ConnectionError):
    """Uploads are durable but the final database commit has an uncertain outcome."""

def finalize_result(repository,lease,pointers,result):
    with repository.session.begin() as db:
        job=db.get(Job,lease.job_id,with_for_update=True)
        if not owned(job,lease) or job.stage!='uploading':raise ValueError('LEASE_LOST')
        job.artifacts=pointers;job.result=result;job.status='succeeded';job.stage='succeeded';job.attempt_token=None;job.lease_until=None
        attempt=db.scalar(select(Attempt).where(Attempt.token==lease.token));attempt.ended_at=time.time();attempt.outcome='succeeded'

def publish_result(repository,store,lease,artifacts,result,lease_guard=None):
    if set(artifacts)!= {'seg','masks','measurements'}:raise ValueError('INCOMPLETE_ARTIFACT_BUNDLE')
    with repository.session() as db:
        job=db.get(Job,lease.job_id)
        if not owned(job,lease) or job.stage!='uploading':raise ValueError('LEASE_LOST')
        attempt=db.scalar(select(Attempt).where(Attempt.token==lease.token));attempt_id=attempt.id
        prefix=f'segmentation/{job.tenant_id}/{job.id}/attempts/{attempt_id}/'
        config=job.config
    pointers={id:{'key':prefix+NAMES[id],'sha256':file_sha(path),'size':path.stat().st_size,'format':FORMATS[id]} for id,path in artifacts.items()}
    manifest={**result,'schema_version':'1.0','job_id':lease.job_id,'attempt_id':attempt_id,'model':config,'artifacts':pointers}
    manifest_path=artifacts['measurements'].parent/'manifest.json';manifest_path.write_text(json.dumps(manifest,sort_keys=True,separators=(',',':')))
    pointers={**pointers,'manifest':{'key':prefix+NAMES['manifest'],'sha256':file_sha(manifest_path),'size':manifest_path.stat().st_size,'format':FORMATS['manifest']}}
    # Candidate pointers precede uploads and allow verified adoption after a DB failure.
    with repository.session.begin() as db:
        job=db.get(Job,lease.job_id,with_for_update=True)
        if not owned(job,lease):raise ValueError('LEASE_LOST')
        attempt=db.scalar(select(Attempt).where(Attempt.token==lease.token));attempt.artifact_candidate={'artifacts':pointers,'result':result}
    for id,path in [*artifacts.items(),('manifest',manifest_path)]:
        if lease_guard and not lease_guard():raise ValueError('LEASE_LOST')
        store.put(pointers[id]['key'],path)
        if not store.verify_output(pointers[id]['key'],pointers[id]['size'],pointers[id]['sha256']):raise ValueError('ARTIFACT_INVALID')
    try:finalize_result(repository,lease,pointers,result)
    except (SQLAlchemyError,ConnectionError):raise PublicationPending('DB_PUBLICATION_PENDING') from None
    return pointers

def verify_job_artifacts(repository,store,job_id):
    with repository.session() as db:
        job=db.get(Job,job_id)
        if not job or job.status!='succeeded':return False
        pointers=job.artifacts
    valid=set(pointers)==set(FORMATS) and all(store.verify_output(p['key'],p['size'],p['sha256']) for p in pointers.values())
    if not valid:
        with repository.session.begin() as db:
            job=db.get(Job,job_id,with_for_update=True)
            if job.status=='succeeded':
                job.status='failed';job.stage='failed';job.safe_error='ARTIFACT_INVALID'
                db.add(AuditEvent(actor='integrity-check',tenant=job.tenant_id,action='artifact_integrity_failure',target_id=job_id))
    return valid

def get_result(repository,store,actor,job_id):
    with repository.session() as db:
        job=db.get(Job,job_id)
        if not job or job.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
        if job.status!='succeeded':raise ValueError('RESULT_NOT_READY')
    if not verify_job_artifacts(repository,store,job_id):raise ValueError('ARTIFACT_INVALID')
    with repository.session() as db:
        job=db.get(Job,job_id)
        return {**job.result,'schema_version':'1.0','job_id':job.id,'status':'succeeded','review_status':'unreviewed','artifacts':[{'artifact_id':id,'format':p['format'],'path':f'/v1/jobs/{job.id}/artifacts/{id}'} for id,p in job.artifacts.items()]}
