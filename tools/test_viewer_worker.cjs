/* Execute the exact shipped worker, with real arrays and background messaging.
 * node --test tools/test_viewer_worker.cjs
 */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Worker}=require('node:worker_threads');
const fs=require('node:fs');
const source=fs.readFileSync('app/export/viewer_worker.js','utf8');
const labels={'1':'kidney_left','2':'kidney_right','3':'liver','4':'empty'};
const shape=[12,11,10],a=[[2,0,0,31],[0,3,0,-42],[0,0,4,19],[0,0,0,1]];
const flat=(x,y,z)=>x+shape[0]*(y+shape[1]*z);
const mask=new Uint8Array(shape.reduce((p,n)=>p*n));
for(let z=2;z<7;z++)for(let y=3;y<7;y++)for(let x=2;x<5;x++)mask[flat(x,y,z)]=1;
for(let z=2;z<7;z++)for(let y=3;y<7;y++)for(let x=8;x<10;x++)mask[flat(x,y,z)]=2;
mask[0]=3;
function approx(value,expected,tol=1e-8){assert.ok(Math.abs(value-expected)<tol,`${value} != ${expected}`);}
async function fixture(t,{affine=a,section_axis=2,input=mask,valid=true}={}){
    const wrapper=`const {parentPort,workerData}=require('node:worker_threads');globalThis.self=globalThis;globalThis.postMessage=(m,t)=>parentPort.postMessage(m,t);eval(workerData);parentPort.on('message',m=>self.onmessage({data:m}));`;
    const worker=new Worker(wrapper,{eval:true,workerData:source});t.after(()=>worker.terminate());let id=0;const pending=new Map();
    worker.on('message',m=>{const p=pending.get(m.id);if(!p||m.progress)return;pending.delete(m.id);m.error?p.reject(Error(m.error)):p.resolve(m.result);});
    worker.on('error',error=>{for(const p of pending.values())p.reject(error);pending.clear();});
    const rpc=(type,args={})=>new Promise((resolve,reject)=>{pending.set(++id,{resolve,reject});worker.postMessage({id,type,args});});
    const config={shape,labels,geometry:{valid,affine_ras_mm:valid?affine:null,section_axis,spacing_mm:[2,3,4],unavailable_reason:valid?null:'MISSING_GEOMETRY'},source_identity:{result_id:'fixture',study_instance_uid:'test-study',series_instance_uid:'test-series'},authenticated_user:null,original_fingerprint:'sha256:original-fixture'};
    await rpc('init',config);
    for(let z=0;z<shape[2];z++){const pixels=new Uint16Array(shape[0]*shape[1]);for(let y=0;y<shape[1];y++)for(let x=0;x<shape[0];x++)pixels[x+shape[0]*y]=flat(x,y,z);await rpc('loadSlice',{z,pixels,labels:input.slice(z*shape[0]*shape[1],(z+1)*shape[0]*shape[1])});}
    return {rpc,worker,config};
}

