/* Exercise actual generator/API packages in Chromium; all assets served locally. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const workspace=path.resolve('work/viewer-package-verification'),fixtures=['server upload','known','جابه جایی scan'];
const {chromium}=require(path.resolve('work/viewer-verification/node_modules/playwright'));
const rules=new Map(),served=[];
const server=http.createServer((req,res)=>{
  const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname),file=path.resolve(workspace,'.'+pathname);
  served.push(pathname);
  const ruleKey=rules.has(pathname)?pathname:[...rules.keys()].find(k=>k.endsWith('*')&&pathname.startsWith(k.slice(0,-1))),rule=rules.get(ruleKey);
  if(rule){rules.delete(ruleKey);if(rule.delay){setTimeout(()=>send(),rule.delay);return;}res.writeHead(503);res.end('test missing asset');return;}
  send();
  function send(){if(!file.startsWith(workspace+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  const ext=path.extname(file),types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.png':'image/png'};
  res.writeHead(200,{'Content-Type':types[ext]||'application/octet-stream','Cache-Control':'no-store'});res.end(fs.readFileSync(file));}
});
(async()=>{
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||path.resolve('work/viewer-verification/browsers/chromium-1155/chrome-linux/chrome'),headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1500,height:1000}}),page=await context.newPage(),errors=[],requests=[];
const localRequests=[];page.on('pageerror',e=>errors.push(String(e)));page.on('request',r=>{if(r.url().startsWith('file:'))localRequests.push(decodeURIComponent(new URL(r.url()).pathname));});page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(decodeURIComponent(new URL(r.url()).pathname));});
const wait=async expression=>{await page.waitForFunction(expression,null,{timeout:60000});};
const report={startup:[],checks:[],pageErrors:errors};
try{
  for(const fixture of fixtures){
    requests.length=0;await page.goto(`http://127.0.0.1:${server.address().port}/${encodeURIComponent(fixture)}/viewer/index.html`);
    await wait('window.ViewerExtension && ViewerExtension.state.ready && !loading && rawPixels');
    const initial=await page.evaluate(()=>({start:data.start,frames:data.frames,overlays:data.overlays,shape:data.shape,focus:ViewerExtension.state.focus}));
    assert.deepEqual(requests.filter(x=>x.endsWith('.png')).map(x=>x.split('/viewer/')[1]).sort(),[initial.frames[initial.start],initial.overlays[initial.start]].sort());
    assert.equal(requests.filter(x=>/measurements.json|advanced.json|mpr\/|surfaces\/|mask-worker|surface-viewer/.test(x)).length,0,JSON.stringify(requests));
    assert.equal(await page.locator('[webkitdirectory]').count(),0);
    assert.equal(await page.evaluate(()=>ViewerExtension.state.localProcessor),false);
    report.startup.push({fixture,requests:[...requests],pngRequests:2,shape:initial.shape});
    const next=(initial.start+1)%initial.frames.length;requests.length=0;await page.evaluate(n=>render(n),next);
    assert.equal(requests.filter(x=>x.endsWith('.png')).length,2);
    if(fixture!=='known')continue;
    await page.evaluate(async()=>{
      await render(data.start);
      for(const value of ['isarmed','lungimed','']){byId('themePicker').value=value;byId('themePicker').dispatchEvent(new Event('change'));if(document.documentElement.dataset.appKey!==value)throw Error('Theme switch failed');}
      byId('selectNone').click();if(ctx.getImageData(0,0,canvas.width,canvas.height).data.some((n,i)=>i%4===3&&n))throw Error('Selection clear failed');
      for(const n of [1,2])document.querySelector('input[data-region="'+n+'"]').click();if(selected.size!==2)throw Error('Multiple selection failed');
      byId('contour').click();const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;
      for(let i=0;i<pixels.length;i+=4){const n=labelPixels[i],p=i/4,x=p%canvas.width,y=Math.floor(p/canvas.width),edge=x===0||x===canvas.width-1||y===0||y===canvas.height-1||labelPixels[i-4]!==n||labelPixels[i+4]!==n||labelPixels[i-4*canvas.width]!==n||labelPixels[i+4*canvas.width]!==n;if((pixels[i+3]>0)!==(selected.has(n)&&edge))throw Error('Contour failed');}
      byId('contour').click();byId('selectAll').click();byId('opacity').value=55;byId('opacity').dispatchEvent(new Event('input'));if(canvas.style.opacity!=='0.55')throw Error('Opacity failed');
      byId('search').value='کلیه';byId('search').dispatchEvent(new Event('input'));if(rows.some(r=>!r.row.hidden&&!r.name.includes('کلیه')))throw Error('Persian search failed');byId('search').value='';byId('search').dispatchEvent(new Event('input'));
      byId('preset').value='lung';byId('preset').dispatchEvent(new Event('change'));const shown=ctCtx.getImageData(0,0,ct.width,ct.height).data;
      for(let i=0;i<rawPixels.length;i+=4){const hu=(rawPixels[i]+256*rawPixels[i+1])*data.hu_step+data.hu_offset;if(shown[i]!==Math.round(Math.max(0,Math.min(1,(hu+600+750)/1500))*255))throw Error('Numeric HU window failed');}
      byId('invert').click();const inverted=ctCtx.getImageData(0,0,ct.width,ct.height).data;for(let i=0;i<shown.length;i+=4)if(inverted[i]!==255-shown[i])throw Error('Inversion failed');
      byId('zoomIn').click();if(zoom<=1)throw Error('Zoom failed');byId('reset').click();if(zoom!==1||panX||panY)throw Error('Reset failed');
      byId('rulerTool').click();const b=world.getBoundingClientRect(),y=b.top+b.height/2;for(const fraction of [.25,.75])stage.dispatchEvent(new PointerEvent('pointerdown',{clientX:b.left+b.width*fraction,clientY:y,button:0}));if(rulerPoints.length!==2||!byId('rulerValue').textContent.includes(fa.format(ct.width*data.spacing[0]/2)))throw Error('Physical ruler failed');byId('clearRuler').click();byId('pan').click();
    });
    report.checks.push('Persian search, themes, multiple selections, contours, opacity, numeric window/level, inversion, zoom/reset and physical ruler');
    // A failed new slice clears both image layers; retry must restore exactly it.
    const missing=(next+1)%initial.frames.length,prefix='/known/viewer/';
    rules.set(prefix+initial.frames[missing],{});await page.evaluate(n=>render(n),missing);
    assert.equal(await page.evaluate(()=>rawPixels===null&&labelPixels===null),true);
    assert.equal(await page.locator('#retrySlice').isVisible(),true);
    assert.equal(await page.evaluate(()=>{const p=ctCtx.getImageData(0,0,ct.width,ct.height).data;return p.every(v=>v===0);}),true);
    await page.locator('#retrySlice').click();await wait('!loading && rawPixels && byId("retrySlice").hidden');
    // Delayed navigation must never overwrite a later request.
    const slow=(missing+1)%initial.frames.length,fast=(missing+2)%initial.frames.length;
    rules.set(prefix+initial.frames[slow],{delay:250});
    await page.evaluate(async ({slow,fast})=>{const a=render(slow);await render(fast);await a;},{slow,fast});
    assert.equal(await page.evaluate(()=>index),fast);
    const expected=await page.evaluate(async()=>{const p=await imagePixels(data.frames[index]);return p.pixels.every((v,i)=>v===rawPixels[i]);});assert.equal(expected,true);
    report.checks.push('missing native slice clears pixels and retries; stale navigation ignored');
    const notCached=(fast+2)%initial.frames.length;requests.length=0;
    await page.evaluate(async n=>{await Promise.all(Array.from({length:8},()=>imagePixels(data.frames[n])));},notCached);
    assert.ok(requests.filter(x=>x.endsWith(initial.frames[notCached])).length<=1);
    // Measurements do not load until opened, and failed JSON loads can retry.
    rules.set(prefix+'data/measurements.json',{});await page.locator('#tabMeasurements').click();await wait('!byId("retryAnalysis").hidden');
    await page.locator('#retryAnalysis').click();await wait('Object.keys(ViewerExtension.state.measurements.edited).length>0 && byId("retryAnalysis").hidden');
    assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count),60);
    assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.edited[2].count),60);
    assert.ok(await page.locator('#pairedTable tbody tr').count()>0);
    report.checks.push('lazy measurements, bilateral table and JSON retry');
    requests.length=0;rules.set(prefix+'data/advanced.json',{});rules.set(prefix+'data/mpr/axial/*',{});await page.locator('#modeMpr').click();await wait('byId("mprStatus").querySelector("button")');
    await page.locator('#mprStatus button').click();await wait('byId("mprStatus").textContent.includes("data/mpr/axial/")');await page.locator('#mprStatus button').click();await wait('ViewerExtension.state.planes.length===3');
    assert.equal(new Set(requests.filter(x=>/data\/mpr\/.*png$/.test(x))).size,3,JSON.stringify(requests));
    assert.equal(requests.filter(x=>/surfaces\/|mask-worker/.test(x)).length,0);
    requests.length=0;
    await page.evaluate(()=>{const slider=byId('mprAxialSlice');slider.value=Number(slider.value)+Number(slider.step);slider.dispatchEvent(new Event('input'));});
    await wait('ViewerExtension.state.planes.length===3');
    assert.equal(requests.filter(x=>/data\/mpr\/.*png$/.test(x)).length,1,JSON.stringify(requests));
    assert.equal(await page.evaluate(()=>ViewerExtension.state.planes.every(p=>Math.abs(p.spec.fixed-ViewerExtension.state.world[p.spec.axes[2]])<1e-8)),true);
    report.checks.push('three current MPR images only; slider loads one additional plane; synchronized RAS');
    await page.evaluate(()=>ViewerExtension.focus(1));requests.length=0;rules.set(prefix+'data/surfaces/1.png',{});await page.locator('#modeSurface').click();await wait('byId("surfaceStatus").querySelector("button")');
    await page.locator('#surfaceStatus button').click();await wait('ViewerExtension.surface?.entries.get(ViewerExtension.state.focus)?.buffer');
    assert.equal(requests.filter(x=>/data\/surfaces\/.*png$/.test(x)).length,2,JSON.stringify(requests));
    assert.equal(await page.evaluate(()=>ViewerExtension.surface.entries.size),1);
    const pixels=await page.evaluate(()=>{const s=ViewerExtension.surface,g=s.gl,p=new Uint8Array(s.canvas.width*s.canvas.height*4);g.readPixels(0,0,s.canvas.width,s.canvas.height,g.RGBA,g.UNSIGNED_BYTE,p);let n=0;for(let i=0;i<p.length;i+=4)if(p[i]>25||p[i+1]>30||p[i+2]>40)n++;return n;});assert.ok(pixels>100);
    await page.evaluate(()=>ViewerExtension.surface.add(2));await wait('ViewerExtension.surface.entries.get(2)?.buffer');
    assert.equal(requests.filter(x=>/data\/surfaces\/.*png$/.test(x)).length,3);
    report.checks.push('focused 3D surface only; explicit additional surface; WebGL rendering; missing metadata/MPR/surface retry');
    requests.length=0;await page.locator('#tabEditing').click();await wait('ViewerExtension.state.localProcessor && byId("editStatus").textContent.includes("آماده است")');
    assert.equal(requests.filter(x=>/data\/ct\/|data\/mpr\//.test(x)).length,0,JSON.stringify(requests));
    assert.equal(requests.filter(x=>x.endsWith('mask-worker.js')).length,1);
    const original=await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count);
    await page.locator('#modeNative').click();await page.evaluate(()=>render(2));await page.locator('#editingEnabled').check();await page.locator('#brushDiameter').fill('4');
    await page.selectOption('#reviewState','approved');await wait('ViewerExtension.state.reviews[1]==="approved"');
    const brushPoint=await page.evaluate(()=>{const b=world.getBoundingClientRect();return {x:b.left+8.5*b.width/ct.width,y:b.top+5.5*b.height/ct.height};});
    await page.mouse.click(brushPoint.x,brushPoint.y);
    await wait('ViewerExtension.state.revision>0 && !ViewerExtension.state.busy');
    assert.equal(await page.evaluate(()=>ViewerExtension.state.reviews[1]),'needs_correction');
    const pkg=await page.evaluate(()=>ViewerExtension.exportPackage());assert.ok(pkg.changes.length>0);assert.equal(pkg.saved_to_server,false);
    const edited=await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count);assert.ok(edited>original);
    await page.locator('#undoEdit').click();await wait('!ViewerExtension.state.busy');assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count),original);
    await page.locator('#redoEdit').click();await wait('!ViewerExtension.state.busy');assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count),edited);
    await page.evaluate(()=>ViewerExtension.mutate('undo'));await page.evaluate(p=>ViewerExtension.importPackage(p),pkg);assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count),edited);
    await page.selectOption('#maskVersion','original');await wait('!ViewerExtension.state.busy && ViewerExtension.state.version==="original"');
    assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.original[1].count),original);
    report.checks.push('editing loads engine/labels without CT volume; real pointer brush and undo/redo controls, approval invalidation, export/import, immutable original');
    await page.evaluate(async()=>{for(let z=0;z<data.shape[2];z++)await Promise.all([imagePixels(data.frames[z]),imagePixels(data.overlays[z])]);});
    const stats=await page.evaluate(()=>ViewerLoader.stats());assert.ok(stats.decodedBytes<=stats.maxBytes&&stats.decodedImages<=stats.maxImages);report.cache=stats;
    report.checks.push('concurrent requests deduplicated; decoded image LRU bounded by bytes and count');
  }
  report.fileStartup=[];
  for(const fixture of fixtures){
    localRequests.length=0;requests.length=0;
    await page.goto('file://'+path.join(workspace,fixture,'viewer/index.html'));
    await wait('window.ViewerExtension?.state.ready && !loading && rawPixels');
    assert.equal(localRequests.filter(x=>x.endsWith('.png.js')).length,2,JSON.stringify(localRequests));
    assert.equal(localRequests.filter(x=>/measurements|advanced.json|mpr\/|surfaces\/|mask-worker|surface-viewer/.test(x)).length,0);
    assert.equal(await page.locator('[webkitdirectory]').count(),0);
    report.fileStartup.push({fixture,requests:[...localRequests],initialImages:2});
    localRequests.length=0;
    await page.evaluate(()=>render((index+1)%data.frames.length));
    assert.equal(localRequests.filter(x=>x.endsWith('.png.js')).length,2);
    if(fixture!=='known')continue;
    // Missing a local script-backed image clears the display, then retries.
    const z=await page.evaluate(()=>(index+1)%data.frames.length),asset=await page.evaluate(z=>data.frames[z]+'.js',z);
    const source=path.join(workspace,fixture,'viewer',asset),temporary=source+'.missing';
    fs.renameSync(source,temporary);
    try{await page.evaluate(z=>render(z),z);assert.equal(await page.evaluate(()=>rawPixels===null&&labelPixels===null),true);assert.equal(await page.locator('#retrySlice').isVisible(),true);}
    finally{fs.renameSync(temporary,source);}
    await page.locator('#retrySlice').click();await wait('!loading&&rawPixels&&byId("retrySlice").hidden');
    await page.locator('#tabMeasurements').click();await wait('ViewerExtension.state.measurements.edited[1]?.count===60');
    localRequests.length=0;await page.locator('#modeMpr').click();await wait('ViewerExtension.state.planes.length===3');
    assert.equal(localRequests.filter(x=>/data\/mpr\/.*png.js$/.test(x)).length,3);
    localRequests.length=0;await page.locator('#modeSurface').click();await wait('ViewerExtension.surface?.entries.get(1)?.buffer');
    assert.equal(localRequests.filter(x=>/data\/surfaces\/.*png.js$/.test(x)).length,1);
    localRequests.length=0;await page.locator('#tabEditing').click();await wait('ViewerExtension.state.localProcessor&&byId("editStatus").textContent.includes("آماده است")');
    assert.equal(localRequests.filter(x=>/data\/ct\/|data\/mpr\//.test(x)).length,0);
    await page.evaluate(()=>ViewerExtension.mutate('brush',{label:1,z:2,diameter:4,erase:false,paths:[[8,5]]}));
    assert.ok(await page.evaluate(()=>ViewerExtension.state.measurements.edited[1].count>60));
    assert.equal(requests.length,0,'Direct opening must not contact an HTTP server');
    assert.equal(localRequests.filter(x=>/\.(png|json)$/.test(x)).length,0,'Local numeric data must use browser-supported script loading');
  }
  report.checks.push('double-click file:// opening: one CT/mask, lazy navigation/measurements/MPR/surface/editing, missing-file retry; no server or launcher');
  assert.deepEqual(errors,[]);report.ok=true;
  fs.writeFileSync(path.join(workspace,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exit(1);});
