/* Real file:// workflow, worker execution, WebGL framebuffer, UI pointer events.
 * Test-only dependency: npm install --prefix work/viewer-verification playwright@1.50.1
 * python3 -m tools.generate_viewer_demo
 * node tools/check_viewer_advanced.cjs [work/viewer-verification]
 */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(process.argv[2]||'work/viewer-verification');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(root,'node_modules/playwright'));
const close=(a,b,tol=1e-6)=>assert.ok(Math.abs(a-b)<tol,`${a} != ${b}`);
async function main(){
    const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||(fs.existsSync(path.join(root,'browsers/chromium-1155/chrome-linux/chrome'))?path.join(root,'browsers/chromium-1155/chrome-linux/chrome'):'/snap/bin/chromium'),headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
    const context=await browser.newContext({acceptDownloads:true,offline:true,viewport:{width:1500,height:1100}}),errors=[],requests=[];
    const page=await context.newPage();page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
    const wait=()=>page.waitForFunction(()=>ViewerExtension.state.ready&&!loading&&!ViewerExtension.state.busy,{},{timeout:30000});
    const open=async name=>{await page.goto('file://'+path.join(root,name,'viewer.html'));await wait();};
    const row=()=>page.evaluate(()=>ViewerExtension.state.measurements.edited[1]);
    const clickPoint=async(p)=>{await page.locator('#stage').scrollIntoViewIfNeeded();const b=await page.evaluate(p=>{const b=world.getBoundingClientRect();return {x:b.left+(p[0]+.5)*b.width/ct.width,y:b.top+(p[1]+.5)*b.height/ct.height};},p);await page.evaluate(()=>{document.addEventListener('pointerdown',e=>{const p=pointAt(e);window.testBrushPoint=[p.x-.5,p.y-.5];},{once:true,capture:true});});await page.mouse.click(b.x,b.y);return page.evaluate(()=>window.testBrushPoint);};
    const revision=()=>page.evaluate(()=>ViewerExtension.state.revision);
    const waitRevision=async before=>{await page.waitForFunction(before=>ViewerExtension.state.revision>before&&!ViewerExtension.state.busy&&!loading,before);};
    await open('known');
    assert.equal(await page.evaluate(()=>ViewerExtension.state.processing),'server-precomputed');assert.equal(await page.evaluate(()=>ViewerExtension.state.localProcessor),false);
    const immutable=await page.evaluate(()=>JSON.stringify(data));
    await page.locator('#selectNone').click();await page.locator('[data-select-group="kidney"]').click();assert.equal(await page.evaluate(()=>selected.size),2);assert.equal(await page.locator('[data-measurement]').count(),2);assert.equal(await page.locator('#areaChart').getAttribute('data-series-count'),'2');await page.locator('[data-measurement="2"] .measurement-card-header button').click();assert.equal(await page.evaluate(()=>selected.size),2);await page.locator('[data-measurement="1"] .measurement-card-header button').click();
    const base=await row();assert.equal(base.count,60);close(base.volume_ml,1.44);assert.deepEqual(base.dimensions_mm,[6,12,20]);close(base.max_area_mm2,72);
    assert.equal(await page.locator('#pairedTable tbody tr').count(),1);
    await page.locator('#goMaximum').click();await wait();assert.equal(await page.evaluate(()=>index),2);
    assert.equal(await page.evaluate(()=>ViewerExtension.surface.gl===null),true);
    await page.locator('#quickBrush').click();assert.equal(await page.locator('#editingEnabled').isChecked(),true);await page.locator('#brushDiameter').fill('8');
    await page.selectOption('#reviewState','approved');await page.waitForFunction(()=>ViewerExtension.state.reviews[1]==='approved');
    // A real mouse stroke uses the transformed canvas bounds, independent of zoom/pan.
    await page.evaluate(()=>{setZoom(2);panX=24;panY=-17;transform();});let before=await revision();const brushPoint=await clickPoint([9,5]);await waitRevision(before);let expectedAdded=0;for(let y=0;y<16;y++)for(let x=0;x<18;x++)if((x-brushPoint[0])**2*4+(y-brushPoint[1])**2*9<=16+1e-8)expectedAdded++;
    let edited=await row();assert.equal(edited.count,60+expectedAdded);close(edited.volume_ml,(60+expectedAdded)*24/1000);assert.equal(await page.evaluate(()=>ViewerExtension.state.reviews[1]),'needs_correction');
    assert.equal(await page.evaluate(()=>labelPixels[4*(9+ct.width*5)]),1);
    const orig=await page.evaluate(()=>ViewerExtension.rpc('measure',{version:'original',ids:[1]}));assert.equal(orig.rows[1].count,60);
    // Undo/redo uses real controls and restores measurement/chart values.
    before=await revision();await page.locator('#undoEdit').click();await waitRevision(before);assert.equal((await row()).count,60);
    before=await revision();await page.locator('#redoEdit').click();await waitRevision(before);assert.equal((await row()).count,60+expectedAdded);
    await page.selectOption('#maskVersion','original');await page.waitForFunction(()=>ViewerExtension.state.version==='original'&&!loading&&ViewerExtension.state.measurements.original[1]);assert.equal(await page.evaluate(()=>ViewerExtension.state.measurements.original[1].count),60);assert.equal(await page.locator('#editingEnabled').isDisabled(),true);
    await page.selectOption('#maskVersion','edited');await wait();
    const downloadEvent=page.waitForEvent('download');await page.locator('#exportEdits').click();const download=await downloadEvent,packagePath=path.join(root,'downloaded-edits.json');await download.saveAs(packagePath);const pkg=JSON.parse(fs.readFileSync(packagePath,'utf8'));assert.equal(pkg.changes.length,expectedAdded);assert.equal(pkg.authenticated_user,null);assert.equal(pkg.saved_to_server,false);
    before=await revision();await page.locator('#resetMask').click();await waitRevision(before);assert.equal((await row()).count,60);
    before=await revision();await page.locator('#importEdits').setInputFiles(packagePath);await waitRevision(before);assert.equal((await row()).count,60+expectedAdded);
    // Invalid package must leave the active edit state intact.
    const bad=structuredClone(pkg);bad.geometry.affine_ras_mm[0][3]+=1;const badPath=path.join(root,'wrong-geometry.json');fs.writeFileSync(badPath,JSON.stringify(bad));before=await revision();await page.locator('#importEdits').setInputFiles(badPath);await page.waitForFunction(()=>byId('editStatus').textContent.includes('GEOMETRY_OR_LABELS_MISMATCH'));assert.equal(await revision(),before);assert.equal((await row()).count,60+expectedAdded);
    // Actual measurement download contains provenance and physical measurements.
    await page.locator('#tabMeasurements').click();const measurementEvent=page.waitForEvent('download');await page.locator('#saveMeasurement').click();const md=await measurementEvent,measurementPath=path.join(root,'downloaded-measurement.json');await md.saveAs(measurementPath);assert.equal(JSON.parse(fs.readFileSync(measurementPath)).measurement.count,60+expectedAdded);assert.equal(JSON.parse(fs.readFileSync(measurementPath)).selected_measurements.length,2);
    // Real axial/coronal/sagittal canvases and synchronized patient coordinates.
    await page.locator('#modeMpr').click();await page.waitForFunction(()=>ViewerExtension.state.planes.length===3);
    let planes=await page.evaluate(()=>ViewerExtension.state.planes.map(p=>({spec:p.spec,world:ViewerExtension.state.world})));for(const p of planes)close(p.spec.fixed,p.world[p.spec.axes[2]]);
    const cb=await page.locator('#mprCoronal').boundingBox();await page.mouse.click(cb.x+cb.width*.43,cb.y+cb.height*.55);await page.waitForFunction(()=>ViewerExtension.state.planes.every(p=>Math.abs(p.spec.fixed-ViewerExtension.state.world[p.spec.axes[2]])<1e-7));
    // Chart clicks and the accessible slider navigate native acquisition sections.
    await page.locator('#areaSlice').fill('4');await page.locator('#areaSlice').dispatchEvent('input');await page.waitForFunction(()=>index===4&&!loading);
    // Build two real WebGL meshes. Picking is tested at an actual projected surface.
    await page.evaluate(()=>ViewerExtension.focus(1));await page.locator('#modeSurface').click();await page.waitForFunction(()=>ViewerExtension.surface.entries.get(1)?.buffer);
    await page.locator('[data-measurement="2"] .measurement-card-header button').click();await page.waitForFunction(()=>ViewerExtension.surface.entries.get(2)?.buffer);
    const glCheck=await page.evaluate(()=>{const s=ViewerExtension.surface,g=s.gl,p=new Uint8Array(s.canvas.width*s.canvas.height*4);g.readPixels(0,0,s.canvas.width,s.canvas.height,g.RGBA,g.UNSIGNED_BYTE,p);let colored=0;for(let i=0;i<p.length;i+=4)if(p[i]>25||p[i+1]>30||p[i+2]>40)colored++;return {colored,error:g.getError(),meshes:[...s.entries.values()].map(e=>e.count)};});assert.ok(glCheck.colored>100);assert.equal(glCheck.error,0);assert.ok(glCheck.meshes.every(n=>n>0));
    await page.locator('#surfaceCanvas').scrollIntoViewIfNeeded();const target=await page.evaluate(()=>{const s=ViewerExtension.surface,r=ViewerExtension.state.measurements.edited[1],p=r.lower.map((v,i)=>(v+r.upper[i])/2-s.center[i]),axes=s.axes(),b=s.canvas.getBoundingClientRect(),dot=a=>a.reduce((sum,v,i)=>sum+v*p[i],0),scale=s.zoom/s.radius;return {x:b.left+(dot(axes.right)*scale/(b.width/b.height)+1)*b.width/2,y:b.top+(1-dot(axes.up)*scale)*b.height/2};});
    await page.mouse.click(target.x,target.y);await page.waitForFunction(()=>ViewerExtension.state.focus===1&&byId('surfaceStatus').textContent.includes('RAS mm'));
    assert.equal(await page.evaluate(()=>ViewerExtension.surface.entries.size),2);const meshBefore=await page.evaluate(()=>ViewerExtension.surface.entries.get(1).count);await page.locator('#tabEditing').click();
    before=await revision();await page.locator('#undoEdit').click();await waitRevision(before);await page.waitForFunction(old=>ViewerExtension.surface.entries.get(1)?.buffer&&ViewerExtension.surface.entries.get(1).count!==old,meshBefore);
    await page.locator('input[data-region="2"]').uncheck();await page.waitForFunction(()=>!ViewerExtension.surface.entries.has(2));assert.equal(await page.locator('[data-measurement]').count(),1);assert.equal(await page.evaluate(()=>selected.has(1)),true);
    await page.locator('input[data-region="2"]').check();await page.waitForFunction(()=>ViewerExtension.surface.entries.get(2)?.buffer);assert.equal(await page.locator('[data-measurement]').count(),2);assert.equal(await page.evaluate(()=>selected.size),2);
    await page.locator('#surfaceCanvas').screenshot({path:path.join(root,'surface.png')});
    const check=page.locator('#surfaceControls input[type=checkbox]').first();await check.uncheck();assert.equal(await page.evaluate(()=>ViewerExtension.surface.entries.get(1).buffer===null),true);
    await page.locator('#cancelSurface').click();assert.equal(await page.evaluate(()=>ViewerExtension.surface.entries.size),0);
    assert.equal(await page.evaluate(()=>JSON.stringify(data)),immutable);
    // Worker and main native slice requests ignore stale selection/version responses.
    await page.evaluate(async()=>{await Promise.all([render(2),render(7),render(4),render(2)]);});assert.equal(await page.evaluate(()=>index),2);assert.equal(await page.evaluate(async()=>{const image=await imagePixels(data.frames[2]),mask=await ViewerExtension.rpc('slice',{z:2,version:ViewerExtension.state.version});return rawPixels.every((v,i)=>v===image.pixels[i])&&mask.pixels.every((v,i)=>v===labelPixels[i*4]);}),true);
    await page.evaluate(()=>{setZoom(1);panX=panY=0;transform();ViewerExtension.showInspector('measurements');ViewerExtension.showView('native');});
    await page.screenshot({path:path.join(root,'workflow.png'),fullPage:true});
    for(const name of ['oblique','permuted']){
        await open(name);const r=await row();close(r.volume_ml,1.44,1e-6);close(r.max_area_mm2,72,1e-5);
        assert.equal(await page.evaluate(()=>data.geometry.section_is_patient_axial),false);
        await page.locator('#goMaximum').click();await wait();const section=await page.evaluate(()=>{const s=ViewerExtension.state,r=s.measurements.edited[1];return {native:ViewerExtension.worldToVoxel(s.world),axis:data.geometry.section_axis,max:r.max_slice};});close(section.native[section.axis],section.max);
        await page.locator('#modeMpr').click();await page.waitForFunction(()=>ViewerExtension.state.planes.length===3);assert.equal(await page.evaluate(()=>ViewerExtension.state.planes.every(p=>p.spec.fixed===ViewerExtension.state.world[p.spec.axes[2]])),true);
    }
    // Missing geometry leaves the original CT viewer alive and prevents fabricated units.
    await page.goto('file://'+path.join(root,'missing','viewer.html'));await page.waitForFunction(()=>labelPixels&&!loading);assert.equal(await page.locator('#editingEnabled').isDisabled(),true);assert.equal(await page.locator('#startMpr').isDisabled(),true);assert.equal(await page.locator('#rulerTool').isDisabled(),true);assert.ok((await page.locator('#geometryWarning').textContent()).includes('qform'));await page.locator('#next').click();await page.waitForFunction(()=>!loading);
    // Large production-generator export, all 117 classes, same actual rendering workflow.
    await open('demo');assert.equal(await page.evaluate(()=>Object.keys(data.labels).length),117);assert.equal(await page.evaluate(()=>data.present.length),12);
    await page.locator('#modeMpr').click();await page.waitForFunction(()=>ViewerExtension.state.planes.length===3,{},{timeout:30000});
    await page.locator('#modeSurface').click();await page.waitForFunction(()=>[...ViewerExtension.surface.entries.values()].some(e=>e.buffer),{},{timeout:30000});
    assert.equal(await page.evaluate(()=>ViewerExtension.state.localProcessor),false,'viewing server derivatives must not initialize local processing');
    await page.screenshot({path:path.join(root,'demo.png'),fullPage:true});
    // Independent opacity and camera controls affect real rendering state.
    await page.locator('#surfaceControls input[type=range]').first().fill('35');await page.locator('#surfaceControls input[type=range]').first().dispatchEvent('input');assert.equal(await page.evaluate(()=>[...ViewerExtension.surface.entries.values()][0].opacity),.35);
    const sb=await page.locator('#surfaceCanvas').boundingBox();await page.mouse.move(sb.x+sb.width/2,sb.y+sb.height/2);await page.mouse.wheel(0,-120);assert.ok(await page.evaluate(()=>ViewerExtension.surface.zoom>1));await page.locator('#resetCamera').click();close(await page.evaluate(()=>ViewerExtension.surface.zoom),1);
    // Cancellation must keep late mesh results from allocating fresh buffers.
    await page.locator('#cancelSurface').click();await page.locator('#buildSurface').click();await page.locator('#cancelSurface').click();await page.waitForTimeout(150);assert.equal(await page.evaluate(()=>ViewerExtension.surface.entries.size),0);
    // Mobile canvases retain physical aspect ratios without horizontal overflow.
    await page.setViewportSize({width:390,height:844});await page.locator('#modeMpr').click();await page.waitForFunction(()=>ViewerExtension.state.planes.length===3);await page.locator('#toggleStructures').click();assert.equal(await page.locator('#structureSidebar').isVisible(),true);await page.locator('#toggleStructures').click();assert.equal(await page.locator('#structureSidebar').isVisible(),false);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
    await page.waitForFunction(()=>ViewerExtension.state.planes.every(p=>{const c=byId('mpr'+({axial:'Axial',coronal:'Coronal',sagittal:'Sagittal'})[p.spec.kind]),b=c.getBoundingClientRect();return Math.abs(b.width/b.height-p.spec.width*p.spec.step_u/(p.spec.height*p.spec.step_v))<.002;}));
    const aspects=await page.evaluate(()=>ViewerExtension.state.planes.map(p=>{const c=byId('mpr'+({axial:'Axial',coronal:'Coronal',sagittal:'Sagittal'})[p.spec.kind]),b=c.getBoundingClientRect();return {css:b.width/b.height,physical:p.spec.width*p.spec.step_u/(p.spec.height*p.spec.step_v)};}));for(const a of aspects)close(a.css,a.physical,.002);
    await page.screenshot({path:path.join(root,'mobile.png'),fullPage:true});
    // Optional WebGL and worker failures leave the existing viewer usable.
    const fallback=await context.newPage();fallback.on('pageerror',e=>errors.push(String(e)));await fallback.addInitScript(()=>{const getContext=HTMLCanvasElement.prototype.getContext;HTMLCanvasElement.prototype.getContext=function(type,...args){return type==='webgl'?null:getContext.call(this,type,...args);};});await fallback.goto('file://'+path.join(root,'known','viewer.html'));await fallback.waitForFunction(()=>ViewerExtension.state.ready&&!loading);await fallback.locator('#modeSurface').click();assert.ok((await fallback.locator('#surfaceStatus').textContent()).includes('WebGL'));await fallback.locator('#modeNative').click();await fallback.locator('#next').click();await fallback.waitForFunction(()=>!loading&&index===3);await fallback.close();
    const noWorker=await context.newPage();noWorker.on('pageerror',e=>errors.push(String(e)));await noWorker.addInitScript(()=>{window.Worker=function(){throw Error('WORKER_UNAVAILABLE');};});await noWorker.goto('file://'+path.join(root,'known','viewer.html'));await noWorker.waitForFunction(()=>ViewerExtension.state.ready&&!loading);assert.equal(await noWorker.locator('#startMpr').isDisabled(),false);await noWorker.locator('#modeMpr').click();await noWorker.waitForFunction(()=>ViewerExtension.state.planes.length===3);await noWorker.locator('#modeSurface').click();await noWorker.waitForFunction(()=>ViewerExtension.surface.entries.get(1)?.buffer);assert.equal(await noWorker.evaluate(()=>ViewerExtension.state.localProcessor),false);await noWorker.locator('#modeNative').click();await noWorker.locator('#next').click();await noWorker.waitForFunction(()=>!loading);await noWorker.close();
    assert.equal(requests.length,0,'offline export made network requests');assert.deepEqual(errors,[],'browser console/page errors');
    const report={ok:true,offline:true,networkRequests:requests.length,consoleErrors:errors,fixtures:['known','oblique','permuted','missing','demo'],physicalMeasurements:true,multiSelection:true,multiMeasurementCards:true,multiAreaChart:true,batchSurfaceSelection:true,synchronizedSelectionChanges:true,oneClickBrush:true,workspaceNavigation:true,anisotropicPointerBrush:true,undoRedo:true,immutableOriginal:true,downloadsAndValidatedImport:true,synchronizedMpr:true,webglPixels:glCheck.colored,realSurfacePicking:true,meshEditUpdate:true,cancellation:true,mobilePhysicalAspect:true,capabilityFallbacks:true,allClasses:117,nonemptyMasks:12};
    fs.writeFileSync(path.join(root,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close();
}
main().catch(error=>{console.error(error);process.exit(1);});
