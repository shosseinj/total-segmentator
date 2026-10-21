/* Actual Chromium, exact clinic CSP and opaque-origin sandbox. No CSP bypass. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const root=path.resolve(process.argv[2]||'work/viewer-verification');
const {chromium}=require(path.join(root,'node_modules/playwright'));
const policy="sandbox allow-scripts allow-downloads; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'";
async function main(){
    const names=['known','oblique','permuted','demo'];if(fs.existsSync(path.join(root,'live-server/viewer.html')))names.push('live-server');
    const server=http.createServer((req,res)=>{const name=req.url.slice(1);if(!names.includes(name)){res.writeHead(404);res.end();return;}res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':policy});res.end(fs.readFileSync(path.join(root,name,'viewer.html')));});
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||path.join(root,'browsers/chromium-1155/chrome-linux/chrome'),headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
    try{
        const context=await browser.newContext({viewport:{width:1500,height:1000}}),page=await context.newPage(),errors=[],requests=[];
        await context.addInitScript(()=>{window.workerAttempts=0;window.Worker=function(){window.workerAttempts++;throw Error('WORKERS_DISABLED_FOR_TEST');};});
        page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>{if(!r.isNavigationRequest()&&/^https?:/.test(r.url()))requests.push(r.url());});
        const cdp=await context.newCDPSession(page);
        // CDP reads state directly: Playwright waitForFunction uses eval, which this
        // production CSP correctly blocks. Application code never calls eval.
        const run=async expression=>{const r=await cdp.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
        const wait=async(expression)=>{const deadline=Date.now()+30000;while(Date.now()<deadline){if(await run(expression))return;await new Promise(r=>setTimeout(r,40));}throw Error('Timed out: '+expression+' '+await run('JSON.stringify({analysis:byId("analysisStatus").textContent,mpr:byId("mprStatus").textContent,surface:byId("surfaceStatus").textContent,edit:byId("editStatus").textContent})'));};
        const fixtures=[];
        for(const name of names){
            const start=Date.now();await page.goto('http://127.0.0.1:'+server.address().port+'/'+name);await wait('window.ViewerExtension?.state.ready&&!loading');
            const readyMs=Date.now()-start;
            assert.equal(await run('ViewerExtension.state.processing'),'server-precomputed');assert.equal(await run('ViewerExtension.state.localProcessor'),false);
            await page.locator('#modeMpr').click();await wait('ViewerExtension.state.planes.length===3');
            assert.equal(await run('ViewerExtension.state.planes.every(p=>Math.abs(p.spec.fixed-ViewerExtension.state.world[p.spec.axes[2]])<1e-9)'),true);
            await page.locator('#modeSurface').click();await wait('[...ViewerExtension.surface.entries.values()].some(e=>e.buffer)');
            assert.equal(await run('ViewerExtension.state.localProcessor'),false);assert.equal(await run('window.workerAttempts'),0);
            const pixels=await run('(()=>{const s=ViewerExtension.surface,g=s.gl,p=new Uint8Array(s.canvas.width*s.canvas.height*4);g.readPixels(0,0,s.canvas.width,s.canvas.height,g.RGBA,g.UNSIGNED_BYTE,p);let n=0;for(let i=0;i<p.length;i+=4)if(p[i]>25||p[i+1]>30||p[i+2]>40)n++;return n;})()');assert.ok(pixels>100);
            fixtures.push({name,readyMs,meshes:await run('ViewerExtension.surface.entries.size'),coloredPixels:pixels,workerAttempts:0,localProcessor:false,bytes:fs.statSync(path.join(root,name,'viewer.html')).size});
            if(name==='known'){
                // Pick the actual rendered kidney, with no volume or worker.
                const target=await run('(()=>{const s=ViewerExtension.surface,r=ViewerExtension.state.measurements.edited[1],p=r.lower.map((v,i)=>(v+r.upper[i])/2-s.center[i]),a=s.axes(),b=s.canvas.getBoundingClientRect(),dot=a=>a.reduce((sum,v,i)=>sum+v*p[i],0),scale=s.zoom/s.radius;return {x:b.left+(dot(a.right)*scale/(b.width/b.height)+1)*b.width/2,y:b.top+(1-dot(a.up)*scale)*b.height/2};})()');
                await page.mouse.click(target.x,target.y);await wait('byId("surfaceStatus").textContent.includes("RAS mm")&&ViewerExtension.state.focus===1');assert.equal(await run('ViewerExtension.state.localProcessor'),false);
                const hit=await run('({world:ViewerExtension.state.world,row:ViewerExtension.state.measurements.edited[1]})');for(let a=0;a<3;a++)assert.ok(hit.world[a]>=hit.row.lower[a]-.01&&hit.world[a]<=hit.row.upper[a]+.01,JSON.stringify(hit));
                await page.locator('#tabEditing').click();await page.selectOption('#reviewState','approved');await wait('ViewerExtension.state.reviews[1]==="approved"');assert.equal(await run('ViewerExtension.state.localProcessor'),false);
                // Explicit offline edits use a statically embedded, yielding local
                // adapter. They preserve CSP and never allocate a decoded CT volume.
                await run('ViewerExtension.mutate("brush",{label:1,z:2,diameter:4,erase:false,paths:[[8,5]]})');
                await wait('ViewerExtension.state.revision>0&&!ViewerExtension.state.busy&&!loading');assert.equal(await run('ViewerExtension.state.localProcessor'),true);assert.equal(await run('ViewerExtension.state.reviews[1]'),'needs_correction');
                const pkg=await run('ViewerExtension.exportPackage()');assert.ok(pkg.changes.length>0);assert.equal(pkg.saved_to_server,false);
                const count=await run('ViewerExtension.state.measurements.edited[1].count');await run('ViewerExtension.mutate("undo")');assert.equal(await run('ViewerExtension.state.measurements.edited[1].count'),60);await run('ViewerExtension.importPackage('+JSON.stringify(pkg)+')');assert.equal(await run('ViewerExtension.state.measurements.edited[1].count'),count);
                assert.equal(await run('window.workerAttempts'),0);
            }
        }
        assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
        const report={ok:true,policy,opaqueOriginSandbox:true,policyRelaxed:false,networkRequests:requests,consoleErrors:errors,serverMeasurements:true,serverMpr:true,serverMeshes:true,gpuDepthPicking:true,offlineEditsUnderSamePolicy:true,fixtures};fs.writeFileSync(path.join(root,'server-browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
    }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(e=>{console.error(e);process.exit(1);});