test('physical voxel volume, boundary extents, section areas, empty and partial states',async t=>{
    const {rpc}=await fixture(t),r=(await rpc('measure',{version:'original',ids:[1,2,3,4]})).rows;
    assert.equal(r[1].count,60);approx(r[1].volume_ml,1.44);assert.deepEqual(r[1].dimensions_mm,[6,12,20]);approx(r[1].max_area_mm2,72);assert.equal(r[1].max_slice,2);assert.deepEqual(r[1].areas,[0,0,72,72,72,72,72,0,0,0]);assert.equal(r[1].boundary,false);
    assert.equal(r[3].boundary,true);assert.equal(r[3].status,'partial');assert.equal(r[4].status,'empty');assert.equal(r[4].volume_ml,null);assert.equal(r[4].dimensions_mm,null);
});
test('oblique and sheared affine uses patient-axis voxel boundaries and native section area',async t=>{
    const theta=.4,c=Math.cos(theta),s=Math.sin(theta),affine=[[2,0,0,5],[0,3*c,-4*s,7],[0,3*s,4*c,11],[0,0,0,1]],{rpc}=await fixture(t,{affine}),row=(await rpc('measure',{version:'edited',ids:[1]})).rows[1];
    approx(row.volume_ml,1.44);approx(row.max_area_mm2,72);approx(row.dimensions_mm[0],6);approx(row.dimensions_mm[1],12*c+20*s);approx(row.dimensions_mm[2],12*s+20*c);
    const shear=structuredClone(a);shear[0][1]=1.25;const f=await fixture(t,{affine:shear}),r=(await f.rpc('measure',{version:'edited',ids:[1]})).rows[1];approx(r.volume_ml,1.44);approx(r.dimensions_mm[0],6+4*1.25);approx(r.max_area_mm2,72);
});
test('acquisition section axis survives a display permutation',async t=>{
    const {rpc}=await fixture(t,{section_axis:0}),r=(await rpc('measure',{version:'edited',ids:[1]})).rows[1];assert.equal(r.areas.length,shape[0]);assert.equal(r.max_slice,2);approx(r.max_area_mm2,240);assert.equal(r.section_points[2][0],2);
});
test('anisotropic mm brush, protected overlaps, immutable original, undo/redo and approval invalidation',async t=>{
    const {rpc}=await fixture(t);await rpc('review',{label:1,state:'approved'});
    const change=await rpc('brush',{label:1,z:1,diameter:8,paths:[[3,4]],erase:false});assert.equal(change.reviews[1],'needs_correction');
    const pack=await rpc('export');let expected=0;for(let y=0;y<shape[1];y++)for(let x=0;x<shape[0];x++)if((x-3)**2*4+(y-4)**2*9<=16)expected++;
    assert.equal(pack.changes.length,expected);assert.equal(pack.authenticated_user,null);assert.equal(pack.saved_to_server,false);
    assert.equal((await rpc('measure',{version:'original',ids:[1]})).rows[1].count,60);
    assert.equal((await rpc('measure',{version:'edited',ids:[1]})).rows[1].count,60+expected);
    await rpc('review',{label:1,state:'approved'});const undo=await rpc('undo');assert.equal(undo.reviews[1],'needs_correction');assert.equal((await rpc('export')).changes.length,0);
    await rpc('redo');assert.equal((await rpc('export')).changes.length,expected);
    await rpc('brush',{label:1,z:3,diameter:20,paths:[[8,4]],erase:false});assert.equal((await rpc('measure',{version:'edited',ids:[2]})).rows[2].count,40);
    await rpc('brush',{label:1,z:3,diameter:20,paths:[[8,4]],erase:true});assert.equal((await rpc('measure',{version:'edited',ids:[2]})).rows[2].count,40);
    await rpc('resetLabel',{label:1});assert.equal((await rpc('export')).changes.length,0);
});
test('edit package validates identity, geometry and every voxel atomically; import is separate and reviewed again',async t=>{
    const {rpc}=await fixture(t);await rpc('brush',{label:1,z:1,diameter:4,paths:[[3,4]],erase:false});await rpc('review',{label:1,state:'approved'});const pack=await rpc('export');
    await rpc('undo');assert.equal((await rpc('export')).changes.length,0);
    for(const modify of [p=>p.source_identity.result_id='foreign',p=>p.original_fingerprint='other',p=>p.geometry.affine_ras_mm[0][3]+=1,p=>p.labels[1]='foreign',p=>p.changes.push([999999,0,1]),p=>p.changes.push(p.changes[0]),p=>p.changes[0][1]=99,p=>p.changes[0][2]=99]){
        const invalid=structuredClone(pack);modify(invalid);await assert.rejects(rpc('import',{package:invalid}));assert.equal((await rpc('export')).changes.length,0);
    }
    const result=await rpc('import',{package:pack});assert.equal(result.requires_local_review,true);assert.equal(result.reviews[1],'needs_correction');assert.deepEqual((await rpc('export')).changes,pack.changes);
    assert.equal((await rpc('measure',{version:'original',ids:[1]})).rows[1].count,60);
});
test('three reconstructed views sample identical native CT and labels through oblique affine',async t=>{
    const theta=.41,c=Math.cos(theta),s=Math.sin(theta),affine=[[2,0,0,31],[0,3*c,-4*s,-42],[0,3*s,4*c,19],[0,0,0,1]],{rpc}=await fixture(t,{affine});
    const world=[31+3*2,-42+4*3*c-4*4*s,19+4*3*s+4*4*c],result=await rpc('views',{world,version:'edited',token:1});assert.equal(result.planes.length,3);
    let samples=0;
    for(const {spec,pixels,labels,valid} of result.planes){assert.equal(spec.fixed,world[spec.axes[2]]);for(let y=0;y<spec.height;y++)for(let x=0;x<spec.width;x++){
        const w=world.slice();w[spec.axes[0]]=spec.upper[spec.axes[0]]-(x+.5)*spec.step_u;w[spec.axes[1]]=spec.upper[spec.axes[1]]-(y+.5)*spec.step_v;
        const dx=w[0]-31,dy=w[1]+42,dz=w[2]-19,p=[Math.round(dx/2),Math.round((dy*c+dz*s)/3),Math.round((-dy*s+dz*c)/4)],i=x+spec.width*y,inside=p.every((v,a)=>v>=0&&v<shape[a]);assert.equal(Boolean(valid[i]),inside);if(inside){const j=flat(...p);assert.equal(labels[i],mask[j]);assert.equal(pixels[i],j);samples++;}
    }}assert.ok(samples>100);
});
test('3D extraction is lazy, physical, updates after edits and ray picking returns correct label',async t=>{
    const {rpc}=await fixture(t),result=await rpc('meshes',{ids:[1],version:'edited'}),m=result.meshes[0];assert.ok(m.count>0);assert.equal(m.vertices.length,m.count*6);
    const lower=[Infinity,Infinity,Infinity],upper=[-Infinity,-Infinity,-Infinity];for(let i=0;i<m.vertices.length;i+=6)for(let axis=0;axis<3;axis++){const v=m.vertices[i+axis]+result.center[axis];lower[axis]=Math.min(lower[axis],v);upper[axis]=Math.max(upper[axis],v);}
    assert.deepEqual(upper.map((v,i)=>v-lower[i]),[6,12,20]);
    const picked=await rpc('pick',{origin:[37,-30,100],direction:[0,0,-1],ids:[1,2],version:'edited'});assert.equal(picked.label,1);approx(picked.world[2],45,1e-5);
    await rpc('brush',{label:1,z:7,diameter:4,paths:[[3,4]],erase:false});const after=await rpc('meshes',{ids:[1],version:'edited'});assert.notEqual(after.meshes[0].count,m.count);
    const hidden=await rpc('pick',{origin:[37,-30,100],direction:[0,0,-1],ids:[2],version:'edited'});assert.equal(hidden,null);
});
test('missing geometry disables physical features explicitly, while slices still work',async t=>{
    const {rpc}=await fixture(t,{valid:false});assert.equal((await rpc('slice',{z:2,version:'original'})).pixels.length,shape[0]*shape[1]);
    await assert.rejects(rpc('measure',{version:'edited',ids:[1]}),/MISSING_GEOMETRY/);await assert.rejects(rpc('meshes',{ids:[1],version:'edited'}),/MISSING_GEOMETRY/);await assert.rejects(rpc('brush',{label:1,z:1,diameter:8,paths:[[3,4]]}),/MISSING_GEOMETRY/);
});
