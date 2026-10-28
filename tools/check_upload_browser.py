"""Verify live Swagger progress and automatic final-ZIP download in Firefox.

Uses synthetic DICOM and mocked inference in a temporary job directory.
"""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

from tools.test_uploads import UploadTests,synthetic_dicom_zip
from fastapi.testclient import TestClient
from app.main import create_app

TEST=r'''<script>(async()=>{const sleep=ms=>new Promise(r=>setTimeout(r,ms));try{
 const seen=new Set();let downloads=0,downloadUrl='';
 const observer=new MutationObserver(()=>{for(const item of document.querySelectorAll('.upload-transfer'))if(item.dataset.stage)seen.add(item.dataset.stage);});observer.observe(document.getElementById('uploadTransfers'),{subtree:true,attributes:true,childList:true});
 document.addEventListener('click',e=>{if(e.target instanceof HTMLAnchorElement&&/^ct-viewer-[a-f0-9]{32}\.zip$/.test(e.target.download)){downloads++;downloadUrl=e.target.href;}},true);
 const archive=await(await fetch('/fixture.zip')).blob();const body=new FormData();body.append('file',archive,'ct.zip');body.append('wait_timeout','15');body.append('series_index','-1');
 const response=await fetch('/v1/upload/',{method:'POST',headers:__UPLOAD_HEADERS__,body});
 if(response.status!==200)throw Error('Final ZIP upload failed: '+response.status);const receipt=await response.json();
 if(receipt.downloaded_file!=='ct-viewer-'+receipt.job_id+'.zip'||receipt.size_bytes<10000)throw Error('Download receipt missing');
 if(!/^[a-f0-9]{32}$/.test(response.headers.get('Idempotency-Key')||''))throw Error('Automatic request key missing');
 if(downloads!==1||!downloadUrl.startsWith('blob:'))throw Error('Final file was not automatically downloaded exactly once');
 const bytes=new Uint8Array(await(await fetch(downloadUrl)).arrayBuffer());if(bytes[0]!==80||bytes[1]!==75)throw Error('Downloaded file is not a ZIP');
 const external=await fetch('http://localhost:'+location.port+'/auth-probe',{headers:{Authorization:'Bearer example-test-token'}});
 if((await external.json()).hasAuthorization!==__EXPLICIT_AUTH__)throw Error('Default token origin scoping failed');
 if(!seen.has('running'))throw Error('Live processing stage was not shown');
 if(document.querySelector('.upload-transfer').dataset.state!=='succeeded')throw Error('Progress card did not finish');
 if(!document.querySelector('.upload-transfer').textContent.includes('سری ۱'))throw Error('Automatic series selection was not shown');
 if(location.pathname!=='/docs')throw Error('Upload unexpectedly navigated away from Swagger');
 await fetch('/report',{method:'POST',body:JSON.stringify({ok:true,autoAuthorization:__AUTO_AUTH__,automaticDownload:true,finalViewer:true,liveStages:[...seen],bytes:receipt.size_bytes,jobId:receipt.job_id})});
}catch(e){await fetch('/report',{method:'POST',body:JSON.stringify({ok:false,error:String(e)})});}})();</script>'''


