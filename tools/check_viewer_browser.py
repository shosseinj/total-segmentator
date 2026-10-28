"""Exercise real region checkbox and canvas behavior in headless Firefox."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import shutil
import zipfile
import subprocess
import tempfile
import threading

TEST='''<script>
(async()=>{const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));try{
for(let n=0;n<160&&(!window.ViewerExtension||!labelPixels);n++)await sleep(50);if(!window.ViewerExtension||!labelPixels)throw Error('CT label image did not load');
if(selected.size!==data.present.length)throw Error('Fresh viewer did not select every detected region');
const bodyStyle=getComputedStyle(document.body);if(bodyStyle.backgroundColor!=='rgb(249, 250, 251)')throw Error('Default theme was not applied');
const picker=byId('themePicker');for(const [value,color] of [['isarmed','rgb(53, 111, 124)'],['lungimed','rgb(86, 122, 106)'],['','rgb(0, 101, 248)']]){picker.value=value;picker.dispatchEvent(new Event('change'));await sleep(220);if(getComputedStyle(byId('selectAll')).backgroundColor!==color)throw Error('Theme palette did not switch: '+value);}
const onSlice=[...new Set(Array.from(labelPixels).filter((n,i)=>i%4===0&&n>0))];if(onSlice.length<2)throw Error('Need two regions on test slice');
document.getElementById('selectNone').click();if(ctx.getImageData(0,0,canvas.width,canvas.height).data.some((n,i)=>i%4===3&&n))throw Error('Clear selection left mask pixels');
const chosen=onSlice.slice(0,2);for(const n of chosen)document.querySelector('input[data-region="'+n+'"]').click();
if(selected.size!==2)throw Error('Multiple checkboxes did not remain selected');
let output=ctx.getImageData(0,0,canvas.width,canvas.height).data;let colored=0;
for(let i=0;i<output.length;i+=4){const n=labelPixels[i],visible=chosen.includes(n);if((output[i+3]>0)!==visible)throw Error('Region filter leaked or hid pixels');if(visible){colored++;const c=data.colors[n];if(output[i]!==c[0]||output[i+1]!==c[1]||output[i+2]!==c[2])throw Error('Region palette mismatch');}}
document.querySelector('input[data-region="'+chosen[0]+'"]').click();if(selected.size!==1||!selected.has(chosen[1]))throw Error('Unchecking one region changed another');
document.getElementById('selectAll').click();if(selected.size!==data.present.length)throw Error('Select all failed');
document.getElementById('search').value='kidney';document.getElementById('search').dispatchEvent(new Event('input'));
if(rows.some(row=>!row.row.hidden&&!row.name.includes('kidney')))throw Error('Region search failed');
document.getElementById('search').value='کلیه';document.getElementById('search').dispatchEvent(new Event('input'));
if(!rows.some(row=>!row.row.hidden)||rows.some(row=>!row.row.hidden&&!row.name.includes('کلیه')))throw Error('Persian search failed');
document.getElementById('search').value='';document.getElementById('search').dispatchEvent(new Event('input'));
const originalCT=ctCtx.getImageData(0,0,ct.width,ct.height).data;
const preset=byId('preset');preset.value='lung';preset.dispatchEvent(new Event('change'));
const lungCT=ctCtx.getImageData(0,0,ct.width,ct.height).data;let changed=false;
for(let i=0;i<rawPixels.length;i+=4){const hu=(rawPixels[i]+256*rawPixels[i+1])*data.hu_step+data.hu_offset;const expected=Math.round(Math.max(0,Math.min(1,(hu+600+750)/1500))*255);if(lungCT[i]!==expected)throw Error('HU window calculation failed');if(lungCT[i]!==originalCT[i])changed=true;}
if(!changed)throw Error('Window preset did not change CT');
byId('invert').click();const inverted=ctCtx.getImageData(0,0,ct.width,ct.height).data;for(let i=0;i<inverted.length;i+=4)if(inverted[i]!==255-lungCT[i])throw Error('CT inversion failed');
byId('invert').click();byId('contour').click();output=ctx.getImageData(0,0,canvas.width,canvas.height).data;let interiorHidden=0,boundaryKept=0;
for(let i=0;i<output.length;i+=4){const n=labelPixels[i],p=i/4,x=p%canvas.width,y=Math.floor(p/canvas.width),edge=x===0||x===canvas.width-1||y===0||y===canvas.height-1||labelPixels[i-4]!==n||labelPixels[i+4]!==n||labelPixels[i-4*canvas.width]!==n||labelPixels[i+4*canvas.width]!==n;const expected=n>0&&selected.has(n)&&edge;if((output[i+3]>0)!==expected)throw Error('Contour filter failed');if(n&&!edge)interiorHidden++;if(expected)boundaryKept++;}
if(!interiorHidden||!boundaryKept)throw Error('Contour test lacked interior or edge pixels');byId('contour').click();
byId('zoomIn').click();if(zoom<=1||!world.style.transform.includes('scale'))throw Error('Zoom failed');
byId('reset').click();if(zoom!==1||panX!==0||panY!==0)throw Error('Reset view failed');
byId('rulerTool').click();const bounds=world.getBoundingClientRect();const y=bounds.top+bounds.height/2;stage.dispatchEvent(new PointerEvent('pointerdown',{clientX:bounds.left+bounds.width*.25,clientY:y,button:0}));stage.dispatchEvent(new PointerEvent('pointerdown',{clientX:bounds.left+bounds.width*.75,clientY:y,button:0}));
if(rulerPoints.length!==2)throw Error('Ruler did not capture two points');const distance=Math.hypot((rulerPoints[0].x-rulerPoints[1].x)*data.spacing[0],(rulerPoints[0].y-rulerPoints[1].y)*data.spacing[1]);if(Math.abs(distance-ct.width*data.spacing[0]/2)>.01||!byId('rulerValue').textContent.includes(fa.format(distance)))throw Error('Ruler spacing calculation failed');
byId('clearRuler').click();if(rulerPoints.length||byId('rulerValue').textContent)throw Error('Clear ruler failed');
const beforeCine=index;byId('play').click();for(let n=0;n<30&&index===beforeCine;n++)await sleep(50);byId('play').click();if(index===beforeCine||timer)throw Error('Cine playback failed');for(let n=0;n<100&&loading;n++)await sleep(50);
const previousPixels=labelPixels;document.getElementById(index<data.frames.length-1?'next':'previous').click();
for(let n=0;n<100&&labelPixels===previousPixels;n++)await sleep(50);if(labelPixels===previousPixels)throw Error('Slice navigation did not update mask');
byId('tabMeasurements').click();for(let n=0;n<200&&!Object.keys(ViewerExtension.state.measurements.edited).length;n++)await sleep(50);if(!Object.keys(ViewerExtension.state.measurements.edited).length)throw Error('Lazy measurements did not load');
byId('modeMpr').click();for(let n=0;n<200&&ViewerExtension.state.planes.length!==3;n++)await sleep(50);if(ViewerExtension.state.planes.length!==3)throw Error('Lazy MPR did not load');
byId('tabEditing').click();for(let n=0;n<200&&!byId('editStatus').textContent.includes('آماده است');n++)await sleep(50);if(!ViewerExtension.state.localProcessor||!byId('editStatus').textContent.includes('آماده است'))throw Error('Local editing engine did not load');
await fetch('/report',{method:'POST',body:JSON.stringify({ok:true,regions:data.present.length,selectedTogether:2,coloredPixels:colored,windowHU:true,contours:true,ruler:true,cine:true,persian:true,lazyMeasurements:true,lazyMpr:true,lazyEditing:true})});
}catch(error){await fetch('/report',{method:'POST',body:JSON.stringify({ok:false,error:String(error)})});}})();
</script>'''


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('viewer',type=Path)
    parser.add_argument('--browser',choices=['firefox','chromium'],default='firefox')
    parser.add_argument('--offline',action='store_true',help='Extract the package and open index.html directly using file://.')
    args=parser.parse_args()
    completed=threading.Event();report={}
    with tempfile.TemporaryDirectory(prefix='viewer-browser-check-',dir='work') as directory:
        root=Path(directory).resolve()
        if zipfile.is_zipfile(args.viewer):
            with zipfile.ZipFile(args.viewer) as archive:archive.extractall(root)
            viewer=root/'viewer/index.html'
        else:
            shutil.copytree(args.viewer.resolve().parent,root/'viewer')
            viewer=root/'viewer'/args.viewer.name
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.path!='/report':self.send_error(404);return
                report.update(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200);self.send_header('Access-Control-Allow-Origin','*');self.end_headers();completed.set()
        with ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(viewer.parent))) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            test=TEST.replace("fetch('/report'",f"fetch('{base}/report'")
            viewer.write_text(viewer.read_text().replace('</body>',test+'</body>'))
            profile=root/'profile';profile.mkdir()
            url=viewer.as_uri() if args.offline else base+'/index.html'
            command=['firefox','--headless','--no-remote','--profile',str(profile),url] if args.browser=='firefox' else ['chromium','--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--user-data-dir='+str(profile),url]
            with (root/'browser.log').open('w') as log:
                child=subprocess.Popen(command,stdout=log,stderr=log,start_new_session=True)
                try:
                    if not completed.wait(35):
                        print((root/'browser.log').read_text()[-2000:])
                        raise SystemExit('VIEWER_BROWSER_TEST_TIMEOUT')
                    if not report.get('ok'):raise SystemExit('VIEWER_BROWSER_TEST_FAILED: '+report.get('error','unknown'))
                    print('MULTI_REGION_CHECKBOX_BROWSER_OK',args.browser,'file' if args.offline else 'http',json.dumps(report))
                finally:
                    os.killpg(child.pid,signal.SIGTERM)
                    try:child.wait(timeout=5)
                    except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait(timeout=5)
                    server.shutdown()



if __name__=='__main__':main()
