/* Worker and yielding main-thread fallback share exactly the same engine. */
(function(factory){
if(typeof document==='undefined'){factory(self,(message,transfer)=>postMessage(message,transfer));return;}
window.createViewerLocalProcessor=function(){
const scope={};let stopped=false;
const processor={onmessage:null,postMessage(message){setTimeout(()=>{if(!stopped)scope.onmessage({data:message});},0);},terminate(){stopped=true;}};
factory(scope,message=>{if(!stopped)processor.onmessage?.({data:message});});return processor;
};
})(function(self,postMessage){
/* Offline worker. Arrays are x-fast: x + nx * (y + ny*z).
 * Original CT/labels are private and never written after loadSlice().
 * Surfaces are exact exposed voxel faces; all measurements use original-grid voxels.
 */
'use strict';
const yieldTask = () => new Promise(resolve => setTimeout(resolve, 0));
let config, shape, affine, inverse, original, edited, ctValues, revision = 0;
let history = [], future = [], historyBytes = 0, reviews = {}, measureCache = new Map();
let meshToken = 0, viewToken = 0;
const HISTORY_LIMIT = 32 * 1024 * 1024;
const CHANGE_BYTES = 96; // Conservative estimate for JS tuple/number overhead.
const point = (a, p) => a.slice(0, 3).map(row => row[0]*p[0]+row[1]*p[1]+row[2]*p[2]+row[3]);
const offset = (x,y,z) => x + shape[0]*(y + shape[1]*z);
const coordinates = i => [i%shape[0], Math.floor(i/shape[0])%shape[1], Math.floor(i/(shape[0]*shape[1]))];
const activeMask = version => version === 'original' ? original : edited;
const subtract = (a,b) => a.map((v,i)=>v-b[i]);
const cross = (a,b) => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const norm = a => Math.hypot(...a);
function invertAffine(a) {
    const b=a.slice(0,3).map(r=>r.slice(0,3)), det=b[0][0]*(b[1][1]*b[2][2]-b[1][2]*b[2][1])-b[0][1]*(b[1][0]*b[2][2]-b[1][2]*b[2][0])+b[0][2]*(b[1][0]*b[2][1]-b[1][1]*b[2][0]);
    if (!Number.isFinite(det)||Math.abs(det)<1e-12) throw Error('INVALID_AFFINE');
    const r=Array.from({length:4},()=>[0,0,0,0]);
    for(let i=0;i<3;i++)for(let j=0;j<3;j++)r[i][j]=(b[(j+1)%3][(i+1)%3]*b[(j+2)%3][(i+2)%3]-b[(j+1)%3][(i+2)%3]*b[(j+2)%3][(i+1)%3])/det;
    for(let i=0;i<3;i++)r[i][3]=-r[i].slice(0,3).reduce((s,v,j)=>s+v*a[j][3],0);
    r[3][3]=1;return r;
}
function requireGeometry() {if(!config.geometry.valid)throw Error(config.geometry.unavailable_reason);}
function geometryConstants() {
    const b=affine.slice(0,3).map(r=>r.slice(0,3));
    const columns=[0,1,2].map(j=>b.map(r=>r[j]));
    return {half:b.map(r=>r.reduce((s,v)=>s+Math.abs(v),0)/2),
        voxel:Math.abs(columns[0].reduce((s,v,i)=>s+v*cross(columns[1],columns[2])[i],0)),
        area:norm(cross(columns[(config.geometry.section_axis+1)%3],columns[(config.geometry.section_axis+2)%3]))};
}
function measurementRow(n) {
    return {id:n,count:0,volume_ml:null,dimensions_mm:null,lower:null,upper:null,voxel_lower:null,voxel_upper:null,
        areas:Array(shape[config.geometry.section_axis]).fill(0),max_slice:null,max_area_mm2:null,
        section_points:[],boundary:false,status:'empty',revision};
}
async function measurements(version, ids) {
    requireGeometry();const mask=activeMask(version), captured=revision, constants=geometryConstants(), needed=[];
    const result={};
    for(const id of ids) {const key=version+':'+id;if(measureCache.has(key))result[id]=measureCache.get(key);else needed.push(Number(id));}
    if(needed.length) {
        const target=new Set(needed), stats=Object.fromEntries(needed.map(n=>[n,measurementRow(n)]));
        const axis=config.geometry.section_axis;
        for(let z=0;z<shape[2];z++) {
            for(let y=0;y<shape[1];y++)for(let x=0;x<shape[0];x++) {
                const n=mask[offset(x,y,z)];if(!target.has(n))continue;
                const r=stats[n],v=[x,y,z],p=point(affine,v),s=v[axis];r.count++;r.areas[s]++;
                if(!r.lower){r.lower=p.slice();r.upper=p.slice();r.voxel_lower=v.slice();r.voxel_upper=v.slice();}
                for(let a=0;a<3;a++){r.lower[a]=Math.min(r.lower[a],p[a]);r.upper[a]=Math.max(r.upper[a],p[a]);r.voxel_lower[a]=Math.min(r.voxel_lower[a],v[a]);r.voxel_upper[a]=Math.max(r.voxel_upper[a],v[a]);}
                if(!r.section_points[s])r.section_points[s]=v;
                if(x===0||y===0||z===0||x===shape[0]-1||y===shape[1]-1||z===shape[2]-1)r.boundary=true;
            }
            if(z%12===0){await yieldTask();if(captured!==revision&&version!=='original')throw Error('STALE');}
        }
        for(const n of needed) {
            const r=stats[n];
            if(r.count){r.volume_ml=r.count*constants.voxel/1000;r.lower=r.lower.map((v,i)=>v-constants.half[i]);r.upper=r.upper.map((v,i)=>v+constants.half[i]);r.dimensions_mm=subtract(r.upper,r.lower);r.areas=r.areas.map(v=>v*constants.area);r.max_area_mm2=Math.max(...r.areas);r.max_slice=r.areas.indexOf(r.max_area_mm2);r.status=r.boundary?'partial':'measured';}
            measureCache.set(version+':'+n,r);result[n]=r;
        }
    }
    return {rows:result,revision,version};
}
function invalidate(ids) {revision++;meshToken++;viewToken++;for(const n of ids){measureCache.delete('edited:'+n);reviews[n]='needs_correction';}}
function changeSet(changes, saveHistory=true) {
    const changed=changes.filter(([i,b,a])=>b!==a);if(!changed.length)return {revision,affected:[]};
    const affected=new Set();for(const [i,b,a] of changed){edited[i]=a;if(b)affected.add(b);if(a)affected.add(a);}
    invalidate(affected);
    if(saveHistory){future=[];if(changed.length*CHANGE_BYTES>HISTORY_LIMIT){history=[];historyBytes=0;}else{history.push(changed);historyBytes+=changed.length*CHANGE_BYTES;while(historyBytes>HISTORY_LIMIT)historyBytes-=history.shift().length*CHANGE_BYTES;}}
    return {revision,affected:[...affected],reviews:{...reviews},undo:history.length,redo:future.length,history_limited:historyBytes>HISTORY_LIMIT||history.length===0};
}
function brush({label,z,paths,diameter,erase}) {
    requireGeometry();if(!config.labels[label]||!Number.isInteger(z)||z<0||z>=shape[2]||!Number.isFinite(diameter)||diameter<=0||diameter>200)throw Error('INVALID_BRUSH');
    if(!Array.isArray(paths)||paths.length>20000||paths.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v))))throw Error('INVALID_PATH');
    const c0=affine.slice(0,3).map(r=>r[0]),c1=affine.slice(0,3).map(r=>r[1]);
    const g00=c0.reduce((s,v)=>s+v*v,0),g11=c1.reduce((s,v)=>s+v*v,0),g01=c0.reduce((s,v,i)=>s+v*c1[i],0);
    const determinant=g00*g11-g01*g01, radius=diameter/2;
    const rx=radius*Math.sqrt(g11/determinant),ry=radius*Math.sqrt(g00/determinant),changes=new Map();
    function dab(p) {
        for(let y=Math.max(0,Math.ceil(p[1]-ry));y<=Math.min(shape[1]-1,Math.floor(p[1]+ry));y++)for(let x=Math.max(0,Math.ceil(p[0]-rx));x<=Math.min(shape[0]-1,Math.floor(p[0]+rx));x++) {
            const dx=x-p[0],dy=y-p[1];if(dx*dx*g00+2*dx*dy*g01+dy*dy*g11>radius*radius+1e-8)continue;
            const i=offset(x,y,z),old=edited[i],next=erase?(old===label?0:old):(old===0||old===label?label:old);
            if(next!==old)changes.set(i,[i,old,next]);
        }
    }
    for(let k=0;k<paths.length;k++) {
        const p=paths[k];if(!k){dab(p);continue;}
        const prev=paths[k-1],dx=p[0]-prev[0],dy=p[1]-prev[1];
        const length=Math.sqrt(g00*dx*dx+2*g01*dx*dy+g11*dy*dy),steps=Math.max(1,Math.ceil(length/Math.max(.1,radius/3)));
        for(let j=1;j<=steps;j++)dab([prev[0]+dx*j/steps,prev[1]+dy*j/steps]);
    }
    return changeSet([...changes.values()]);
}
function historyMove(redo) {
    const from=redo?future:history,to=redo?history:future,entry=from.pop();if(!entry)return {revision,affected:[]};
    if(redo)historyBytes+=entry.length*CHANGE_BYTES;else historyBytes-=entry.length*CHANGE_BYTES;
    const result=changeSet(entry.map(([i,b,a])=>[i,redo?b:a,redo?a:b]),false);to.push(entry);
    return {...result,undo:history.length,redo:future.length};
}
async function exportPackage() {
    const changes=[];for(let i=0;i<edited.length;i++)if(edited[i]!==original[i])changes.push([i,original[i],edited[i]]);
    return {schema_version:'1.0',package_type:'ct_viewer_edits',source_identity:config.source_identity,
        original_fingerprint:config.original_fingerprint,shape,geometry:config.geometry,labels:config.labels,
        encoding:'sparse_voxel_changes',encoding_description:'[flat_index, original_label, edited_label]; zero-based x + nx*(y + ny*z); background=0; display voxel grid; geometry.voxel_transform maps to original acquisition indices',
        changes,created_at:new Date().toISOString(),authenticated_user:config.authenticated_user||null,
        user_identity_context:config.authenticated_user?'authenticated result creator captured at generation; offline reviewer identity is unavailable':null,
        review_states:{...reviews},relationship:'separate edited version of immutable original model result',saved_to_server:false};
}
function importPackage(p) {
    requireGeometry();
    if(!p||p.schema_version!=='1.0'||p.package_type!=='ct_viewer_edits'||p.encoding!=='sparse_voxel_changes')throw Error('INVALID_EDIT_SCHEMA');
    const canonical=v=>v&&typeof v==='object'?(Array.isArray(v)?v.map(canonical):Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])]))):v;
    const same=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
    if(!same(p.source_identity,config.source_identity)||p.original_fingerprint!==config.original_fingerprint)throw Error('SOURCE_IDENTITY_MISMATCH');
    if(!same(p.shape,shape)||!same(p.geometry,config.geometry)||!same(p.labels,config.labels))throw Error('GEOMETRY_OR_LABELS_MISMATCH');
    if(!Array.isArray(p.changes)||p.changes.length>original.length)throw Error('INVALID_CHANGES');
    const replacement=new Uint8Array(original),seen=new Set();
    for(const row of p.changes) {
        if(!Array.isArray(row)||row.length!==3)throw Error('INVALID_VOXEL_CHANGE');
        const [i,b,a]=row;
        if(!Number.isInteger(i)||i<0||i>=original.length||seen.has(i)||!Number.isInteger(b)||!Number.isInteger(a)||b!==original[i]||(a!==0&&!Object.hasOwn(config.labels,a))||b===a)throw Error('INVALID_VOXEL_CHANGE');
        seen.add(i);replacement[i]=a;
    }
    // Validate the entire package before any edit to the active version.
    const changes=[];for(let i=0;i<edited.length;i++)if(edited[i]!==replacement[i])changes.push([i,edited[i],replacement[i]]);
    const result=changeSet(changes);for(const [i,b,a] of p.changes){if(b)reviews[b]='needs_correction';if(a)reviews[a]='needs_correction';}
    // Imported approval is evidence only. Local review must be performed again.
    return {...result,reviews:{...reviews},imported_review_states:p.review_states||{},requires_local_review:true};
}
function resetLabel(n) {
    const changes=[];for(let i=0;i<edited.length;i++)if(edited[i]!==original[i]&&(edited[i]===n||original[i]===n)) {
        // Restoring one label never overwrites another label added by the user.
        if(edited[i]&&edited[i]!==n&&original[i]===n)continue;
        changes.push([i,edited[i],original[i]]);
    }
    return changeSet(changes);
}
function worldBounds() {
    const lower=[Infinity,Infinity,Infinity],upper=[-Infinity,-Infinity,-Infinity];
    for(let k=0;k<8;k++){const p=point(affine,shape.map((n,a)=>(k&(1<<a))?n-.5:-.5));for(let a=0;a<3;a++){lower[a]=Math.min(lower[a],p[a]);upper[a]=Math.max(upper[a],p[a]);}}
    return {lower,upper};
}
function planeSpec(kind,world) {
    const {lower,upper}=worldBounds(),axes=kind==='axial'?[0,1,2]:kind==='coronal'?[0,2,1]:[1,2,0];
    const step=Math.min(...config.geometry.spacing_mm),[u,v,f]=axes;
    const width=Math.min(768,Math.max(1,Math.ceil((upper[u]-lower[u])/step))),height=Math.min(768,Math.max(1,Math.ceil((upper[v]-lower[v])/step)));
    return {kind,axes,width,height,lower,upper,fixed:world[f],step_u:(upper[u]-lower[u])/width,step_v:(upper[v]-lower[v])/height};
}
async function views({world,version,token,specs}) {
    requireGeometry();const mine=++viewToken,captured=revision,mask=activeMask(version),planes=[];
    for(const kind of ['axial','coronal','sagittal']) {
        const spec=specs?.find(s=>s.kind===kind)||planeSpec(kind,world),pixels=new Uint16Array(spec.width*spec.height),labels=new Uint8Array(pixels.length),valid=new Uint8Array(pixels.length);
        const [u,v,f]=spec.axes;
        for(let y=0;y<spec.height;y++) {
            for(let x=0;x<spec.width;x++) {
                const w=world.slice();w[u]=spec.upper[u]-(x+.5)*spec.step_u;w[v]=spec.upper[v]-(y+.5)*spec.step_v;w[f]=spec.fixed;
                const p=point(inverse,w).map(Math.round),i=x+spec.width*y;
                if(p.some((c,a)=>c<0||c>=shape[a]))continue;
                const j=offset(...p);pixels[i]=ctValues?.[j]||0;labels[i]=mask[j];valid[i]=1;
            }
            if(y%40===0){await yieldTask();if(mine!==viewToken||captured!==revision)throw Error('STALE');}
        }
        planes.push({spec,pixels,labels,valid});
    }
    return {planes,world,token,revision,version};
}
// Outward faces, wound consistently; unsmoothed voxel boundary is intentional.
const faces=[
    {d:[-1,0,0],c:[[0,0,0],[0,0,1],[0,1,1],[0,1,0]]},
    {d:[1,0,0],c:[[1,0,0],[1,1,0],[1,1,1],[1,0,1]]},
    {d:[0,-1,0],c:[[0,0,0],[1,0,0],[1,0,1],[0,0,1]]},
    {d:[0,1,0],c:[[0,1,0],[0,1,1],[1,1,1],[1,1,0]]},
    {d:[0,0,-1],c:[[0,0,0],[0,1,0],[1,1,0],[1,0,0]]},
    {d:[0,0,1],c:[[0,0,1],[1,0,1],[1,1,1],[0,1,1]]}
];
async function meshes({ids,version},requestId) {
    requireGeometry();const mine=++meshToken,captured=revision,mask=activeMask(version),bounds=worldBounds();
    const center=bounds.lower.map((v,i)=>(v+bounds.upper[i])/2),output=[];
    // Bound allocations. Cancellation frees partial arrays on return.
    const MAX_VERTICES=2400000;let total=0;
    for(const id of ids) {
        let count=0;
        const exposed=(x,y,z,face)=>{const p=[x+face.d[0],y+face.d[1],z+face.d[2]];return p.some((v,a)=>v<0||v>=shape[a])||mask[offset(...p)]!==id;};
        for(let z=0;z<shape[2];z++) {
            for(let y=0;y<shape[1];y++)for(let x=0;x<shape[0];x++)if(mask[offset(x,y,z)]===id)for(const f of faces)if(exposed(x,y,z,f))count+=6;
            if(z%8===0){await yieldTask();if(mine!==meshToken||captured!==revision)throw Error('STALE');}
        }
        total+=count;if(total>MAX_VERTICES)throw Error('SURFACE_MEMORY_LIMIT: ناحیه‌های کمتری برای سه‌بعدی انتخاب کنید.');
        const vertices=new Float32Array(count*6);let out=0;
        for(let z=0;z<shape[2];z++) {
            for(let y=0;y<shape[1];y++)for(let x=0;x<shape[0];x++)if(mask[offset(x,y,z)]===id)for(const f of faces)if(exposed(x,y,z,f)) {
                const corners=f.c.map(c=>subtract(point(affine,[x+c[0]-.5,y+c[1]-.5,z+c[2]-.5]),center));
                const normal=cross(subtract(corners[1],corners[0]),subtract(corners[2],corners[0])),length=norm(normal);
                for(const j of [0,1,2,0,2,3]){vertices.set(corners[j],out);vertices.set(normal.map(v=>v/length),out+3);out+=6;}
            }
            if(z%6===0){postMessage({progress:true,id:requestId,value:(output.length+z/shape[2])/Math.max(1,ids.length)});await yieldTask();if(mine!==meshToken||captured!==revision)throw Error('STALE');}
        }
        output.push({id,vertices,count});
    }
    return {meshes:output,center,bounds,revision,version};
}
function pick({origin,direction,ids,version}) {
    requireGeometry();const mask=activeMask(version),allowed=new Set(ids),o=point(inverse,origin),b=point(inverse,origin.map((v,i)=>v+direction[i])),d=subtract(b,o);
    let entry=0,exit=Infinity;
    for(let a=0;a<3;a++) {
        if(Math.abs(d[a])<1e-12){if(o[a]<-.5||o[a]>shape[a]-.5)return null;continue;}
        let t1=(-.5-o[a])/d[a],t2=(shape[a]-.5-o[a])/d[a];if(t1>t2)[t1,t2]=[t2,t1];entry=Math.max(entry,t1);exit=Math.min(exit,t2);
    }
    if(exit<entry)return null;
    let t=entry+1e-7;const p=o.map((v,a)=>Math.floor(v+d[a]*t+.5));
    const step=d.map(v=>v>0?1:-1),delta=d.map(v=>Math.abs(v)<1e-12?Infinity:Math.abs(1/v));
    const next=p.map((v,a)=>Math.abs(d[a])<1e-12?Infinity:(v+(step[a]>0?.5:-.5)-o[a])/d[a]);
    for(let k=0;k<shape.reduce((s,v)=>s+v,0)+6&&t<=exit;k++) {
        if(p.some((v,a)=>v<0||v>=shape[a]))break;
        const label=mask[offset(...p)];if(allowed.has(label))return {label,world:origin.map((v,a)=>v+direction[a]*Math.max(entry,t-1e-7)),voxel:p.slice()};
        const a=next.indexOf(Math.min(...next));t=next[a]+1e-7;p[a]+=step[a];next[a]+=delta[a];
    }
    return null;
}
async function dispatch(type,args,id) {
    switch(type) {
        case 'init': {
            config=args;shape=config.shape;affine=config.geometry.affine_ras_mm;
            if(config.geometry.valid)inverse=invertAffine(affine);
            const n=shape.reduce((a,b)=>a*b,1);original=new Uint8Array(n);edited=new Uint8Array(n);ctValues=config.include_ct===false?null:new Uint16Array(n);
            return {ready:true};
        }
        case 'loadSlice': {const start=args.z*shape[0]*shape[1];original.set(args.labels,start);edited.set(args.labels,start);if(ctValues)ctValues.set(args.pixels,start);return true;}
        case 'measure': return measurements(args.version,args.ids);
        case 'slice': {const start=args.z*shape[0]*shape[1];return {pixels:activeMask(args.version).slice(start,start+shape[0]*shape[1]),revision,version:args.version};}
        case 'brush': return brush(args);
        case 'undo': return historyMove(false);
        case 'redo': return historyMove(true);
        case 'resetLabel': return resetLabel(args.label);
        case 'review': if(!['approved','rejected','needs_correction'].includes(args.state)||!config.labels[args.label])throw Error('INVALID_REVIEW');reviews[args.label]=args.state;return {...reviews};
        case 'export': return exportPackage();
        case 'import': return importPackage(args.package);
        case 'views': return views(args);
        case 'meshes': return meshes(args,id);
        case 'pick': return pick(args);
        default: throw Error('UNKNOWN_OPERATION');
    }
}
// Serialize mutations/measurements. MPR and mesh jobs can be superseded/cancelled.
let queue=Promise.resolve();
self.onmessage = event => {
    const {id,type,args}=event.data;
    if(type==='cancelMeshes'){meshToken++;return;}
    if(type==='cancelViews'){viewToken++;return;}
    const run=async()=>{try {const result=await dispatch(type,args,id),transfer=[];
        if(result?.meshes)for(const m of result.meshes)transfer.push(m.vertices.buffer);
        if(result?.planes)for(const p of result.planes)transfer.push(p.pixels.buffer,p.labels.buffer,p.valid.buffer);
        if(result?.pixels?.buffer)transfer.push(result.pixels.buffer);
        postMessage({id,result},transfer);
    } catch(error){postMessage({id,error:String(error.message||error)});}};
    if(['meshes','views','pick'].includes(type))queue.then(run);else queue=queue.then(run);
};

});