def main():
    fixture=UploadTests();fixture.setUp()
    auto_authorize='--auto-authorize' in sys.argv
    if auto_authorize:
        fixture.client.close()
        fixture.settings=replace(fixture.settings,swagger_auto_authorize=True)
        fixture.client=TestClient(create_app(fixture.settings,fixture.repository,fixture.store))
        fixture.addCleanup(fixture.client.close)
    completed=threading.Event();report={}
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as target:
        for i,slices in enumerate((3,5)):
            with zipfile.ZipFile(io.BytesIO(synthetic_dicom_zip(slices))) as source:
                for name in source.namelist():target.writestr(f'{i}/'+name,source.read(name))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.forward('GET')
        def do_POST(self):self.forward('POST')
        def do_OPTIONS(self):
            self.send_response(204);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Access-Control-Allow-Headers','Authorization');self.end_headers()
        def forward(self,method):
            payload=self.rfile.read(int(self.headers.get('Content-Length',0)))
            if self.path=='/report':
                report.update(json.loads(payload));self.send_response(200);self.end_headers();completed.set();return
            if self.path=='/fixture.zip':
                self.send_response(200);self.send_header('Content-Type','application/zip');self.end_headers();self.wfile.write(archive.getvalue());return
            if self.path=='/auth-probe':
                content=json.dumps({'hasAuthorization':bool(self.headers.get('Authorization'))}).encode()
                self.send_response(200);self.send_header('Access-Control-Allow-Origin','*');self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content);return
            response=fixture.client.request(method,self.path,headers=dict(self.headers),content=payload,follow_redirects=False)
            content=response.content
            if self.path=='/docs':
                document=content.decode()
                # Exercise the real app hook without relying on CDN availability.
                document=re.sub(r'<script[^>]*src=[^>]*>.*?</script>','',document,flags=re.S)
                document=re.sub(r'<script>(.*?)</script>',lambda m:'' if 'SwaggerUIBundle' in m[1] else m[0],document,flags=re.S)
                test=TEST.replace('__UPLOAD_HEADERS__',json.dumps({} if auto_authorize else {'Authorization':'Bearer example-test-token'})).replace('__EXPLICIT_AUTH__',json.dumps(not auto_authorize)).replace('__AUTO_AUTH__',json.dumps(auto_authorize))
                content=document.replace('</body>',test+'</body>').encode()
            self.send_response(response.status_code)
            for key,value in response.headers.items():
                if key.lower() not in ('content-length','transfer-encoding','connection'):self.send_header(key,value)
            self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
    try:
        with fixture.local_worker(model_delay=1.6),ThreadingHTTPServer(('127.0.0.1',0),Handler) as server,tempfile.TemporaryDirectory(prefix='upload-browser-',dir='work') as directory:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            profile=Path(directory).resolve()/'profile';profile.mkdir()
            downloads=Path(directory).resolve()/'downloads';downloads.mkdir()
            # This isolated synthetic test must not wait on external download
            # reputation checks. The user's browser preferences are untouched.
            (profile/'user.js').write_text('user_pref("browser.safebrowsing.downloads.enabled",false);\nuser_pref("browser.safebrowsing.downloads.remote.enabled",false);\nuser_pref("browser.download.folderList",2);\nuser_pref("browser.download.dir",'+json.dumps(str(downloads))+');\nuser_pref("browser.helperApps.neverAsk.saveToDisk","application/zip,application/octet-stream");\nuser_pref("browser.download.useDownloadDir",true);\nuser_pref("browser.download.always_ask_before_handling_new_types",false);\n')
            with (Path(directory)/'firefox.log').open('w') as log:
                child=subprocess.Popen(['firefox','--headless','--no-remote','--profile',str(profile),f'http://127.0.0.1:{server.server_port}/docs'],stdout=log,stderr=log,start_new_session=True)
                try:
                    if not completed.wait(35):raise SystemExit('UPLOAD_BROWSER_TEST_TIMEOUT')
                    if not report.get('ok'):raise SystemExit('UPLOAD_BROWSER_TEST_FAILED: '+report.get('error','unknown'))
                    target=downloads/('ct-viewer-'+report['jobId']+'.zip');expected=fixture.produced[report['jobId']]['zip']
                    for _ in range(200):
                        if target.exists() and target.stat().st_size==len(expected):break
                        time.sleep(.05)
                    if not target.exists() or hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(expected).digest():
                        print('DOWNLOAD_FILES',[(p.name,p.stat().st_size) for p in downloads.iterdir()],'expected_bytes',len(expected))
                        raise SystemExit('AUTOMATIC_DOWNLOADED_FILE_MISMATCH')
                    fixture.assert_clean()
                    print('FINAL_ZIP_AUTODOWNLOAD_BROWSER_OK',json.dumps(report))
                finally:
                    os.killpg(child.pid,signal.SIGTERM)
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
                    server.shutdown()
        fixture.tearDown()
    finally:fixture.doCleanups()


if __name__=='__main__':main()
