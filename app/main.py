from contextlib import asynccontextmanager
from typing import Literal
from pathlib import Path
import asyncio
import json
import time
from fastapi import Depends,FastAPI,File,Form,Header,HTTPException,Query,Request,UploadFile
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse,RedirectResponse,Response
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select,text
from sqlalchemy.exc import SQLAlchemyError
from botocore.exceptions import BotoCoreError,ClientError
from app.auth import authenticate,authenticate_local,local_request_key
from app.contracts import BatchRequest,ScanRequest
from app.db.models import Batch,BatchJob,Job,Scan,ScanObject,Series,WorkerHealth
from app.db.repository import Repository
from app.jobs.service import JobService,STRUCTURES,canonical_hash
from app.storage.minio_store import MinioStore
from app.export.publisher import get_result
from app.local_jobs import LocalJobs
from app.inference.structures import TOTAL_LABELS
from app.upload_page import STAGES

ERROR_STATUS={'ACCESS_DENIED':403,'SOURCE_DENIED':403,'SCOPE_DENIED':403,'IDEMPOTENCY_KEY_REQUIRED':400,'INVALID_CONFIG':422,'MR_NOT_VALIDATED':422,'TASK_MODALITY_MISMATCH':422,'CHECKPOINT_UNAVAILABLE':503,'INVENTORY_IO_FAILED':503}
ERROR_STATUS.update(ARCHIVE_LIMIT=413,CORRUPT_ZIP=422,EMPTY_ZIP=422,ENCRYPTED_ZIP=422,UPLOAD_VERIFY_FAILED=503)
ERROR_STATUS.update(UPLOAD_NOT_FOUND=404,ARTIFACT_NOT_FOUND=404)
ERROR_STATUS.update(VIEW_LINK_INVALID=403)
SAVED_VIEWER_PATH=Path(__file__).resolve().parents[1]/'exports'/'dicom-viewer.html'

