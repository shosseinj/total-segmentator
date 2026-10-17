import hashlib,json,re,time
from dataclasses import asdict
from sqlalchemy import select,text
from app.db.models import Scan,ScanObject,Series,Batch,Job,BatchJob,Outbox,AuditEvent,Idempotency

STRUCTURES={'liver','kidney_left','kidney_right','spleen'}
OUTPUTS={'dicom_seg_binary','nifti','measurements_json','manifest_json'}

def canonical_hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()

class JobService:
    def __init__(self,repository,settings,store=None):self.repository=repository;self.settings=settings;self.store=store

    def authorize(self,actor,mutation=False):
        if actor.tenant_id!=self.settings.tenant_id or mutation and not actor.operator:raise ValueError('ACCESS_DENIED')
        if not re.fullmatch('[A-Za-z0-9._-]{1,128}',actor.actor_id):raise ValueError('ACCESS_DENIED')

    def lock(self,db,actor):
        if db.bind.dialect.name=='postgresql':
            key=int.from_bytes(hashlib.sha256(actor.tenant_id.encode()).digest()[:8],'big',signed=True)
            db.execute(text('SELECT pg_advisory_xact_lock(:key)'),{'key':key})

    def mutation(self,actor,route,key,body,operation):
        self.authorize(actor,True)
        if not key or len(key)>128:raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
        digest=canonical_hash(body)
        with self.repository.session.begin() as db:
            self.lock(db,actor)
            existing=db.scalar(select(Idempotency).where(Idempotency.tenant_id==actor.tenant_id,Idempotency.actor_id==actor.actor_id,Idempotency.route==route,Idempotency.key==key))
            if existing:
                if existing.body_hash!=digest:raise ValueError('IDEMPOTENCY_CONFLICT')
                return existing.response
            response=operation(db)
            db.add(Idempotency(tenant_id=actor.tenant_id,actor_id=actor.actor_id,route=route,key=key,body_hash=digest,response=response))
            return response

    def replay(self,actor,route,key,body):
        self.authorize(actor,True)
        if not key or len(key)>128:raise ValueError('IDEMPOTENCY_KEY_REQUIRED')
        with self.repository.session() as db:
            row=db.scalar(select(Idempotency).where(Idempotency.tenant_id==actor.tenant_id,Idempotency.actor_id==actor.actor_id,Idempotency.route==route,Idempotency.key==key))
            if row:
                if row.body_hash!=canonical_hash(body):raise ValueError('IDEMPOTENCY_CONFLICT')
                return row.response
        return None

    def start_scan(self,actor,request,key):
        if request.source_id!=self.settings.source_id or not request.prefix.startswith(self.settings.allowed_prefix):raise ValueError('SOURCE_DENIED')
        if request.object_keys and (len(request.object_keys)>request.max_objects or any(not p.startswith(request.prefix) for p in request.object_keys)):raise ValueError('SCOPE_DENIED')
        def operation(db):
            scan=Scan(tenant_id=actor.tenant_id,source_id=request.source_id,requested_scope=request.model_dump(exclude={'source_id'}),created_by=actor.actor_id);db.add(scan);db.flush()
            db.add(Outbox(target_id=scan.id,event_type='inventory'))
            db.add(AuditEvent(actor=actor.actor_id,tenant=actor.tenant_id,action='scan_requested',target_id=scan.id))
            return {'scan_id':scan.id}
        return self.mutation(actor,'scans',key,request.model_dump(),operation)

    def normalize(self,request):
        if set(request.structures)-STRUCTURES or len(set(request.structures))!=len(request.structures) or set(request.outputs)!=OUTPUTS:raise ValueError('INVALID_CONFIG')
        if request.task=='total_mr' and not self.settings.mr_enabled:raise ValueError('MR_NOT_VALIDATED')
        if not re.fullmatch('[0-9a-f]{64}',self.settings.checkpoint_digest):raise ValueError('CHECKPOINT_UNAVAILABLE')
        return {'task':request.task,'structures':sorted(request.structures),'quality':request.quality,'outputs':sorted(OUTPUTS),'model_release':self.settings.model_release,'checkpoint_digest':self.settings.checkpoint_digest,'pipeline_version':self.settings.pipeline_version}

    def create_batch(self,actor,request,key):
        replay=self.replay(actor,'batches',key,request.model_dump())
        if replay is not None:return replay
        config=self.normalize(request)
        # Validate immutable input before approving; changed bytes demand a new scan.
        if self.store:
            from app.storage.minio_store import download_snapshot
            from app.contracts import ObjectSnapshot
            import tempfile
            self.settings.scratch.mkdir(parents=True,exist_ok=True)
            with self.repository.session() as db:
                scan=db.get(Scan,request.scan_id)
                if not scan or scan.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
                snapshots={}
                for item_id in request.series_item_ids:
                    item=db.get(Series,item_id)
                    if not item:raise ValueError('ITEM_NOT_FOUND')
                    obj=db.get(ScanObject,item.scan_object_id)
                    if obj.scan_id!=scan.id:raise ValueError('ITEM_NOT_IN_SCAN')
                    snapshots[obj.id]=obj.snapshot
            for snapshot in snapshots.values():
                with tempfile.TemporaryDirectory(dir=self.settings.scratch) as tmp:
                    download_snapshot(self.store,ObjectSnapshot(**snapshot),__import__('pathlib').Path(tmp)/'input.zip',self.settings.max_archive)
            from app.export.publisher import verify_job_artifacts
            with self.repository.session() as db:
                succeeded=db.scalars(select(Job).where(Job.tenant_id==actor.tenant_id,Job.status=='succeeded')).all()
            for existing in succeeded:verify_job_artifacts(self.repository,self.store,existing.id)
        def operation(db):
            scan=db.get(Scan,request.scan_id)
            if not scan or scan.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            if scan.status!='completed':raise ValueError('SCAN_NOT_COMPLETED')
            if scan.source_id!=self.settings.source_id:raise ValueError('SOURCE_DENIED')
            if len(set(request.series_item_ids))!=len(request.series_item_ids):raise ValueError('DUPLICATE_ITEMS')
            batch=Batch(tenant_id=actor.tenant_id,scan_id=scan.id,created_by=actor.actor_id,approved_item_ids=request.series_item_ids,normalized_config=config);db.add(batch);db.flush()
            created=[];existing=[]
            for id in request.series_item_ids:
                item=db.get(Series,id)
                if not item:raise ValueError('ITEM_NOT_FOUND')
                obj=db.get(ScanObject,item.scan_object_id)
                if obj.scan_id!=scan.id:raise ValueError('ITEM_NOT_IN_SCAN')
                if item.eligibility!='eligible' or not obj.snapshot.get('sha256'):raise ValueError('ITEM_NOT_ELIGIBLE')
                if item.modality!=('CT' if request.task=='total' else 'MR'):raise ValueError('TASK_MODALITY_MISMATCH')
                result_key=canonical_hash({'tenant_id':actor.tenant_id,'source_id':scan.source_id,'input_sha256':obj.snapshot['sha256'],'study_uid':item.study_uid,'series_uid':item.series_uid,**config})
                job=db.scalar(select(Job).where(Job.result_key==result_key).with_for_update())
                if job:
                    if job.cancel_requested and job.status not in ('cancelled','failed'):raise ValueError('JOB_CANCEL_PENDING')
                    if job.status in ('failed','cancelled','unsupported'):raise ValueError('EXPLICIT_RETRY_REQUIRED')
                    existing.append(job.id)
                else:
                    job=Job(tenant_id=actor.tenant_id,result_key=result_key,series_item_id=item.id,config={**config,'configuration_hash':canonical_hash(config)});db.add(job);db.flush();created.append(job.id)
                    db.add(Outbox(target_id=job.id,event_type='inference'))
                db.add(BatchJob(batch_id=batch.id,job_id=job.id,reused=job.id in existing))
            db.add(AuditEvent(actor=actor.actor_id,tenant=actor.tenant_id,action='batch_approved',target_id=batch.id))
            return {'batch_id':batch.id,'job_ids':created+existing,'created':created,'existing':existing}
        return self.mutation(actor,'batches',key,request.model_dump(),operation)

    def cancel_batch(self,actor,batch_id,key):
        def operation(db):
            batch=db.get(Batch,batch_id)
            if not batch or batch.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            batch.status='cancelled';batch.cancel_requested_at=time.time()
            members=db.scalars(select(BatchJob).where(BatchJob.batch_id==batch_id)).all()
            for member in members:member.membership_status='cancelled'
            db.flush()
            for member in members:
                job=db.get(Job,member.job_id,with_for_update=True)
                active=db.scalar(select(BatchJob).where(BatchJob.job_id==job.id,BatchJob.membership_status=='active'))
                if not active and job.status not in ('succeeded','failed','unsupported','cancelled'):
                    job.cancel_requested=True
                    if job.status=='queued':job.status='cancelled';job.stage='cancelled'
            db.add(AuditEvent(actor=actor.actor_id,tenant=actor.tenant_id,action='batch_cancelled',target_id=batch_id))
            return {'batch_id':batch_id,'status':'cancelled'}
        return self.mutation(actor,'cancel/'+batch_id,key,{},operation)

    def retry(self,actor,job_id,key):
        def operation(db):
            job=db.get(Job,job_id,with_for_update=True)
            if not job or job.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            if job.status not in ('failed','cancelled') or job.safe_error in ('INPUT_CHANGED','INVALID_PIXEL_DATA','UNSUPPORTED_TRANSFER_SYNTAX'):raise ValueError('NOT_RETRYABLE')
            if job.attempt_token:raise ValueError('WORKER_TERMINATION_REQUIRED')
            members=db.scalars(select(BatchJob).join(Batch).where(BatchJob.job_id==job.id,Batch.tenant_id==actor.tenant_id)).all()
            if not members:raise ValueError('NO_APPROVAL')
            # Explicit retry reactivates an existing approved membership.
            members[0].membership_status='active';db.get(Batch,members[0].batch_id).status='approved'
            job.status='queued';job.stage='queued';job.cancel_requested=False;job.safe_error=None;job.artifacts={};job.result={};job.retry_at=None
            db.add(Outbox(target_id=job.id,event_type='inference'));db.add(AuditEvent(actor=actor.actor_id,tenant=actor.tenant_id,action='retry_approved',target_id=job.id))
            return {'job_id':job.id}
        return self.mutation(actor,'retry/'+job_id,key,{},operation)
