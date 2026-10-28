"""Open the real Swagger upload form in Firefox and check it stays responsive.

Swagger's CDN assets are fetched once and served locally for the browser run.
No uploads, inference, credentials or running API service are needed.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading

import httpx
from fastapi.testclient import TestClient

from app.main import create_app


TEST = '''<script>(async()=>{
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const report=data=>fetch('/browser-report',{method:'POST',body:JSON.stringify(data)});
async function waitFor(find,attempts=80){for(let i=0;i<attempts;i++){const value=find();if(value)return value;await sleep(100);}throw Error('Swagger control did not appear');}
try{
 const path=await waitFor(()=>[...document.querySelectorAll('.opblock-summary-path')].find(node=>node.textContent.trim()==='/v1/upload/'));
 const operation=path.closest('.opblock');
 await report({stage:'opening-upload'});
 operation.querySelector('.opblock-summary-control').click();
 const tryButton=await waitFor(()=>operation.querySelector('.try-out__btn'));
 await sleep(300);
 tryButton.click();
 const file=await waitFor(()=>operation.querySelector('input[type="file"]'));
 if(file.disabled)throw Error('ZIP file control is disabled');
 const structures=operation.querySelector('input[value="all"]');
 if(!structures)throw Error('Structures control missing');
 structures.value='liver';structures.dispatchEvent(new Event('input',{bubbles:true}));
 await sleep(300);
 if(!operation.querySelector('.execute'))throw Error('Execute button missing');
 operation.querySelector('.opblock-summary-control').click();
 await sleep(300);
 operation.querySelector('.opblock-summary-control').click();
 await waitFor(()=>operation.querySelector('.opblock-body'));
 await sleep(300);
 let downloadedBytes=0;
 if(__DOWNLOAD_SAVED__){
  const savedPath=await waitFor(()=>[...document.querySelectorAll('.opblock-summary-path')].find(node=>node.textContent.trim()==='/v1/exports/dicom-viewer.zip'));
  const saved=savedPath.closest('.opblock');
  await report({stage:'opening-saved-download',operationId:saved.id,links:[...saved.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'))});
  document.querySelector('a[href="#/Saved%20results/download_saved_dicom_viewer"]').click();
  const trySaved=await waitFor(()=>saved.querySelector('.try-out__btn'));
  trySaved.click();
  await waitFor(()=>saved.querySelector('.execute'));
  await report({stage:'downloading-saved-viewer'});
  saved.querySelector('.execute').click();
  const link=await waitFor(()=>saved.querySelector('a[download="ct-viewer-saved-dicom.zip"]'),400);
  const blob=await(await fetch(link.href)).blob();
  downloadedBytes=blob.size;
  if(downloadedBytes<10000)throw Error('Saved viewer download is incomplete');
  await sleep(300);
 }
 await report({done:true,ok:true,stage:'upload-form-responsive',downloadedBytes});
}catch(error){await report({done:true,ok:false,error:String(error)});}
})();</script>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',help='Check a running API instead of an isolated test app.')
    parser.add_argument('--download-saved',action='store_true',help='Also execute the saved-viewer download in an automatically authorized live Swagger UI.')
    args=parser.parse_args()
    if args.download_saved and not args.base_url:parser.error('--download-saved requires --base-url')
    assets={}
    with httpx.Client(timeout=20,trust_env=False) as remote:
        for name in ('swagger-ui-bundle.js','swagger-ui.css'):
            response=remote.get('https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/'+name)
            response.raise_for_status()
            assets['/swagger-assets/'+name]=response.content
    completed=threading.Event();report={};sample_requests=[]
    source=httpx.Client(base_url=args.base_url,timeout=10,trust_env=False) if args.base_url else TestClient(create_app(None))
    with source as client:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass

            def do_POST(self):
                if self.path!='/browser-report':self.send_error(404);return
                report.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.end_headers()
                if report.get('done'):completed.set()

            def do_GET(self):
                if self.path.startswith('/docs/samples/'):
                    sample_requests.append(self.path)
                if self.path in assets:
                    content=assets[self.path]
                    media='text/javascript' if self.path.endswith('.js') else 'text/css'
                    status=200
                else:
                    response=client.get(self.path,headers={name:self.headers[name] for name in ('Authorization','Range') if name in self.headers})
                    content=response.content;status=response.status_code
                    media=response.headers.get('content-type','application/octet-stream')
                    if self.path=='/docs':
                        document=content.decode().replace('https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/','/swagger-assets/')
                        probe=TEST.replace('__DOWNLOAD_SAVED__',json.dumps(args.download_saved))
                        content=document.replace('</body>',probe+'</body>').encode()
                self.send_response(status);self.send_header('Content-Type',media)
                if self.path not in assets:
                    for name in ('Content-Disposition','Cache-Control','Content-Range'):
                        if name in response.headers:self.send_header(name,response.headers[name])
                self.send_header('Content-Length',str(len(content)));self.end_headers()
                self.wfile.write(content)

        with ThreadingHTTPServer(('127.0.0.1',0),Handler) as server,tempfile.TemporaryDirectory(prefix='swagger-browser-',dir='work') as folder:
            threading.Thread(target=server.serve_forever,daemon=True).start()
            profile=Path(folder).resolve()/'profile';profile.mkdir()
            with (Path(folder)/'firefox.log').open('w') as log:
                child=subprocess.Popen(['firefox','--headless','--no-remote','--profile',str(profile),
                                        f'http://127.0.0.1:{server.server_port}/docs'],
                                       stdout=log,stderr=log,start_new_session=True)
                try:
                    if not completed.wait(50 if args.download_saved else 25):
                        raise SystemExit('SWAGGER_BROWSER_TIMEOUT '+json.dumps({'last_report':report,'sample_requests':sample_requests}))
                    if not report.get('ok'):raise SystemExit('SWAGGER_BROWSER_FAILED '+json.dumps(report))
                    if sample_requests:raise SystemExit('Swagger eagerly loaded the full HTML example: '+json.dumps(sample_requests))
                    print('SWAGGER_UPLOAD_FORM_BROWSER_OK',json.dumps(report))
                finally:
                    os.killpg(child.pid,signal.SIGTERM)
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
                    server.shutdown()


if __name__=='__main__':main()