def create_app(settings,repository=None,store=None):
    @asynccontextmanager
    async def lifespan(app):
        if local_jobs:await run_in_threadpool(local_jobs.reap_ephemeral,True)
        try:yield
        finally:
            if local_jobs:await run_in_threadpool(local_jobs.reap_ephemeral,True)

    app=FastAPI(title='ناحیه‌بندی سی‌تی با کارت گرافیک',docs_url=None,redoc_url=None,lifespan=lifespan,
                description='برای دریافت viewer.html از POST /v1/upload/ استفاده کنید. فقط HTML نهایی تحویل داده می‌شود و فایل‌های موقت پاک می‌شوند. آپلودها سابقهٔ خودکار ندارند؛ نسخهٔ ذخیره‌شدهٔ dicom.zip از بخش Saved results قابل دریافت است. در حالت آزمایشی، توکن Swagger خودکار وارد می‌شود؛ در حالت عادی SEG_SERVICE_TOKEN را در Authorize وارد کنید. اطلاعات مرکز و اجرا و کلید درخواست خودکار تعیین می‌شوند. پیش‌فرض پردازش همهٔ ۱۱۷ ناحیه است.')
    app.state.settings=settings
    repo=repository or (Repository(settings.database_url) if settings else None)
    store=store or (MinioStore(settings) if settings else None)
    service=JobService(repo,settings,store) if settings else None
    app.state.repository=repo
    local_jobs=LocalJobs(settings) if settings else None

    @app.get('/',include_in_schema=False)
    def index():return RedirectResponse('/docs')

    @app.get('/docs',include_in_schema=False)
    def swagger():
        response=get_swagger_ui_html(openapi_url=app.openapi_url,title='GPU segmentation · Swagger')
        token=settings.service_token if settings and settings.swagger_auto_authorize else None
        # Only this explicitly enabled test UI receives the token; never put it
        # in OpenAPI, result HTML, URLs or browser persistent storage.
        toolbar=Path(__file__).with_name('web').joinpath('swagger_upload.html').read_text().replace('__STAGES__',json.dumps(STAGES,ensure_ascii=False))
        toolbar=toolbar.replace('__DEFAULT_TOKEN__',json.dumps(token).replace('<','\\u003c'))
        document=response.body.decode().replace('<body>','<body>'+toolbar)
        if token:
            document=document.replace('const ui = SwaggerUIBundle({','const ui = SwaggerUIBundle({\n        onComplete: () => window.segSwaggerAuthorize(ui),',1)
        return HTMLResponse(document,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get('/results',include_in_schema=False)
    def no_saved_results():return RedirectResponse('/docs')

    @app.get('/docs/samples/viewer.html',include_in_schema=False)
    def sample_viewer(download:bool=False):
        return FileResponse(Path(__file__).with_name('web')/'samples'/'viewer.html',media_type='text/html',
                            filename='viewer.html' if download else None,
                            headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/v1/exports/dicom-viewer.html',tags=['Saved results'],operation_id='download_saved_dicom_viewer',
             summary='Download the saved dicom.zip viewer',response_class=FileResponse,
             description='نسخهٔ کامل ذخیره‌شده از پردازش dicom.zip را دریافت کنید. Try it out و سپس Execute را بزنید و از Download file استفاده کنید. فایل HTML را پس از دریافت در مرورگر باز کنید. این مسیر نیازمند توکن سرویس است و پردازش جدیدی اجرا نمی‌کند.',
             responses={200:{'description':'Complete saved HTML viewer, delivered as a file download.',
                             'content':{'application/octet-stream':{'schema':{'type':'string','format':'binary'}}}},
                        404:{'description':'The saved viewer is not present on this server.'}})
    def download_saved_viewer(actor=Depends(authenticate_local)):
        if not SAVED_VIEWER_PATH.is_file():raise HTTPException(404,'SAVED_VIEWER_NOT_FOUND')
        return FileResponse(SAVED_VIEWER_PATH,media_type='application/octet-stream',filename='dicom-viewer.html',
                            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'})

    @app.exception_handler(ValueError)
    async def safe_error(request,exc):
        code=str(exc)
        return JSONResponse(status_code=ERROR_STATUS.get(code,409),content={'detail':code if code.isupper() and len(code)<80 else 'INTERNAL_ERROR'})

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request,exc):return JSONResponse(status_code=503,content={'detail':'DB_UNAVAILABLE'})

    async def storage_unavailable(request,exc):return JSONResponse(status_code=503,content={'detail':'STORAGE_UNAVAILABLE'})
    for error in (BotoCoreError,ClientError,ConnectionError):app.add_exception_handler(error,storage_unavailable)

    @app.get('/health/live')
    def live():return {'status':'alive'}

    def model_ready():
        import time
        with repo.session() as db:
            return db.scalar(select(WorkerHealth).where(WorkerHealth.role=='gpu',WorkerHealth.ready==True,WorkerHealth.heartbeat_at>time.time()-90)) is not None

    @app.get('/health/ready')
    def ready():
        if settings is None:raise HTTPException(503,'CONFIG_MISSING')
        try:
            with repo.engine.connect() as db:db.execute(text('SELECT 1'))
        except Exception:raise HTTPException(503,'DB_UNAVAILABLE') from None
        return {'status':'ready','worker_model_ready':model_ready(),'model_checkpoints_configured':bool(settings.checkpoint_digest)}

    @app.get('/metrics')
    def metrics(actor=Depends(authenticate)):
        import time
        from collections import Counter
        with repo.session() as db:
            jobs=db.scalars(select(Job).where(Job.tenant_id==actor.tenant_id)).all()
            statuses=Counter(j.status for j in jobs)
            errors=Counter(j.safe_error for j in jobs if j.status=='failed' and j.safe_error)
            benchmarks=[j.result.get('benchmark',{}) for j in jobs if j.status=='succeeded']
            times=[b['wall_seconds'] for b in benchmarks if 'wall_seconds' in b]
            return {'queued_jobs':statuses['queued'],'jobs_by_stage':dict(statuses),'failures_by_code':dict(errors),
                    'gpu_jobs_with_active_lease':sum(j.status in {'validating','running','exporting','uploading'} and (j.lease_until or 0)>time.time() for j in jobs),
                    'gpu_worker_ready':model_ready(),'mean_completed_wall_seconds':sum(times)/len(times) if times else None}

    @app.get('/v1/capabilities')
    def capabilities(actor=Depends(authenticate)):
        return {'sources':[{'source_id':settings.source_id,'prefix':settings.allowed_prefix}],'tasks':['total']+(['total_mr'] if settings.mr_enabled else []),'structures':sorted(STRUCTURES),'qualities':['standard','fast'],'outputs':['dicom_seg_binary','nifti','measurements_json','manifest_json'],'model_release':settings.model_release,'pipeline_version':settings.pipeline_version,'max_objects':20,'mr_validated':settings.mr_enabled,'inference_ready':model_ready()}

    @app.post('/v1/scans',status_code=202)
    def start_scan(body:ScanRequest,actor=Depends(authenticate),key:str=Header(default='',alias='Idempotency-Key')):return service.start_scan(actor,body,key)

    @app.get('/v1/uploads',include_in_schema=False)
    def local_list(actor=Depends(authenticate_local)):
        return {'items':[],'retention':False}

    @app.get('/v1/uploads/capabilities',tags=['آپلود ZIP و نتایج'],summary='فهرست ۱۱۷ ناحیهٔ قابل پردازش')
    def local_capabilities(actor=Depends(authenticate_local)):
        return {'structures_default':'all','structures':list(TOTAL_LABELS.values()),'region_count':len(TOTAL_LABELS),
                'qualities':['standard','fast'],'storage':'temporary','output':'viewer.html','retention':False}

    @app.get('/v1/upload/progress',include_in_schema=False)
    def current_upload_progress(actor=Depends(authenticate_local),key:str=Header(default='',alias='Idempotency-Key',include_in_schema=False)):
        return JSONResponse(local_jobs.progress(actor,key),headers={'Cache-Control':'no-store'})

    @app.get('/v1/uploads/{id}',tags=['آپلود ZIP و نتایج'],summary='وضعیت پردازش فعال؛ نتیجه ذخیره نمی‌شود')
    def local_status(id:str,actor=Depends(authenticate_local)):
        state=local_jobs.authorized(id,actor)
        return {k:state[k] for k in ('job_id','status','stage','selected_series_index','series') if k in state}

    @app.post('/v1/uploads',status_code=200,include_in_schema=False,response_class=FileResponse)
    @app.post('/v1/upload/',status_code=200,tags=['آپلود ZIP و نتایج'],summary='آپلود ZIP و دریافت HTML نهایی بدون نگه‌داری نتیجه',response_class=FileResponse,
              description='فقط فایل HTML مستقل برمی‌گردد. ZIP ورودی و همهٔ فایل‌های موقت پس از آماده‌شدن پاسخ پاک می‌شوند؛ سابقه، کش ماسک و ZIP نتیجه نگه‌داری نمی‌شود. Swagger مرحلهٔ جاری را نشان می‌دهد و viewer.html را خودکار دریافت می‌کند. پیش‌فرض سری -1 یعنی انتخاب خودکار CT معتبر با بیشترین برش.\n\n'
                          '**نمونهٔ خروجی:** [نمایش viewer.html](/docs/samples/viewer.html) · [دریافت فایل نمونه](/docs/samples/viewer.html?download=true). این نمونه با داده‌ها و ماسک‌های مصنوعی ساخته شده و شامل اطلاعات بیمار یا پیش‌بینی مدل نیست.',
              openapi_extra={'externalDocs':{'description':'نمایش نمونهٔ کامل viewer.html با داده‌های مصنوعی','url':'/docs/samples/viewer.html'}},
              responses={200:{'description':'فایل کامل و مستقل viewer.html؛ نمونهٔ تعاملی در توضیحات بالا قابل نمایش و دریافت است.',
                              'headers':{'Content-Disposition':{'schema':{'type':'string'},'example':'attachment; filename="viewer.html"'},
                                         'X-Job-Id':{'schema':{'type':'string'},'example':'0123456789abcdef0123456789abcdef'},
                                         'X-Selected-Series-Index':{'schema':{'type':'integer'},'example':0},
                                         'X-Selected-Series-Slices':{'schema':{'type':'integer'},'example':24}},
                              'content':{'text/html':{'schema':{'type':'string','format':'binary'},
                                                      # externalValue makes Swagger fetch and render the entire
                                                      # embedded CT viewer when this operation is expanded.
                                                      'examples':{'viewer':{'summary':'نمونهٔ کوتاه HTML؛ فایل کامل از لینک بالا قابل دریافت است.',
                                                                            'value':'<!doctype html>\n<html lang="fa" dir="rtl">\n<head><meta charset="utf-8"><title>فضای بررسی سی‌تی | شریف</title></head>\n<body>\n<main class="viewer-app">\n<!-- Shortened example: the complete viewer includes CT images, segmentation masks and interactive controls. -->\n</main>\n</body>\n</html>'}}}}},
                         422:{'description':'خطای فایل یا پردازش؛ فایل‌های موقت پاک می‌شوند.'},504:{'description':'پایان مهلت؛ پردازش لغو و فایل‌های موقت پاک می‌شوند.'}})
    async def upload_zip(request:Request,file:UploadFile=File(...,description='فایل ZIP حاوی فایل‌های دایکام؛ پوشه‌های داخلی مجازند.',json_schema_extra={'format':'binary'}),
                   structures:str=Form('all',description='همهٔ ۱۱۷ ناحیه با all؛ یا نام‌های لاتین ناحیه‌ها با ویرگول.'),
                   quality:Literal['standard','fast']=Form('standard'),
                   series_index:int|None=Form(-1,ge=-1,description='-1 یا خالی = انتخاب خودکار CT معتبر با بیشترین برش؛ 0 و بالاتر = شمارهٔ صریح سری.'),
                   wait_timeout:int=Form(3600,ge=1,le=86400,description='حداکثر انتظار برای HTML، بر حسب ثانیه. پس از پایان مهلت، فایل‌های موقت پاک می‌شوند.'),
                   actor=Depends(authenticate_local),key:str=Depends(local_request_key)):
        if series_index==-1:series_index=None
        result=await run_in_threadpool(local_jobs.submit,file.file,actor,key,
            [x.strip() for x in structures.split(',') if x.strip()],quality,False,series_index is None,series_index,True,wait_timeout)
        id=result['job_id'];deadline=time.monotonic()+wait_timeout
        headers={'X-Job-Id':id,'Idempotency-Key':key,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}
        try:
            while True:
                if time.monotonic()>=deadline:
                    return JSONResponse({'detail':'RESULT_WAIT_TIMEOUT','message_fa':'مهلت انتظار پایان یافت؛ پردازش لغو و فایل‌های موقت پاک می‌شوند.'},status_code=504,headers=headers)
                if await request.is_disconnected():return Response(status_code=499)
                try:state=await run_in_threadpool(local_jobs.authorized,id,actor)
                except ValueError as exc:
                    if str(exc)=='UPLOAD_NOT_FOUND':
                        return JSONResponse({'detail':'UPLOAD_ABORTED'},status_code=422,headers=headers)
                    raise
                if state['status']=='succeeded':
                    content,state=await run_in_threadpool(local_jobs.collect_html,id,actor)
                    selected=next((item for item in state.get('series',[]) if item['index']==state.get('selected_series_index')),None)
                    if selected:headers.update({'X-Selected-Series-Index':str(selected['index']),'X-Selected-Series-Slices':str(selected['slices'])})
                    headers['Content-Disposition']='attachment; filename="viewer.html"'
                    return Response(content,media_type='text/html',headers=headers)
                if state['status']=='failed':
                    error={'detail':state.get('safe_error') or 'LOCAL_PROCESSING_FAILED'}
                    if error['detail']=='ITEM_NOT_ELIGIBLE':
                        error['message_fa']='سری انتخاب‌شده قابل پردازش نیست. انتخاب خودکار (-1) را وارد کنید و دوباره ارسال کنید.'
                    return JSONResponse(error,status_code=422,headers=headers)
                await asyncio.sleep(.25)
        finally:
            await run_in_threadpool(local_jobs.discard,id,actor)
            await file.close()

    @app.get('/v1/scans/{id}')
    def scan_status(id:str,actor=Depends(authenticate)):
        with repo.session() as db:
            scan=db.get(Scan,id)
            if not scan or scan.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            return {'scan_id':id,'status':scan.status,'counters':scan.counters,'completed_at':scan.completed_at,'safe_error':scan.safe_error,'scope':{'prefix':scan.requested_scope['prefix'],'max_objects':scan.requested_scope['max_objects']}}

    @app.get('/v1/scans/{id}/items')
    def scan_items(id:str,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),actor=Depends(authenticate)):
        with repo.session() as db:
            scan=db.get(Scan,id)
            if not scan or scan.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            rows=db.scalars(select(Series).join(ScanObject).where(ScanObject.scan_id==id).order_by(Series.id).offset(offset).limit(limit+1)).all()
            jobs=db.scalars(select(Job).where(Job.tenant_id==actor.tenant_id)).all()
            def associated(row):
                snapshot=db.get(ScanObject,row.scan_object_id).snapshot
                return [{'job_id':j.id,'series_item_id':row.id,'study_uid':row.study_uid,'series_uid':row.series_uid,'status':j.status,'stage':j.stage,'safe_error':j.safe_error,'configuration':j.config,'retryability':j.status in ('failed','cancelled') and not j.attempt_token and j.safe_error not in ('INPUT_CHANGED','INVALID_PIXEL_DATA','UNSUPPORTED_TRANSFER_SYNTAX')} for j in jobs if j.result_key==canonical_hash({'tenant_id':actor.tenant_id,'source_id':scan.source_id,'input_sha256':snapshot.get('sha256'),'study_uid':row.study_uid,'series_uid':row.series_uid,**{k:v for k,v in j.config.items() if k!='configuration_hash'}})]
            return {'items':[{'id':r.id,'study_uid':r.study_uid,'series_uid':r.series_uid,'modality':r.modality,'eligibility':r.eligibility,'safe_error':r.safe_error,'slices':r.geometry_summary.get('slices'),'warnings':r.warnings,'jobs':associated(r)} for r in rows[:limit]],'next_offset':offset+limit if len(rows)>limit else None}

    @app.post('/v1/batches',status_code=202)
    def start_batch(body:BatchRequest,actor=Depends(authenticate),key:str=Header(default='',alias='Idempotency-Key')):return service.create_batch(actor,body,key)

    @app.get('/v1/batches/{id}')
    def batch_status(id:str,actor=Depends(authenticate)):
        with repo.session() as db:
            batch=db.get(Batch,id)
            if not batch or batch.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            rows=db.execute(select(Job,BatchJob).join(BatchJob).where(BatchJob.batch_id==id)).all()
            statuses=['cancelled' if member.membership_status=='cancelled' else job.status for job,member in rows]
            counters={'pending':statuses.count('queued'),'running':sum(s in {'validating','running','exporting','uploading'} for s in statuses),**{s:statuses.count(s) for s in ['succeeded','failed','unsupported','cancelled']}}
            terminal=not counters['pending'] and not counters['running']
            return {'batch_id':id,'status':'completed' if terminal else batch.status,'terminal':terminal,'counters':counters,'jobs':[{'job_id':job.id,'series_item_id':job.series_item_id,'status':'cancelled' if member.membership_status=='cancelled' else job.status,'stage':job.stage,'safe_error':job.safe_error} for job,member in rows]}

    @app.post('/v1/batches/{id}/cancel',status_code=202)
    def cancel(id:str,actor=Depends(authenticate),key:str=Header(default='',alias='Idempotency-Key')):return service.cancel_batch(actor,id,key)

    @app.get('/v1/jobs/{id}')
    def job_status(id:str,actor=Depends(authenticate)):
        with repo.session() as db:
            job=db.get(Job,id)
            if not job or job.tenant_id!=actor.tenant_id:raise ValueError('ACCESS_DENIED')
            item=db.get(Series,job.series_item_id)
            return {'job_id':id,'series_item_id':item.id,'study_uid':item.study_uid,'series_uid':item.series_uid,'status':job.status,'stage':job.stage,'safe_error':job.safe_error,'retryability':job.status in ('failed','cancelled') and not job.attempt_token and job.safe_error not in ('INPUT_CHANGED','INVALID_PIXEL_DATA','UNSUPPORTED_TRANSFER_SYNTAX'),'configuration':job.config}

    @app.get('/v1/jobs/{id}/result')
    def result(id:str,actor=Depends(authenticate)):return get_result(repo,store,actor,id)

    @app.get('/v1/jobs/{id}/artifacts/{artifact_id}')
    def artifact(id:str,artifact_id:str,actor=Depends(authenticate)):
        get_result(repo,store,actor,id)
        with repo.session() as db:
            pointer=db.get(Job,id).artifacts.get(artifact_id)
            if not pointer:raise HTTPException(404,'ARTIFACT_NOT_FOUND')
        data=store.read_output(pointer['key'])
        import hashlib
        if len(data)!=pointer['size'] or hashlib.sha256(data).hexdigest()!=pointer['sha256']:raise ValueError('ARTIFACT_INVALID')
        return Response(data,media_type='application/dicom' if artifact_id=='seg' else 'application/octet-stream',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.post('/v1/jobs/{id}/retry',status_code=202)
    def retry(id:str,actor=Depends(authenticate),key:str=Header(default='',alias='Idempotency-Key')):return service.retry(actor,id,key)

    return app


def from_env():
    from app.config import Settings
    return create_app(Settings.from_env())
