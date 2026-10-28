"""Local ZIP segmentation uses disk and GPU only, including during remote outages."""
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import subprocess
import sys
import threading
import time
import unittest
import zipfile

from fastapi.testclient import TestClient
import numpy as np
from pydicom.dataset import FileDataset,FileMetaDataset
from pydicom.uid import CTImageStorage,ExplicitVRLittleEndian,generate_uid
from unittest.mock import AsyncMock,Mock,patch
import nibabel as nib

from app.config import Settings
from app.local_jobs import LocalJobs
from app.worker.local_worker import process
from app.main import create_app
from app.contracts import ActorContext
from app.export.preview import generate_preview


def synthetic_dicom_zip(slices=3):
    buffer=io.BytesIO()
    study,series,frame=generate_uid(),generate_uid(),generate_uid()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for index in range(slices):
            sop=generate_uid()
            meta=FileMetaDataset()
            meta.MediaStorageSOPClassUID=CTImageStorage
            meta.MediaStorageSOPInstanceUID=sop
            meta.TransferSyntaxUID=ExplicitVRLittleEndian
            ds=FileDataset(None,{},file_meta=meta,preamble=b'\0'*128)
            ds.SOPClassUID=CTImageStorage;ds.SOPInstanceUID=sop
            ds.StudyInstanceUID=study;ds.SeriesInstanceUID=series;ds.FrameOfReferenceUID=frame
            ds.PatientID='SYNTHETIC-UPLOAD-TEST';ds.Modality='CT'
            ds.Rows=4;ds.Columns=4;ds.SamplesPerPixel=1;ds.PhotometricInterpretation='MONOCHROME2'
            ds.BitsAllocated=16;ds.BitsStored=16;ds.HighBit=15;ds.PixelRepresentation=1
            ds.ImageOrientationPatient=[1,0,0,0,1,0];ds.ImagePositionPatient=[0,0,index*2]
            ds.PixelSpacing=[1,1];ds.RescaleSlope=1;ds.RescaleIntercept=0
            ds.PixelData=np.full((4,4),40+index,dtype='<i2').tobytes()
            encoded=io.BytesIO();ds.save_as(encoded,enforce_file_format=True)
            archive.writestr(f'series/slice{index}.dcm',encoded.getvalue())
    return buffer.getvalue()


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root=Path(self.directory.name)
        self.settings=Settings(database_url='postgresql+psycopg://unused/unused',redis_url='redis://unused',
                               service_token='example-test-token',endpoint='http://unused',access_key='test',secret_key='test',
                               input_bucket='unused-input',output_bucket='unused-output',tenant_id='test-tenant',source_id='source',
                               allowed_prefix='existing-clinic/',scratch=root/'work')
        self.repository=Mock();self.store=Mock()
        self.client=TestClient(create_app(self.settings,self.repository,self.store))
        self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer example-test-token','X-Tenant-Id':'test-tenant','X-Actor-Id':'tester',
                      'X-Operator':'true','Idempotency-Key':'upload-test-1'}
        self.payload=synthetic_dicom_zip()
        self.jobs=LocalJobs(self.settings)

    def tearDown(self):
        self.assertEqual(self.repository.mock_calls,[],'Local uploads must never use the database')
        self.assertEqual(self.store.mock_calls,[],'Local uploads must never use MinIO')

    def upload(self,data=None,headers=None,path='/v1/upload/',**options):
        return self.client.post(path,headers=self.headers if headers is None else headers,
            data={'wait_timeout':'10',**options},files={'file':('scan.zip',self.payload if data is None else data,'application/zip')})

    def assert_clean(self):
        self.assertEqual(list(self.jobs.root.glob('*/')),[],'Inputs, masks and HTML must all be deleted')

    @contextmanager
    def local_worker(self,model_delay=.12,model_error=None):
        stopped=threading.Event();errors=[]
        self.started=threading.Event();self.produced={};self.model_calls=0
        def inference(volume,config,workdir,**kwargs):
            self.model_calls+=1;self.assertTrue(kwargs['lease_guard']());self.started.set()
            time.sleep(model_delay)
            if model_error:raise ValueError(model_error)
            workdir.mkdir(parents=True,exist_ok=True)
            nib.save(nib.Nifti1Image(volume.array,volume.affine_ras),workdir/'native.nii.gz')
            return nib.Nifti1Image(np.full(volume.array.shape,5,dtype=np.uint8),volume.affine_ras)
        def preview(*args,**kwargs):
            result=generate_preview(*args,**kwargs)
            directory=Path(args[2]);id=directory.parent.name
            self.produced[id]={'zip':(directory/result['viewer']).read_bytes(),
                'files':sorted(p.name for p in directory.iterdir())}
            return result
        def run():
            try:
                while not stopped.is_set():
                    self.jobs.reap_ephemeral()
                    for path in list(self.jobs.root.glob('*/status.json')):
                        state=self.jobs.claim(path.parent.name)
                        if state:process(self.jobs,state)
                    stopped.wait(.01)
            except Exception as exc:errors.append(exc)
        with patch('app.worker.local_worker.run_segmentation',side_effect=inference),patch('app.worker.local_worker.generate_preview',side_effect=preview),patch('app.worker.local_worker.create_portable_package') as archive,patch('app.worker.local_worker.export_labelmap_seg') as dicom:
            worker=threading.Thread(target=run,daemon=True);worker.start()
            try:yield
            finally:
                stopped.set();worker.join(10)
                self.assertFalse(worker.is_alive(),'Worker failed to finish')
                self.assertEqual(errors,[])
                archive.assert_not_called();dicom.assert_not_called()

    def test_final_zip_is_identical_to_generated_file_and_nothing_is_retained(self):
        with self.local_worker():response=self.upload()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'application/zip')
        id=response.headers['x-job-id']
        self.assertEqual(response.headers['content-disposition'],f'attachment; filename="ct-viewer-{id}.zip"')
        self.assertEqual(response.content,self.produced[id]['zip'])
        self.assertEqual(self.produced[id]['files'],[f'ct-viewer-{id}.zip','masks.nii.gz'])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIsNone(archive.testzip())
            data=json.loads(archive.read('viewer/data/manifest.json'))
            html=archive.read('viewer/index.html').decode()
            self.assertEqual(len(data['labels']),117)
            self.assertEqual(data['present'],[5])
            self.assertIn('themePicker',html)
            self.assertNotIn('data:image',html)
            self.assertNotIn('viewer/viewer.html',archive.namelist())
            self.assertIn('--color-primary: #0065f8',archive.read('viewer/assets/viewer.css').decode())
            self.assertNotIn('example-test-token',str(data))
        self.assert_clean()
        self.assertEqual(self.client.get('/v1/uploads',headers=self.headers).json(),{'items':[],'retention':False})
        self.assertEqual(self.client.get('/v1/uploads/'+id,headers=self.headers).status_code,404)
        self.assertEqual(self.client.get('/v1/uploads/'+id+'/result',headers=self.headers).status_code,404)

    def test_token_only_and_plural_alias_return_zip_without_a_cache(self):
        headers={'Authorization':self.headers['Authorization']}
        with self.local_worker():
            first=self.upload(headers=headers)
            second=self.upload(headers=headers,path='/v1/uploads')
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(self.model_calls,2)
        self.assertEqual(len(first.headers['Idempotency-Key']),32)
        self.assertNotEqual(first.headers['Idempotency-Key'],second.headers['Idempotency-Key'])
        self.assertNotEqual(first.headers['X-Job-Id'],second.headers['X-Job-Id'])
        self.assert_clean()

    def test_saved_viewer_download_is_authenticated_and_returns_original_bytes(self):
        path=Path(self.directory.name)/'dicom-viewer.zip'
        content=b'PK\x03\x04saved-viewer-test'
        path.write_bytes(content)
        with patch('app.main.SAVED_VIEWER_PATH',path):
            url='/v1/exports/dicom-viewer.zip'
            self.assertEqual(self.client.get(url).status_code,401)
            self.assertEqual(self.client.get(url,headers={'Authorization':'Bearer wrong-token'}).status_code,401)
            self.assertEqual(self.client.get(url,headers={**self.headers,'X-Tenant-Id':'other'}).status_code,403)
            response=self.client.get(url,headers={'Authorization':self.headers['Authorization']})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,content)
            self.assertEqual(response.headers['content-type'],'application/zip')
            self.assertEqual(response.headers['content-disposition'],'attachment; filename="ct-viewer-saved-dicom.zip"')
            self.assertEqual(response.headers['cache-control'],'no-store')
            path.unlink()
            self.assertEqual(self.client.get(url,headers=self.headers).status_code,404)

    def test_authentication_and_explicit_scope_restrictions(self):
        for changed,status in [({},401),({'Authorization':'Bearer wrong-token'},401),
                ({**self.headers,'X-Tenant-Id':'other'},403),({**self.headers,'X-Actor-Id':'bad actor'},403),
                ({**self.headers,'X-Operator':'false'},403)]:
            self.assertEqual(self.upload(headers=changed).status_code,status)
        self.assert_clean()

    def test_swagger_auto_authorization_is_optional_and_does_not_disable_api_auth(self):
        for enabled in (False,True):
            with TestClient(create_app(replace(self.settings,swagger_auto_authorize=enabled),self.repository,self.store)) as client:
                docs=client.get('/docs')
                self.assertEqual(docs.headers['cache-control'],'no-store')
                self.assertEqual('onComplete: () => window.segSwaggerAuthorize(ui)' in docs.text,enabled)
                self.assertEqual(self.settings.service_token in docs.text,enabled)
                self.assertNotIn(self.settings.service_token,client.get('/openapi.json').text)
                self.assertEqual(client.get('/v1/uploads/capabilities').status_code,401)
                self.assertEqual(client.get('/v1/uploads/capabilities',headers=self.headers).status_code,200)

    def test_swagger_token_cannot_terminate_the_inline_script(self):
        token='example-test-token</script><script>untrusted()</script>'
        with TestClient(create_app(replace(self.settings,service_token=token,swagger_auto_authorize=True),self.repository,self.store)) as client:
            document=client.get('/docs').text
        self.assertNotIn(token,document)
        self.assertIn('example-test-token\\u003c/script>',document)

    def test_invalid_empty_oversized_and_unsupported_inputs_leave_no_files(self):
        self.assertEqual(self.upload(b'bad').status_code,422)
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w'):pass
        self.assertEqual(self.upload(archive.getvalue()).json(),{'detail':'EMPTY_ZIP'})
        with TestClient(create_app(replace(self.settings,max_archive=10),self.repository,self.store)) as client:
            response=client.post('/v1/upload/',headers=self.headers,files={'file':('scan.zip',self.payload)})
        self.assertEqual(response.status_code,413)
        with self.local_worker():response=self.upload(synthetic_dicom_zip(1))
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['detail'],'NO_ELIGIBLE_CT_SERIES')
        self.assert_clean()

    def test_auto_selection_and_invalid_explicit_selection_are_cleaned(self):
        archive=io.BytesIO()
        with zipfile.ZipFile(archive,'w') as target:
            for index,slices in enumerate((1,5,3)):
                with zipfile.ZipFile(io.BytesIO(synthetic_dicom_zip(slices))) as source:
                    for name in source.namelist():target.writestr(f'{index}/'+name,source.read(name))
        payload=archive.getvalue()
        with self.local_worker():
            invalid=self.upload(payload,series_index='0')
            self.assertEqual(invalid.status_code,422)
            self.assertEqual(set(invalid.json()),{'detail','message_fa'})
            self.assertEqual(invalid.json()['detail'],'ITEM_NOT_ELIGIBLE')
            self.assert_clean()
            automatic=self.upload(payload,series_index='-1')
        self.assertEqual(automatic.status_code,200,automatic.text)
        self.assertEqual(automatic.headers['X-Selected-Series-Index'],'1')
        self.assertEqual(automatic.headers['X-Selected-Series-Slices'],'5')
        self.assert_clean()

    def test_model_failure_is_cleaned(self):
        with self.local_worker(model_error='MODEL_FAILED'):response=self.upload()
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json(),{'detail':'MODEL_FAILED'})
        self.assert_clean()

    def test_live_progress_is_authenticated_and_disappears_after_delivery(self):
        response=[]
        with self.local_worker(model_delay=.6):
            upload=threading.Thread(target=lambda:response.append(self.upload()))
            upload.start();self.assertTrue(self.started.wait(5))
            progress=self.client.get('/v1/upload/progress',headers=self.headers)
            self.assertEqual(progress.status_code,200)
            self.assertEqual(progress.json()['stage'],'running')
            self.assertNotIn('viewer_url',progress.json())
            self.assertEqual(self.client.get('/v1/upload/progress').status_code,401)
            self.assertEqual(self.client.get('/v1/upload/progress',headers={**self.headers,'X-Tenant-Id':'other'}).status_code,403)
            upload.join(10)
        self.assertEqual(response[0].status_code,200,response[0].text)
        self.assert_clean()

    def test_queued_timeout_deletes_input_immediately(self):
        response=self.upload(wait_timeout='1')
        self.assertEqual(response.status_code,504,response.text)
        self.assertEqual(response.json()['detail'],'RESULT_WAIT_TIMEOUT')
        self.assert_clean()

    def test_running_timeout_cleans_when_model_exits(self):
        with self.local_worker(model_delay=1.3):
            response=self.upload(wait_timeout='1')
            self.assertEqual(response.status_code,504,response.text)
        self.assert_clean()

    def test_disconnected_request_cleans_queued_and_running_inputs(self):
        with patch('starlette.requests.Request.is_disconnected',new=AsyncMock(return_value=True)):
            self.assertEqual(self.upload().status_code,499)
        self.assert_clean()
        async def disconnected(request):return self.started.is_set()
        with self.local_worker(model_delay=.4),patch('starlette.requests.Request.is_disconnected',new=disconnected):
            self.assertEqual(self.upload().status_code,499)
        self.assert_clean()

    def test_active_duplicate_is_rejected_and_reaper_removes_expired_or_orphaned_work(self):
        actor=ActorContext(self.settings.tenant_id,'tester',True)
        first=self.jobs.submit(io.BytesIO(self.payload),actor,'upload-test-1',['all'],'standard',False,True,None,True,3600)
        directory=self.jobs.directory(first['job_id'])
        duplicate=self.upload()
        self.assertEqual(duplicate.status_code,409)
        self.assertEqual(duplicate.json()['detail'],'UPLOAD_IN_PROGRESS')
        self.assertEqual((directory/'input.zip').read_bytes(),self.payload)
        state=self.jobs.load(first['job_id']);self.jobs.save(state,expires_at=0)
        self.jobs.reap_ephemeral();self.assert_clean()
        self.jobs.submit(io.BytesIO(self.payload),actor,'orphan',['all'],'standard',False,True,None,True,3600)
        for name in ('.incoming-interrupted','e'*32):
            path=self.jobs.root/name;path.mkdir();(path/'input.zip').write_bytes(b'interrupted')
        with TestClient(create_app(self.settings,self.repository,self.store)):self.assert_clean()
        self.assertIsNone(self.jobs.claim('f'*32))
        self.assert_clean()

    def test_swagger_only_exposes_final_zip_and_needed_inputs(self):
        schema=self.client.get('/openapi.json').json()
        operation=schema['paths']['/v1/upload/']['post']
        self.assertEqual(operation['security'],[{'ServiceToken':[]}])
        self.assertNotIn('/v1/upload/progress',schema['paths'])
        self.assertNotIn('/v1/uploads',schema['paths'])
        self.assertNotIn('/v1/uploads/{id}/result',schema['paths'])
        removed={'Idempotency-Key','X-Tenant-Id','X-Actor-Id','X-Operator'}
        self.assertFalse(removed & {p['name'] for p in operation.get('parameters',[])})
        self.assertEqual(operation['responses']['200']['content']['application/zip']['schema']['format'],'binary')
        form=operation['requestBody']['content']['multipart/form-data']['schema']['$ref'].split('/')[-1]
        properties=schema['components']['schemas'][form]['properties']
        self.assertNotIn('reuse_segmentation',properties)
        self.assertEqual(properties['series_index']['default'],-1)
        capabilities=self.client.get('/v1/uploads/capabilities',headers=self.headers).json()
        self.assertFalse(capabilities['retention'])
        self.assertEqual(capabilities['output'],'ct-viewer-<result-id>.zip')

    def test_model_child_temporary_files_stay_inside_request_cleanup_boundary(self):
        from app.inference.totalseg_adapter import run_segmentation
        volume=SimpleNamespace(array=np.zeros((2,2,2),dtype=np.int16),affine_ras=np.eye(4))
        workdir=Path(self.directory.name)/'request/model'
        config={'model_cache':str(Path(self.directory.name)/'models'),'checkpoint_digest':'test','multilabel_output':True}
        child=Mock(returncode=0);child.poll.return_value=0
        aliases=[]
        def start(*args,**kwargs):
            for name in ('TMPDIR','TMP','TEMP'):
                temporary=Path(kwargs['env'][name])
                self.assertEqual(temporary.resolve(),workdir/'tmp')
                self.assertTrue(temporary.is_symlink())
                self.assertLess(len(str(temporary)),40)
                aliases.append(temporary)
            nib.save(nib.Nifti1Image(np.zeros((2,2,2),dtype=np.uint8),np.eye(4)),workdir/'model-labels.nii.gz')
            return child
        with patch('app.inference.totalseg_adapter.verify_model_cache'),patch('app.inference.totalseg_adapter.subprocess.Popen',side_effect=start):
            output=run_segmentation(volume,config,workdir)
        self.assertEqual(output.shape,(2,2,2))
        self.assertTrue(all(not alias.parent.exists() for alias in aliases))

    def test_long_request_paths_support_real_multiprocessing_and_cleanup_on_failure(self):
        from app.inference.totalseg_adapter import model_temporary_directory
        workdir=Path(self.directory.name)/('request-'+'a'*120)/'model'
        workdir.mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError,'test cleanup'):
            with model_temporary_directory(workdir) as temporary:
                # This was failing in nnUNet's Manager with AF_UNIX path too long.
                result=subprocess.run([sys.executable,'-c',
                    'import multiprocessing; m=multiprocessing.Manager(); m.shutdown()'],
                    env=dict(os.environ,TMPDIR=temporary),capture_output=True,text=True,timeout=15)
                self.assertEqual(result.returncode,0,result.stderr)
                alias=Path(temporary)
                (alias/'temporary-model-data').write_bytes(b'test')
                self.assertTrue((workdir/'tmp/temporary-model-data').is_file())
                raise RuntimeError('test cleanup')
        self.assertFalse(alias.parent.exists())


if __name__=='__main__':unittest.main()
