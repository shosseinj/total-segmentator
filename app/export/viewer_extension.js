/* Additions to the existing Persian viewer; no network, storage service or CDN. */
(() => {
'use strict';
const el=byId, kinds=['axial','coronal','sagittal'], titles={axial:'Axial',coronal:'Coronal',sagittal:'Sagittal'};
const pre=data.precomputed?.schema_version===1?{...data.precomputed}:null,planeCache=new Map(),dirtyLabels=new Set();
let metadataLoading=null,measurementsLoading=null;
function metadata(){if(!metadataLoading)metadataLoading=ViewerLoader.json(pre.metadata_url).then(value=>Object.assign(pre,value)).catch(error=>{metadataLoading=null;throw error;});return metadataLoading;}
function measurementData(){if(!measurementsLoading)measurementsLoading=ViewerLoader.json(pre.measurements_url).then(value=>pre.measurements=value).catch(error=>{measurementsLoading=null;throw error;});return measurementsLoading;}
const ui={ready:false,version:'edited',focus:Number(data.present[0]||Object.keys(data.labels)[0]),revision:0,processing:pre?'server-precomputed':'unavailable',localProcessor:false,
    reviews:{},measurements:{original:{},edited:{}},world:null,planes:[],mpr:false,busy:false,loadCancelled:false,view:'native',inspector:'measurements'};
let worker=null,engineLoading=null,nextId=0,pending=new Map(),loadSession=0,measurementRequest=0,nativeRequest=0,viewRequest=0,viewTimer=null;
let stroke=null,originalSlice=null,resolveReady,surface=null,selectionSignature='',selectionIdsSignature='',surfaceSelectionTimer=null;
const ready=new Promise(resolve=>resolveReady=resolve);
const markers=document.createElement('canvas');markers.id='markers';world.append(markers);const markerCtx=markers.getContext('2d');
const affine=data.geometry?.affine_ras_mm;
const point=(a,p)=>a.slice(0,3).map(r=>r[0]*p[0]+r[1]*p[1]+r[2]*p[2]+r[3]);
const fmt=v=>v===null||v===undefined||!Number.isFinite(v)?'ناموجود':fa.format(v);
const labelName=n=>data.labels_fa[n]||data.labels[n];
const activeRow=()=>ui.measurements[ui.version][ui.focus];
const message=(id,text)=>el(id).textContent=text;
function showError(error,id='editStatus'){if(['STALE','CANCELLED'].includes(error.message))return;message(id,'انجام نشد: '+error.message);const retry=id==='mprStatus'?requestViews:id==='surfaceStatus'?()=>surface?surface.refresh():showView('surface',true):id==='editStatus'&&!worker?()=>ensureLocal().catch(showError):null;if(retry){const button=document.createElement('button');button.textContent='تلاش دوباره';button.onclick=retry;el(id).append(button);}}
function makeWorker(){
    worker=createViewerLocalProcessor();ui.localProcessor=true;
    worker.onmessage=event=>{const {id,result,error,progress,value}=event.data,p= pending.get(id);if(!p)return;
        if(progress){p.progress?.(value);return;}pending.delete(id);if(error)p.reject(Error(error));else p.resolve(result);};
    worker.onerror=event=>{ui.ready=false;setEnabled(false);for(const p of pending.values())p.reject(Error(event.message||'WORKER_UNAVAILABLE'));pending.clear();message('analysisStatus','پردازش پس‌زمینه در این مرورگر در دسترس نیست. نمای اصلی همچنان قابل استفاده است.');};
}
function engineRpc(type,args={},progress,transfer=[]){
    if(!worker)return Promise.reject(Error('WORKER_UNAVAILABLE'));
    return new Promise((resolve,reject)=>{const id=++nextId;pending.set(id,{resolve,reject,progress});worker.postMessage({id,type,args},transfer);});
}
async function ensureLocal(){
    if(engineLoading)return engineLoading;
    engineLoading=(async()=>{
        await ViewerLoader.script('assets/mask-worker.js');
        makeWorker();message('editStatus','آماده‌سازی ویرایش آفلاین؛ محاسبات تغییرات این نسخه روی همین دستگاه انجام می‌شود…');
        await engineRpc('init',{shape:data.shape,geometry:data.geometry,labels:data.labels,source_identity:data.source_identity,original_fingerprint:data.original_fingerprint,authenticated_user:data.authenticated_user,include_ct:false});
        for(let z=0;z<data.shape[2];z++){
            const mask=await imagePixels(data.overlays[z]),labels=new Uint8Array(mask.width*mask.height);
            for(let i=0;i<labels.length;i++)labels[i]=mask.pixels[i*4];
            await engineRpc('loadSlice',{z,labels});
        }
        for(const [label,state] of Object.entries(ui.reviews))await engineRpc('review',{label:Number(label),state});
        message('editStatus','ویرایش آفلاین آماده است؛ تغییرات فقط محلی‌اند.');
    })().catch(error=>{terminateWorker();engineLoading=null;throw error;});
    return engineLoading;
}
function snapWorld(world){
    const {planes}=pre.mpr;
    return world.map((v,a)=>{const s=Object.values(planes).find(p=>p.axes[2]===a),i=Math.max(0,Math.min(s.frames.length-1,Math.round((s.upper[a]-v)/s.step_f-.5)));return s.upper[a]-(i+.5)*s.step_f;});
}
async function serverPlanes(world){
    await metadata();
    return Promise.all(kinds.map(async kind=>{
        const source=pre.mpr.planes[kind],f=source.axes[2],z=Math.max(0,Math.min(source.frames.length-1,Math.round((source.upper[f]-world[f])/source.step_f-.5))),key=kind+':'+z;
        let decoded=planeCache.get(key);
        if(!decoded){decoded=imagePixels(source.frames[z]).then(image=>{const length=source.width*source.height,pixels=new Uint16Array(length),labels=new Uint8Array(length),valid=new Uint8Array(length);for(let i=0;i<length;i++){pixels[i]=image.pixels[i*4]+256*image.pixels[i*4+1];labels[i]=image.pixels[i*4+2];valid[i]=image.pixels[i*4+3]?1:0;}return {pixels,labels,valid};}).catch(error=>{if(planeCache.get(key)===decoded)planeCache.delete(key);throw error;});planeCache.set(key,decoded);if(planeCache.size>6)planeCache.delete(planeCache.keys().next().value);}
        const {frames,...spec}=source;spec.fixed=source.upper[f]-(z+.5)*source.step_f;
        return {spec,...await decoded};
    }));
}
async function rpc(type,args={},progress,transfer=[]){
    if(!pre)return engineRpc(type,args,progress,transfer);
    const original=args.version==='original'||ui.revision===0;
    if(type==='measure'){
        await measurementData();
        const rows=Object.fromEntries(args.ids.filter(n=>args.version==='original'||!dirtyLabels.has(Number(n))).map(n=>[n,pre.measurements[n]])),remaining=args.ids.filter(n=>!rows[n]);
        if(remaining.length){await ensureLocal();Object.assign(rows,(await engineRpc(type,{...args,ids:remaining})).rows);}
        return {rows,revision:ui.revision,version:args.version};
    }
    if(type==='slice'&&original){const mask=await imagePixels(data.overlays[args.z]),pixels=new Uint8Array(mask.width*mask.height);for(let i=0;i<pixels.length;i++)pixels[i]=mask.pixels[i*4];return {pixels,revision:ui.revision,version:args.version};}
    if(type==='views'){
        const planes=await serverPlanes(args.world);
        if(!original){await ensureLocal();const changed=await engineRpc('views',{...args,specs:planes.map(p=>p.spec)});changed.planes.forEach((p,i)=>{planes[i]={...planes[i],labels:p.labels,valid:p.valid};});}
        return {planes,world:args.world,token:args.token,revision:ui.revision,version:args.version};
    }
    if(type==='meshes'){
        await metadata();
        const meshes=[],remaining=[];
        for(const id of args.ids){if(args.version!=='original'&&dirtyLabels.has(Number(id))){remaining.push(id);continue;}const asset=pre.surfaces.meshes[id];if(!asset)continue;if(asset.unavailable_reason)throw Error(asset.unavailable_reason);const image=await imagePixels(asset.png),bytes=new Uint8Array(asset.byte_length);for(let i=0;i<bytes.length;i++)bytes[i]=image.pixels[Math.floor(i/3)*4+i%3];const vertices=new Float32Array(bytes.buffer);meshes.push({id,vertices,count:asset.count});progress?.(meshes.length/args.ids.length);}
        if(remaining.length){await ensureLocal();meshes.push(...(await engineRpc(type,{...args,ids:remaining},progress)).meshes);}
        return {...pre.surfaces,meshes,revision:ui.revision,version:args.version};
    }
    if(type==='review'&&!engineLoading){if(!['approved','rejected','needs_correction'].includes(args.state)||!data.labels[args.label])throw Error('INVALID_REVIEW');return {...ui.reviews,[args.label]:args.state};}
    if(type==='export'&&!engineLoading)return {schema_version:'1.0',package_type:'ct_viewer_edits',source_identity:data.source_identity,original_fingerprint:data.original_fingerprint,shape:data.shape,geometry:data.geometry,labels:data.labels,encoding:'sparse_voxel_changes',encoding_description:'[flat_index, original_label, edited_label]; zero-based x + nx*(y + ny*z); background=0; display voxel grid; geometry.voxel_transform maps to original acquisition indices',changes:[],created_at:new Date().toISOString(),authenticated_user:data.authenticated_user||null,user_identity_context:data.authenticated_user?'authenticated result creator captured at generation; offline reviewer identity is unavailable':null,review_states:{...ui.reviews},relationship:'separate edited version of immutable original model result',saved_to_server:false};
    await ensureLocal();return engineRpc(type,args,progress,transfer);
}
function notify(type){worker?.postMessage({type});}
function terminateWorker(){worker?.terminate();worker=null;for(const p of pending.values())p.reject(Error('CANCELLED'));pending.clear();}
function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function setEnabled(value){for(const id of ['editingEnabled','resetMask','exportEdits','importEdits','startMpr','buildSurface','modeMpr','modeSurface','quickBrush'])el(id).disabled=!value;}
function updateVersion(){
    const editing=el('editingEnabled').checked&&ui.version==='edited';
    message('activeVersion',(ui.version==='original'?'نسخهٔ فعال: خروجی اصلی مدل؛ فقط خواندنی':'نسخهٔ فعال: ماسک ویرایشی جداگانه')+' · '+fmt(selected.size)+' ناحیهٔ انتخاب‌شده');
    el('editingEnabled').disabled=!ui.ready||ui.version!=='edited';if(!editing)el('editingEnabled').checked=false;
    el('reviewState').value=ui.reviews[ui.focus]||'unreviewed';el('reviewState').disabled=!ui.ready||ui.version!=='edited'||ui.busy;
    for(const id of ['maskVersion','exportEdits','importEdits'])el(id).disabled=!ui.ready||ui.busy;
    el('resetMask').disabled=!ui.ready||ui.version!=='edited'||ui.busy;
    el('undoEdit').disabled=!ui.ready||ui.version!=='edited'||!ui.undo||ui.busy;
    el('redoEdit').disabled=!ui.ready||ui.version!=='edited'||!ui.redo||ui.busy;
    el('quickBrush').disabled=!ui.ready||ui.version!=='edited'||ui.busy;el('quickBrush').classList.toggle('active',editing);message('nativeModeBadge',editing?'قلم روی '+labelName(ui.focus):'برش بومی');
    if(editing){stopPlay();stage.style.cursor='crosshair';}else stage.style.cursor=tool==='pan'?'grab':'crosshair';
}
function detail(){
    updateVersion();const out=el('measurementDetail'),ids=[...selected];out.replaceChildren();message('measurementCount',fmt(ids.length)+' ناحیه');
    if(!ids.length){const empty=document.createElement('div');empty.className='empty-selection';empty.textContent='از فهرست سمت راست، چند ناحیه را انتخاب کنید تا اندازه‌گیری و نمودار آن‌ها هم‌زمان دیده شود.';out.append(empty);}
    for(const n of ids){const r=ui.measurements[ui.version][n],card=document.createElement('article');card.className='measurement-card'+(n===ui.focus?' is-focus':'');card.dataset.measurement=String(n);card.style.setProperty('--region-color','rgb('+data.colors[n].join(',')+')');
        const head=document.createElement('div');head.className='measurement-card-header';const name=document.createElement('button');name.textContent=labelName(n);name.title='نمایش نشانگر و بررسی این ناحیه؛ انتخاب‌های دیگر حفظ می‌شوند';name.setAttribute('aria-pressed',String(n===ui.focus));name.onclick=()=>focus(n);
        const volume=document.createElement('span');volume.className='volume-number';volume.textContent=ui.ready?fmt(r?.volume_ml):'…';if(r?.count){const unit=document.createElement('small');unit.textContent=' mL';volume.append(unit);}head.append(name,volume);card.append(head);
        if(r?.count){const dims=document.createElement('div');dims.className='card-dimensions';const caption=document.createElement('span');caption.textContent='کادر R–L / A–P / I–S';const values=document.createElement('span');values.textContent=r.dimensions_mm.map(fmt).join(' × ')+' mm';dims.append(caption,values);card.append(dims);
            const footer=document.createElement('div');footer.className='card-footer';const area=document.createElement('span');area.textContent='بیشترین سطح: '+fmt(r.max_area_mm2)+' mm²';const go=document.createElement('button');go.textContent='برش '+fmt(r.max_slice+1)+' ←';go.title='رفتن به برش بیشترین سطح '+labelName(n);go.onclick=()=>{focus(n);navigateSection(r.max_slice);};footer.append(area,go);card.append(footer);
            if(r.boundary){const warning=document.createElement('p');warning.className='card-warning';warning.textContent='تماس با مرز اسکن · پوشش ممکن است ناقص باشد';card.append(warning);}
        }else{const empty=document.createElement('p');empty.textContent=ui.ready?'ماسک خالی؛ اندازه‌گیری ناموجود':'در حال آماده‌سازی اندازه‌گیری…';card.append(empty);}
        const review=ui.reviews[n];if(review){const state=document.createElement('p');state.className='small';state.dataset.review=review;state.textContent={approved:'تأییدشده',rejected:'ردشده',needs_correction:'نیازمند اصلاح'}[review];card.append(state);}out.append(card);
    }
    el('goMaximum').disabled=!ui.ready||!selected.has(ui.focus)||!activeRow()?.count;el('goMaximum').textContent='بیشترین سطحِ '+labelName(ui.focus);
    el('saveMeasurement').disabled=!ui.ready||!ids.some(n=>ui.measurements[ui.version][n]?.count);drawChart();drawMarkers();
}
function drawChart(){
    const c=el('areaChart'),context=c.getContext('2d'),legend=el('chartLegend');context.clearRect(0,0,c.width,c.height);legend.replaceChildren();
    const plotted=[...selected].map(n=>[n,ui.measurements[ui.version][n]]).filter(([,r])=>r?.count);c.dataset.seriesCount=plotted.length;if(!plotted.length)return;
    const padding=28,width=c.width-padding*2,height=c.height-padding*2,max=Math.max(...plotted.map(([,r])=>r.max_area_mm2)),length=plotted[0][1].areas.length;
    context.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--color-base-200').trim()||'#ddd';context.lineWidth=1;for(let j=0;j<3;j++){const y=padding+height*j/2;context.beginPath();context.moveTo(padding,y);context.lineTo(c.width-padding,y);context.stroke();}
    for(const [n,r] of plotted){const color='rgb('+data.colors[n].join(',')+')';context.strokeStyle=color;context.lineWidth=n===ui.focus?3:2;context.beginPath();r.areas.forEach((a,i)=>{const x=padding+width*i/Math.max(1,length-1),y=c.height-padding-height*a/max;i?context.lineTo(x,y):context.moveTo(x,y);});context.stroke();
        context.fillStyle=color;context.beginPath();context.arc(padding+width*r.max_slice/Math.max(1,length-1),c.height-padding-height*r.max_area_mm2/max,n===ui.focus?4:3,0,7);context.fill();
        const item=document.createElement('span'),dot=document.createElement('i');dot.style.background=color;item.append(dot,document.createTextNode(labelName(n)));legend.append(item);
    }
    context.fillStyle=getComputedStyle(document.body).color;context.font='12px Tahoma';context.fillText(fmt(max),padding,16);context.fillText('1',padding,c.height-6);context.fillText(String(length),c.width-padding-20,c.height-6);
    const slice=ui.world?worldToVoxel(ui.world)[data.geometry.section_axis]:index;context.strokeStyle='#98a2b3';context.setLineDash([4,3]);context.beginPath();const x=padding+width*slice/Math.max(1,length-1);context.moveTo(x,padding);context.lineTo(x,c.height-padding);context.stroke();context.setLineDash([]);el('areaSlice').max=length-1;el('areaSlice').value=Math.max(0,Math.min(length-1,Math.round(slice)));
}
function pairs(){
    const body=el('pairedTable').tBodies[0];body.replaceChildren();
    for(const [leftId,name] of Object.entries(data.labels)) {
        if(!/(^|_)left(?=_|$)/.test(name))continue;const right=Object.entries(data.labels).find(([,n])=>n===name.replace(/(^|_)left(?=_|$)/,'$1right'));if(!right)continue;
        const left=ui.measurements[ui.version][leftId],r=ui.measurements[ui.version][right[0]];if(!left?.count&&!r?.count)continue;
        const tr=document.createElement('tr'),nameCell=document.createElement('td');nameCell.textContent=labelName(leftId)+' / '+labelName(right[0]);tr.append(nameCell);
        for(const [id,row] of [[Number(leftId),left],[Number(right[0]),r]]){const td=document.createElement('td');td.textContent=row?.count?fmt(row.volume_ml)+' mL · '+row.dimensions_mm.map(fmt).join(' × ')+' mm':'ماسک خالی / ناموجود';if(row?.count){const b=document.createElement('button');b.textContent='رفتن به '+labelName(id);b.onclick=()=>{focus(id);navigateSection(row.max_slice);};td.append(b);}tr.append(td);}
        const valid=left?.volume_ml>0&&r?.volume_ml>0,abs=document.createElement('td'),percent=document.createElement('td');abs.textContent=valid?fmt(Math.abs(r.volume_ml-left.volume_ml)):'ناموجود';percent.textContent=valid?fmt(100*(r.volume_ml-left.volume_ml)/left.volume_ml)+'٪':'ناموجود (مرجع خالی یا صفر)';tr.append(abs,percent);body.append(tr);
    }
    if(!body.childElementCount){const tr=body.insertRow(),td=tr.insertCell();td.colSpan=5;td.textContent='جفت دارای ماسک غیرخالی در دسترس نیست.';}
}
function updateRows(){
    for(const item of rows){const n=Number(item.check.value),r=ui.measurements[ui.version][n];if(!r)continue;item.present=!!r.count;item.check.disabled=!r.count;item.row.classList.toggle('empty',!r.count);if(!r.count){selected.delete(n);item.check.checked=false;}}
    filter();count();
}
async function measure(ids=Object.keys(data.labels).map(Number)){
    const session=loadSession,request=++measurementRequest,version=ui.version;
    const result=await rpc('measure',{version,ids});if(session!==loadSession||version!==ui.version||request!==measurementRequest)return;
    Object.assign(ui.measurements[version],result.rows);ui.revision=result.revision;updateRows();selectionChanged(true);pairs();
}
function focus(n){
    ui.focus=Number(n);el('editTarget').value=String(n);
    const row=rows.find(r=>Number(r.check.value)===ui.focus);if(row?.present){selected.add(ui.focus);row.check.checked=true;}
    for(const r of rows)r.row.classList.toggle('focus-region',r===row);
    updateVersion();detail();if(labelPixels)overlay();drawPlanes();surface?.draw();
    if(ui.view==='surface'&&surface)surface.add(ui.focus).catch(error=>showError(error,'surfaceStatus'));
}
function load(){
    ++loadSession;ui.ready=!!pre;setEnabled(ui.ready);el('cancelAnalysis').disabled=true;
    if(!pre){message('analysisStatus','فایل‌های آمادهٔ نماهای پیشرفته موجود نیستند؛ نمای اصلی قابل استفاده است.');resolveReady(false);return;}
    ui.world=point(affine,[(data.shape[0]-1)/2,(data.shape[1]-1)/2,index]);selectionChanged(true);
    message('analysisStatus','برای بارگذاری اندازه‌گیری‌ها، بخش اندازه‌گیری را باز کنید.');
    el('analysisProgress').value=1;el('analysisLoading').classList.add('complete');resolveReady(true);
}
async function slice(z,pixels){
    originalSlice=new Uint8ClampedArray(pixels);if(!ui.ready||pre&&(ui.version==='original'||ui.revision===0))return pixels;
    const version=ui.version,request=++nativeRequest,session=loadSession;
    try{const result=await rpc('slice',{z,version});if(request!==nativeRequest||version!==ui.version||session!==loadSession)throw Error('STALE');
        const rgba=new Uint8ClampedArray(pixels);for(let i=0;i<result.pixels.length;i++)rgba[i*4]=result.pixels[i];return rgba;
    }catch(error){throw error;}
}
function nativeRendered(){
    markers.width=ct.width;markers.height=ct.height;
    message('position',affine?'مبدأ صفحه (RAS mm): '+point(affine,[0,0,index]).map(fmt).join(' ، '):'مختصات بیمار ناموجود');
    if(ui.world&&ui.view==='native'){const p=worldToVoxel(ui.world);p[2]=index;ui.world=point(affine,p);positionText();if(ui.mpr)requestViews();}
    drawMarkers();drawChart();fitNativeWorld();
}
let inverse=null;
function worldToVoxel(p){if(!inverse)return [0,0,index];return point(inverse,p);}
function drawMarkers(){
    if(markers.width!==ct.width||markers.height!==ct.height){markers.width=ct.width;markers.height=ct.height;}
    markerCtx.clearRect(0,0,markers.width,markers.height);if(!ui.ready)return;
    if(el('compareOriginal').checked&&selected.has(ui.focus)&&ui.version==='edited'&&originalSlice){markerCtx.fillStyle='#00ffff';const w=ct.width,h=ct.height;for(let y=0;y<h;y++)for(let x=0;x<w;x++){const i=4*(x+w*y);if(originalSlice[i]===ui.focus&&(x===0||y===0||x===w-1||y===h-1||originalSlice[i-4]!==ui.focus||originalSlice[i+4]!==ui.focus||originalSlice[i-4*w]!==ui.focus||originalSlice[i+4*w]!==ui.focus))markerCtx.fillRect(x,y,1,1);}}
    const r=activeRow();if(el('showMarkers').checked&&selected.has(ui.focus)&&r?.count){
        markerCtx.strokeStyle='#ffe16b';markerCtx.lineWidth=1;markerCtx.setLineDash([4,3]);markerCtx.strokeRect(r.voxel_lower[0],r.voxel_lower[1],r.voxel_upper[0]-r.voxel_lower[0]+1,r.voxel_upper[1]-r.voxel_lower[1]+1);markerCtx.setLineDash([]);
        const a=data.geometry.section_axis;
        if(a!==2||index===r.max_slice){markerCtx.fillStyle='#ffe16b';const w=ct.width,h=ct.height;for(let y=0;y<h;y++)for(let x=0;x<w;x++){if(a===0&&x!==r.max_slice||a===1&&y!==r.max_slice)continue;const i=4*(x+w*y);if(labelPixels?.[i]===ui.focus&&(a!==2||x===0||y===0||x===w-1||y===h-1||labelPixels[i-4]!==ui.focus||labelPixels[i+4]!==ui.focus||labelPixels[i-4*w]!==ui.focus||labelPixels[i+4*w]!==ui.focus))markerCtx.fillRect(x,y,1,1);}}
    }
    if(ui.mpr&&ui.world){const p=worldToVoxel(ui.world);markerCtx.strokeStyle='#ff8c28';markerCtx.lineWidth=1;markerCtx.beginPath();markerCtx.moveTo(p[0]+.5,0);markerCtx.lineTo(p[0]+.5,ct.height);markerCtx.moveTo(0,p[1]+.5);markerCtx.lineTo(ct.width,p[1]+.5);markerCtx.stroke();}
}
function positionText(){if(ui.world)message('worldPosition','RAS mm: '+ui.world.map(v=>Number(v.toFixed(3))).join(' , '));}
async function navigateWorld(p,followNative=true){
    ui.world=p.slice();positionText();
    if(followNative){const voxel=worldToVoxel(p),z=Math.max(0,Math.min(data.shape[2]-1,Math.round(voxel[2])));if(z!==index){const preserved=p.slice();await render(z);ui.world=preserved;positionText();}}
    drawMarkers();if(ui.mpr)requestViews();
}
async function navigateSection(s){showView('native');const r=activeRow();if(!r)return;const native=r.section_points[s]||worldToVoxel(ui.world||point(affine,[0,0,index]));native[data.geometry.section_axis]=Number(s);await navigateWorld(point(affine,native));}
async function changed(result){
    if(!result.affected?.length){updateVersion();return;}
    ui.revision=result.revision;ui.reviews=result.reviews||ui.reviews;ui.undo=result.undo;ui.redo=result.redo;
    for(const n of result.affected){dirtyLabels.add(Number(n));delete ui.measurements.edited[n];}
    surface?.invalidate(result.affected);updateVersion();message('editStatus','نسخهٔ ویرایشی تغییر کرد؛ بازبینی دوباره لازم است. فقط محلی؛ روی سرور ذخیره نشده است.');
    await measure(result.affected);focus(ui.focus);await render(index);surface?.refresh(result.affected);
}
async function mutate(type,args={}){
    if(ui.busy||!ui.ready||ui.version!=='edited')return;ui.busy=true;updateVersion();
    try{const result=await rpc(type,args);await changed(result);}catch(error){showError(error);}finally{ui.busy=false;updateVersion();}
}
function requestViews(){
    if(!ui.mpr||ui.view!=='mpr'||!ui.ready)return;clearTimeout(viewTimer);notify('cancelViews');const token=++viewRequest;
    if(ui.planes.length)ui.planeSpecs=ui.planes.map(p=>p.spec);ui.planes=[];for(const kind of kinds){const c=el('mpr'+titles[kind]);c.getContext('2d').clearRect(0,0,c.width,c.height);}
    viewTimer=setTimeout(async()=>{const version=ui.version,captured=ui.revision;message('mprStatus',pre?'بازکردن نماهای آمادهٔ سرور…':'بازسازی نماهای همگام…');try{if(pre){await metadata();if(token!==viewRequest||ui.view!=='mpr')return;ui.world=snapWorld(ui.world);positionText();}const result=await rpc('views',{world:ui.world.slice(),version,token});if(token!==viewRequest||!ui.mpr||ui.view!=='mpr'||captured!==ui.revision||version!==ui.version)return;ui.planes=result.planes;drawPlanes();message('mprStatus','سه نما همگام‌اند · برای جابه‌جایی نشانگر روی تصویر کلیک کنید.');}catch(error){if(token===viewRequest)showError(error,'mprStatus');}},30);
}
function drawPlanes(){
    const mount=el('mprMount');if(!mount.clientWidth||!mount.clientHeight)return;const grid=el('mprPanel').querySelector('.mpr-grid');grid.style.flex='none';grid.style.height=Math.max(180,mount.clientHeight-el('mprPanel').querySelector('.tools').offsetHeight-el('mprPanel').querySelector('.mpr-status').offsetHeight-52)+'px';
    for(const plane of ui.planes){const {spec,pixels,labels,valid}=plane,c=el('mpr'+titles[spec.kind]),context=c.getContext('2d');c.width=spec.width;c.height=spec.height;
        const physicalAspect=(spec.width*spec.step_u)/(spec.height*spec.step_v),figure=c.parentElement,available=Math.max(1,figure.clientWidth-16),availableHeight=Math.max(1,figure.clientHeight-figure.querySelector('figcaption').offsetHeight-46),cssWidth=Math.min(available,availableHeight*physicalAspect);c.style.width=cssWidth+'px';c.style.height=(cssWidth/physicalAspect)+'px';
        const image=context.createImageData(c.width,c.height),level=Number(el('level').value),ww=Math.max(1,Number(el('width').value)||1),opacity=Number(el('opacity').value)/100;
        for(let i=0;i<pixels.length;i++){if(!valid[i]){image.data[i*4+3]=255;continue;}const hu=pixels[i]*data.hu_step+data.hu_offset;let value=Math.round(Math.max(0,Math.min(1,(hu-level+ww/2)/ww))*255);if(el('invert').checked)value=255-value;let color=[value,value,value],n=labels[i],edge=i%c.width===0||i%c.width===c.width-1||i<c.width||i>=pixels.length-c.width||labels[i-1]!==n||labels[i+1]!==n||labels[i-c.width]!==n||labels[i+c.width]!==n;
            if(el('visible').checked&&selected.has(n)&&(!el('contour').checked||edge))color=data.colors[n].map(v=>Math.round(v*opacity+value*(1-opacity)));
            if(el('showMarkers').checked&&selected.has(n)&&n===ui.focus&&edge)color=[255,225,107];image.data.set([...color,255],i*4);
        }context.putImageData(image,0,0);
        const [u,v,f]=spec.axes,r=activeRow();
        if(el('showMarkers').checked&&selected.has(ui.focus)&&r?.count&&spec.fixed>=r.lower[f]&&spec.fixed<=r.upper[f]){context.strokeStyle='#ffe16b';context.setLineDash([4,3]);context.strokeRect((spec.upper[u]-r.upper[u])/spec.step_u,(spec.upper[v]-r.upper[v])/spec.step_v,r.dimensions_mm[u]/spec.step_u,r.dimensions_mm[v]/spec.step_v);context.setLineDash([]);}
        if(ui.world){const x=(spec.upper[u]-ui.world[u])/spec.step_u,y=(spec.upper[v]-ui.world[v])/spec.step_v;context.strokeStyle='#ff8c28';context.beginPath();context.moveTo(x,0);context.lineTo(x,c.height);context.moveTo(0,y);context.lineTo(c.width,y);context.stroke();}
        const slider=el('mpr'+titles[spec.kind]+'Slice');slider.min=spec.lower[f]+(spec.step_f||0)/2;slider.max=spec.upper[f]-(spec.step_f||0)/2;slider.step=spec.step_f||Math.min(spec.step_u,spec.step_v)/4;slider.value=spec.fixed;
    }
}
function clearMpr(){ui.mpr=false;viewRequest++;clearTimeout(viewTimer);notify('cancelViews');ui.planes=[];for(const kind of kinds){const c=el('mpr'+titles[kind]);c.width=c.height=1;c.style.height='auto';}drawMarkers();message('mprStatus','نماهای بازسازی‌شده آزاد شدند.');}
for(const kind of kinds){const c=el('mpr'+titles[kind]);c.onclick=e=>{const p=ui.planes.find(p=>p.spec.kind===kind);if(!p)return;const s=p.spec,b=c.getBoundingClientRect(),w=ui.world.slice();w[s.axes[0]]=s.upper[s.axes[0]]-(e.clientX-b.left)/b.width*s.width*s.step_u;w[s.axes[1]]=s.upper[s.axes[1]]-(e.clientY-b.top)/b.height*s.height*s.step_v;navigateWorld(w);};el('mpr'+titles[kind]+'Slice').oninput=e=>{const spec=ui.planes.find(p=>p.spec.kind===kind)?.spec||ui.planeSpecs?.find(s=>s.kind===kind);if(!spec)return;const w=ui.world.slice();w[spec.axes[2]]=Number(e.target.value);navigateWorld(w);};}
function consume(e){e.preventDefault();e.stopImmediatePropagation();}
stage.addEventListener('pointerdown',e=>{
    if(e.button!==0||!ui.ready||loading)return;
    if(tool==='crosshairTool'){consume(e);const p=pointAt(e);if(inside(p))navigateWorld(point(affine,[p.x-.5,p.y-.5,index]),false);return;}
    if(!el('editingEnabled').checked||ui.version!=='edited'||ui.busy)return;consume(e);const p=pointAt(e);if(!inside(p))return;
    const diameter=Number(el('brushDiameter').value);if(!Number.isFinite(diameter)||diameter<=0||diameter>200){message('editStatus','قطر قلم باید بین ۰٫۱ و ۲۰۰ میلی‌متر باشد.');return;}
    stopPlay();stroke={label:ui.focus,z:index,diameter,erase:el('brushMode').value==='erase',paths:[[p.x-.5,p.y-.5]]};stage.setPointerCapture(e.pointerId);drawStroke();
},true);
function drawStroke(){drawMarkers();if(!stroke)return;markerCtx.strokeStyle=stroke.erase?'#ff7777':'#ffffff';markerCtx.lineWidth=1;markerCtx.beginPath();stroke.paths.forEach((p,i)=>i?markerCtx.lineTo(p[0]+.5,p[1]+.5):markerCtx.moveTo(p[0]+.5,p[1]+.5));markerCtx.stroke();const p=stroke.paths.at(-1);markerCtx.beginPath();markerCtx.ellipse(p[0]+.5,p[1]+.5,stroke.diameter/(2*data.geometry.spacing_mm[0]),stroke.diameter/(2*data.geometry.spacing_mm[1]),0,0,7);markerCtx.stroke();}
stage.addEventListener('pointermove',e=>{if(!stroke)return;consume(e);const p=pointAt(e);if(inside(p)&&stroke.paths.length<20000){stroke.paths.push([p.x-.5,p.y-.5]);drawStroke();}},true);
stage.addEventListener('pointerup',e=>{if(!stroke)return;consume(e);const operation=stroke;stroke=null;mutate('brush',operation);},true);
for(const name of ['pointercancel','lostpointercapture'])stage.addEventListener(name,()=>{if(stroke){stroke=null;drawMarkers();}},true);
stage.addEventListener('wheel',e=>{if(stroke)consume(e);},{capture:true,passive:false});
document.addEventListener('keydown',e=>{if(stroke&&['ArrowRight','ArrowLeft','ArrowUp','ArrowDown',' '].includes(e.key))consume(e);},true);
message('editIdentity',data.authenticated_user?'هویت سازندهٔ نتیجه در زمان تولید ثبت شده است: '+data.authenticated_user.actor_id+'؛ هویت بازبین آفلاین احراز نمی‌شود.':'هویت کاربر احرازشده در این فایل در دسترس نیست؛ هویت بازبین ساخته یا حدس زده نمی‌شود.');
const options=el('editTarget');for(const [n,name] of Object.entries(data.labels)){const option=document.createElement('option');option.value=n;option.textContent=labelName(n)+' · '+name;options.append(option);}options.value=ui.focus;options.onchange=()=>focus(options.value);
el('maskVersion').onchange=async e=>{if(ui.busy)return;ui.busy=true;ui.version=e.target.value;el('editingEnabled').checked=false;surface?.invalidate();updateVersion();try{await measure();await render(index);surface?.refresh();}catch(error){showError(error);}finally{ui.busy=false;updateVersion();}};
el('editingEnabled').onchange=()=>{if(el('editingEnabled').checked)showView('native');updateVersion();};
el('compareOriginal').onchange=drawMarkers;
el('showMarkers').onchange=()=>{drawMarkers();drawPlanes();};
el('undoEdit').title='واگرد · Ctrl+Z';el('redoEdit').title='ازنو · Ctrl+Shift+Z';
document.addEventListener('keydown',e=>{if(ui.inspector!=='editing'||['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)||!(e.ctrlKey||e.metaKey)||e.key.toLowerCase()!=='z')return;e.preventDefault();mutate(e.shiftKey?'redo':'undo');});
el('undoEdit').onclick=()=>mutate('undo');el('redoEdit').onclick=()=>mutate('redo');el('resetMask').onclick=()=>mutate('resetLabel',{label:ui.focus});
el('reviewState').onchange=async e=>{if(e.target.value==='unreviewed'||ui.version!=='edited')return;try{ui.reviews=await rpc('review',{label:ui.focus,state:e.target.value});selectionChanged(true);message('editStatus','وضعیت بازبینی محلی ثبت شد؛ برای نگه‌داری بستهٔ ویرایشی را دریافت کنید.');}catch(error){showError(error);}};
el('exportEdits').onclick=async()=>{try{download(await rpc('export'),'CT-edits-'+data.job_id+'.json');message('editStatus','بستهٔ نسخهٔ ویرایشی دریافت شد؛ روی سرور ذخیره نشده است.');}catch(error){showError(error);}};
el('importEdits').onchange=async e=>{const file=e.target.files[0];e.target.value='';if(!file)return;try{if(file.size>256*1024*1024)throw Error('EDIT_FILE_TOO_LARGE');const pkg=JSON.parse(await file.text());if(ui.version!=='edited'){ui.version='edited';el('maskVersion').value='edited';}ui.busy=true;const result=await rpc('import',{package:pkg});await changed(result);ui.reviews=result.reviews;await measure();updateVersion();message('editStatus','بسته پس از اعتبارسنجی هویت، اثر انگشت و هندسه وارد شد. بازبینی دوباره لازم است؛ فقط محلی.');}catch(error){showError(error);}finally{ui.busy=false;updateVersion();}};
el('goMaximum').onclick=()=>navigateSection(activeRow().max_slice);
el('areaSlice').oninput=e=>navigateSection(Number(e.target.value));
el('areaChart').onclick=e=>{const r=activeRow();if(!r?.count)return;const b=e.target.getBoundingClientRect(),x=(e.clientX-b.left)*640/b.width,s=Math.max(0,Math.min(r.areas.length-1,Math.round((x-28)/(640-56)*(r.areas.length-1))));navigateSection(s);};
el('saveMeasurement').onclick=()=>download({schema_version:'1.0',source_identity:data.source_identity,original_fingerprint:data.original_fingerprint,geometry:data.geometry,label:ui.focus,label_name:data.labels[ui.focus],mask_version:ui.version,measurement:activeRow(),selected_measurements:[...selected].map(n=>({label:n,label_name:data.labels[n],measurement:ui.measurements[ui.version][n]})),created_at:new Date().toISOString(),saved_to_server:false},'CT-measurement-'+ui.focus+'.json');
el('cancelAnalysis').onclick=()=>{ui.loadCancelled=true;++loadSession;terminateWorker();ui.ready=false;setEnabled(false);message('analysisStatus','آماده‌سازی لغو شد؛ نمای اصلی قابل استفاده است.');el('retryAnalysis').hidden=false;el('cancelAnalysis').disabled=true;};
el('retryAnalysis').onclick=openMeasurements;
el('startMpr').onclick=()=>{showView('mpr');el('mprPanel').open=true;ui.mpr=true;positionText();requestViews();drawMarkers();};
el('cancelMpr').onclick=clearMpr;
el('mprPanel').addEventListener('toggle',()=>{if(!el('mprPanel').open&&ui.mpr)clearMpr();});
el('crosshairTool').onclick=()=>{showView('native');el('editingEnabled').checked=false;setTool('crosshairTool');message('help','روی تصویر اصلی بزنید تا محل فیزیکی مشترک سه نما انتخاب شود.');};
for(const id of ['opacity','visible','contour','level','width','invert','preset'])el(id).addEventListener(['visible','contour','invert','preset'].includes(id)?'change':'input',drawPlanes);
el('themePicker').addEventListener('change',drawChart);window.addEventListener('resize',()=>{if(ui.planes.length)drawPlanes();});
el('mprMount').append(el('mprPanel'));el('surfaceMount').append(el('surfacePanel'));
function fitNativeWorld(){
    const spacing=data.geometry?.valid?data.geometry.spacing_mm:[1,1,1],aspect=data.shape[0]*spacing[0]/(data.shape[1]*spacing[1]),width=stage.clientWidth,height=stage.clientHeight;if(!width||!height)return;
    const fittedWidth=Math.min(width,height*aspect);world.style.width=fittedWidth+'px';world.style.height=(fittedWidth/aspect)+'px';
}
async function showView(mode,initialize=false){
    if(initialize&&mode!=='native'&&!ui.ready)return;ui.view=mode;if(mode!=='native'){el('editingEnabled').checked=false;updateVersion();}
    el('nativeView').hidden=mode!=='native';el('mprMount').hidden=mode!=='mpr';el('surfaceMount').hidden=mode!=='surface';
    for(const button of document.querySelectorAll('[data-view]')){const active=button.dataset.view===mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}
    if(mode==='mpr'){el('mprPanel').open=true;if(initialize)el('startMpr').click();}
    if(mode==='surface'){el('surfacePanel').open=true;if(initialize){try{await metadata();await ViewerLoader.script('assets/surface-viewer.js');if(!surface){surface=new OfflineSurface({el,rpc,notify,ui,data,focus,navigateWorld,showError,getSelected:()=>[...selected],beforeShow:()=>showView('surface'),getRow:n=>ui.measurements[ui.version][n]||pre.surfaces.structures[n]});window.ViewerExtension.surface=surface;}if(ui.view==='surface')await surface.setSelection([ui.focus]);}catch(error){showError(error,'surfaceStatus');}}}
    requestAnimationFrame(()=>{fitNativeWorld();drawPlanes();surface?.draw();});
}
async function openMeasurements(){el('analysisLoading').classList.remove('complete');message('analysisStatus','در حال بارگذاری اندازه‌گیری‌ها…');el('retryAnalysis').hidden=true;try{await measure();message('analysisStatus','اندازه‌گیری‌ها آماده‌اند.');el('analysisLoading').classList.add('complete');}catch(error){showError(error,'analysisStatus');el('retryAnalysis').hidden=false;}}
function showInspector(name){
    if(name==='measurements')openMeasurements();else ensureLocal().catch(showError);
    ui.inspector=name;const editing=name==='editing';if(!editing){el('editingEnabled').checked=false;updateVersion();}el('editingPanel').hidden=!editing;el('measurementPanel').hidden=editing;el(editing?'editingPanel':'measurementPanel').open=true;
    for(const [id,active] of [['tabEditing',editing],['tabMeasurements',!editing]]){el(id).classList.toggle('active',active);el(id).setAttribute('aria-pressed',String(active));}
}
function selectionChanged(force=false){
    const ids=[...selected],idSignature=ids.join(','),signature=idSignature+'|'+ui.version+'|'+ui.revision+'|'+ui.ready+'|'+ui.focus+'|'+JSON.stringify(ui.reviews);
    if(!force&&signature===selectionSignature)return;const idsChanged=idSignature!==selectionIdsSignature;selectionSignature=signature;selectionIdsSignature=idSignature;
    if(ids.length&&!selected.has(ui.focus)&&!el('editingEnabled').checked){ui.focus=ids[0];el('editTarget').value=String(ui.focus);}
    message('selectionCount',fmt(ids.length));message('selectionFooter',fmt(ids.length)+' ناحیه به‌صورت هم‌زمان انتخاب شده است');
    const chips=el('selectedChips');chips.replaceChildren();if(!ids.length){const empty=document.createElement('span');empty.className='selection-empty';empty.textContent='با تیک‌زدن ناحیه‌ها، انتخاب خود را بسازید';chips.append(empty);}
    for(const n of ids){const chip=document.createElement('span');chip.className='selection-chip'+(n===ui.focus?' is-focus':'');chip.dataset.selectedRegion=String(n);const dot=document.createElement('i');dot.className='dot';dot.style.background='rgb('+data.colors[n].join(',')+')';const name=document.createElement('button');name.textContent=labelName(n);name.onclick=()=>focus(n);const remove=document.createElement('button');remove.className='chip-remove';remove.textContent='×';remove.setAttribute('aria-label','حذف '+labelName(n)+' از انتخاب');remove.onclick=()=>{selected.delete(n);const row=rows.find(r=>Number(r.check.value)===n);if(row)row.check.checked=false;overlay();filter();};chip.append(dot,name,remove);chips.append(chip);}
    for(const item of rows){const n=Number(item.check.value),r=ui.measurements[ui.version][n];item.row.classList.toggle('focus-region',n===ui.focus&&selected.has(n));if(!item.volumeNode){item.volumeNode=document.createElement('span');item.volumeNode.className='region-volume';item.row.insertBefore(item.volumeNode,item.row.querySelector('.go'));}item.volumeNode.textContent=r?.count?fmt(r.volume_ml)+' mL':'';}
    for(const button of document.querySelectorAll('[data-select-group]'))button.disabled=!rows.some(row=>row.present&&data.labels[row.check.value].includes(button.dataset.selectGroup));
    filter();detail();
    if(idsChanged&&ui.view==='surface'&&surface?.gl){surface.setSelection([...surface.entries.keys()].filter(n=>selected.has(n)));}
}
for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>showView(button.dataset.view,true);
el('tabMeasurements').onclick=()=>showInspector('measurements');el('tabEditing').onclick=()=>showInspector('editing');
el('editingPanel').addEventListener('toggle',()=>{if(el('editingPanel').open)showInspector('editing');});
el('measurementPanel').addEventListener('toggle',()=>{if(el('measurementPanel').open)showInspector('measurements');});
el('quickBrush').onclick=()=>{showInspector('editing');showView('native');el('editingEnabled').checked=!el('editingEnabled').checked;updateVersion();};
for(const id of ['pan','windowTool','rulerTool'])el(id).addEventListener('click',()=>{el('editingEnabled').checked=false;updateVersion();});
el('onlySelected').onchange=filter;
for(const button of document.querySelectorAll('[data-select-group]'))button.onclick=()=>{for(const row of rows){const n=Number(row.check.value);if(row.present&&data.labels[n].includes(button.dataset.selectGroup)){selected.add(n);row.check.checked=true;}}overlay();};
el('toggleStructures').onclick=()=>{const open=el('structureSidebar').classList.toggle('mobile-open');el('structureSidebar').style.top=(document.querySelector('.app-header').getBoundingClientRect().bottom+8)+'px';el('toggleStructures').setAttribute('aria-expanded',String(open));};
document.addEventListener('pointerdown',e=>{if(window.innerWidth<=980&&!el('structureSidebar').contains(e.target)&&!el('toggleStructures').contains(e.target)){el('structureSidebar').classList.remove('mobile-open');el('toggleStructures').setAttribute('aria-expanded','false');}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){el('structureSidebar').classList.remove('mobile-open');el('toggleStructures').setAttribute('aria-expanded','false');}});
if(typeof ResizeObserver!=='undefined'){const observer=new ResizeObserver(()=>{fitNativeWorld();drawPlanes();surface?.draw();});observer.observe(el('viewColumn'));observer.observe(stage);}
selectionChanged(true);requestAnimationFrame(fitNativeWorld);
window.ViewerExtension={state:ui,ready,rpc,focus,slice,nativeRendered,drawMarkers,
    value:n=>ui.ready?ui.measurements[ui.version][n]?.volume_ml:data.geometry?.valid?data.volumes_ml[n]:null,
    clearNativeMarkers:()=>markerCtx.clearRect(0,0,markers.width,markers.height),
    afterOverlay:()=>{selectionChanged();drawMarkers();drawPlanes();},navigateWorld,navigateSection,worldToVoxel,point,measure,mutate,download,
    showView,showInspector,selectionChanged,selected,exportPackage:()=>rpc('export'),importPackage:async p=>{const r=await rpc('import',{package:p});await changed(r);return r;}};
if(!data.geometry?.valid){
    message('geometryWarning',data.geometry?.unavailable_reason||'مبدأ، جهت و واحد فیزیکی معتبر در دسترس نیست.');message('analysisStatus','اندازه‌گیری، قلم میلی‌متری و نماهای تشریحی / سه‌بعدی غیرفعال‌اند.');setEnabled(false);el('cancelAnalysis').disabled=true;el('reviewState').disabled=true;
    for(const node of stage.querySelectorAll('.orientation'))node.hidden=true;
    message('geometry',data.shape.map(fmt).join(' × ')+' وکسل؛ فاصله، جهت یا واحد فیزیکی معتبر موجود نیست.');
    const sectionLabel=el('slice').nextElementSibling?.firstElementChild;if(sectionLabel)sectionLabel.textContent='صفحهٔ شبکهٔ CT؛ جهت بیمار ناموجود';
    el('rulerTool').disabled=true;message('sectionDefinition','جهت بیمار مشخص نیست. نمایش صفحه‌های شبکهٔ CT همچنان قابل استفاده است.');resolveReady(false);count();return;
}
// Inversion uses the same affine convention as the worker.
{
    const a=affine,b=a.slice(0,3).map(r=>r.slice(0,3)),det=b[0][0]*(b[1][1]*b[2][2]-b[1][2]*b[2][1])-b[0][1]*(b[1][0]*b[2][2]-b[1][2]*b[2][0])+b[0][2]*(b[1][0]*b[2][1]-b[1][1]*b[2][0]);inverse=Array.from({length:4},()=>[0,0,0,0]);
    for(let i=0;i<3;i++)for(let j=0;j<3;j++)inverse[i][j]=(b[(j+1)%3][(i+1)%3]*b[(j+2)%3][(i+2)%3]-b[(j+1)%3][(i+2)%3]*b[(j+2)%3][(i+1)%3])/det;
    for(let i=0;i<3;i++)inverse[i][3]=-inverse[i].slice(0,3).reduce((s,v,j)=>s+v*a[j][3],0);inverse[3][3]=1;
}
message('sectionDefinition','سطح‌ها روی برش‌های بومی اخذ تصویر، بدون بازنمونه‌گیری اندازه‌گیری می‌شوند (محور شبکهٔ '+(data.geometry.section_axis+1)+'). '+(data.geometry.section_is_patient_axial?'این صفحات با صفحهٔ محوری بیمار هم‌جهت‌اند.':'این صفحات محوری تشریحی بیمار نیستند؛ ممکن است مایل یا با جهت دیگر باشند.'));
const aligned=affine.slice(0,3).every((row,i)=>row.slice(0,3).every((v,j)=>i===j||Math.abs(v)<1e-5));
if(!aligned){for(const node of stage.querySelectorAll('.orientation'))node.hidden=true;message('position','صفحهٔ بومی؛ جهت دقیق در نماهای بازسازی‌شده');}
const sectionLabel=el('slice').nextElementSibling?.firstElementChild;if(sectionLabel)sectionLabel.textContent='صفحهٔ شبکهٔ اصلی · سطح‌ها: برش بومی اخذ تصویر';
// Ruler distance uses the full affine metric, including a possible shear.
const originalDrawRuler=drawRuler;drawRuler=function(){originalDrawRuler();if(rulerPoints.length===2){const [a,b]=rulerPoints,d=[a.x-b.x,a.y-b.y,0],mm=Math.hypot(...affine.slice(0,3).map(row=>row[0]*d[0]+row[1]*d[1]));message('rulerValue','فاصلهٔ درون همین برش: '+fmt(mm)+' میلی‌متر');}};
message('geometry',data.shape.map(fmt).join(' × ')+' وکسل · فاصلهٔ معتبر: '+data.geometry.spacing_mm.map(fmt).join(' × ')+' میلی‌متر');
fitNativeWorld();
load();
})();
