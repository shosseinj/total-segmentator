/* Small WebGL surface renderer for the existing dependency-free offline viewer.
 * Original meshes are built on the server. Picking reads the rendered GPU depth.
 */
class OfflineSurface {
    constructor(api) {
        Object.assign(this,api);this.entries=new Map();this.gl=null;this.sequence=0;this.yaw=.55;this.pitch=-.35;this.zoom=1;this.radius=1;this.center=[0,0,0];this.meshCenter=[0,0,0];this.pointer=null;
        this.canvas=this.el('surfaceCanvas');
        this.el('buildSurface').onclick=()=>this.setSelection(this.getSelected());
        this.el('resetCamera').onclick=()=>{this.yaw=.55;this.pitch=-.35;this.zoom=1;this.fitCamera();this.draw();};
        this.el('cancelSurface').onclick=()=>this.release();
        this.el('surfacePanel').addEventListener('toggle',()=>{if(!this.el('surfacePanel').open)this.release();});
        this.canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();this.release();this.gl=null;this.el('surfaceStatus').textContent='WebGL متوقف شد؛ نمای دوبعدی قابل استفاده است.';});
        this.canvas.addEventListener('pointerdown',e=>{if(e.button!==0)return;this.pointer={x:e.clientX,y:e.clientY,lastX:e.clientX,lastY:e.clientY,moved:false};this.canvas.setPointerCapture(e.pointerId);});
        this.canvas.addEventListener('pointermove',e=>{if(!this.pointer)return;const p=this.pointer;this.yaw+=(e.clientX-p.lastX)*.008;this.pitch=Math.max(-1.5,Math.min(1.5,this.pitch+(e.clientY-p.lastY)*.008));p.lastX=e.clientX;p.lastY=e.clientY;p.moved ||= Math.hypot(e.clientX-p.x,e.clientY-p.y)>4;this.draw();});
        this.canvas.addEventListener('pointerup',e=>{const p=this.pointer;this.pointer=null;if(p&&!p.moved)this.pickAt(e.clientX,e.clientY);});
        this.canvas.addEventListener('pointercancel',()=>this.pointer=null);
        this.canvas.addEventListener('wheel',e=>{e.preventDefault();this.zoom=Math.max(.2,Math.min(8,this.zoom*(e.deltaY>0?.9:1.1)));this.draw();},{passive:false});
        window.addEventListener('resize',()=>{this.fitCamera();this.draw();});
    }
    initialize() {
        if(this.gl)return true;
        const gl=this.canvas.getContext('webgl',{alpha:false,antialias:true,preserveDrawingBuffer:true});
        if(!gl){this.el('surfaceStatus').textContent='WebGL در این مرورگر موجود نیست؛ نمای اصلی و اندازه‌گیری‌ها قابل استفاده‌اند.';return false;}
        const shader=(type,source)=>{const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS)){const reason=gl.getShaderInfoLog(s);gl.deleteShader(s);throw Error(reason);}return s;};
        const vs=shader(gl.VERTEX_SHADER,'attribute vec3 position; attribute vec3 normal; uniform mat3 projection; uniform mat3 rotation; uniform vec3 offset; varying float light; void main(){gl_Position=vec4(projection*(position+offset),1.0);vec3 n=normalize(rotation*normal);light=0.4+0.6*abs(dot(n,normalize(vec3(0.3,0.5,1.0))));}');
        const fs=shader(gl.FRAGMENT_SHADER,'precision highp float; uniform vec4 color; uniform float pickId; varying float light; void main(){if(pickId>0.0){float q=floor(gl_FragCoord.z*16777215.0+0.5);gl_FragColor=vec4(pickId,floor(q/65536.0),mod(floor(q/256.0),256.0),mod(q,256.0))/255.0;}else{gl_FragColor=vec4(color.rgb*light,color.a);}}');
        const program=gl.createProgram();gl.attachShader(program,vs);gl.attachShader(program,fs);gl.linkProgram(program);gl.deleteShader(vs);gl.deleteShader(fs);
        if(!gl.getProgramParameter(program,gl.LINK_STATUS)){gl.deleteProgram(program);throw Error('WEBGL_LINK_FAILED');}
        this.gl=gl;this.program=program;this.locations={position:gl.getAttribLocation(program,'position'),normal:gl.getAttribLocation(program,'normal'),projection:gl.getUniformLocation(program,'projection'),rotation:gl.getUniformLocation(program,'rotation'),color:gl.getUniformLocation(program,'color'),offset:gl.getUniformLocation(program,'offset'),pickId:gl.getUniformLocation(program,'pickId')};
        this.pickBuffer=gl.createFramebuffer();this.pickTexture=gl.createTexture();this.pickDepth=gl.createRenderbuffer();
        gl.bindTexture(gl.TEXTURE_2D,this.pickTexture);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,1,1,0,gl.RGBA,gl.UNSIGNED_BYTE,null);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
        gl.bindRenderbuffer(gl.RENDERBUFFER,this.pickDepth);gl.renderbufferStorage(gl.RENDERBUFFER,gl.DEPTH_COMPONENT16,1,1);gl.bindFramebuffer(gl.FRAMEBUFFER,this.pickBuffer);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,this.pickTexture,0);gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.RENDERBUFFER,this.pickDepth);
        this.pickingAvailable=gl.checkFramebufferStatus(gl.FRAMEBUFFER)===gl.FRAMEBUFFER_COMPLETE;gl.bindFramebuffer(gl.FRAMEBUFFER,null);
        return true;
    }
    async add(id){return this.setSelection([...this.entries.keys(),id]);}
    async setSelection(ids){
        this.beforeShow();this.el('surfacePanel').open=true;
        const available=[...new Set(ids)].filter(id=>this.ui.measurements[this.ui.version][id]?.count);
        if(!available.length){this.invalidate();this.entries.clear();this.controls();this.el('surfaceStatus').textContent='از فهرست ناحیه‌ها، یک یا چند ناحیهٔ دارای ماسک را انتخاب کنید.';return;}
        if(!this.initialize())return;
        const wanted=new Set(available),missing=[];
        for(const [id,e] of this.entries)if(!wanted.has(id)){if(e.buffer)this.gl.deleteBuffer(e.buffer);this.entries.delete(id);}
        for(const id of available)if(!this.entries.has(id)){this.entries.set(id,{id,visible:true,opacity:.85,buffer:null,count:0});missing.push(id);}else if(!this.entries.get(id).buffer&&this.entries.get(id).visible)missing.push(id);
        this.controls();this.fitCamera();this.draw();
        if(missing.length)await this.refresh(missing);else{++this.sequence;this.notify('cancelMeshes');}
    }
    fitCamera(){
        const rows=[...this.entries.values()].filter(e=>e.visible).map(e=>this.ui.measurements[this.ui.version][e.id]).filter(r=>r?.count);if(!rows.length)return;
        const lower=[0,1,2].map(a=>Math.min(...rows.map(r=>r.lower[a]))),upper=[0,1,2].map(a=>Math.max(...rows.map(r=>r.upper[a])));
        this.center=lower.map((v,a)=>(v+upper[a])/2);const bounds=this.canvas.getBoundingClientRect(),aspect=bounds.height?bounds.width/bounds.height:1;this.radius=Math.max(1,Math.hypot(...upper.map((v,a)=>v-lower[a]))*.58*Math.max(1,1/Math.max(.1,aspect)));
    }
    controls(){
        const list=this.el('surfaceControls');list.replaceChildren();
        for(const entry of this.entries.values()){
            const label=document.createElement('label'),check=document.createElement('input'),opacity=document.createElement('input');check.type='checkbox';check.checked=entry.visible;check.setAttribute('aria-label','نمایش سه‌بعدی '+this.data.labels_fa[entry.id]);
            check.onchange=()=>{entry.visible=check.checked;if(!entry.visible){if(entry.buffer)this.gl.deleteBuffer(entry.buffer);entry.buffer=null;this.sequence++;this.notify('cancelMeshes');this.fitCamera();this.draw();}else{this.fitCamera();this.refresh([entry.id]);}};
            opacity.type='range';opacity.min=0;opacity.max=100;opacity.value=entry.opacity*100;opacity.setAttribute('aria-label','شفافیت سه‌بعدی '+this.data.labels_fa[entry.id]);opacity.oninput=()=>{entry.opacity=Number(opacity.value)/100;this.draw();};
            const text=document.createElement('span');text.textContent=this.data.labels_fa[entry.id];text.style.color='rgb('+this.data.colors[entry.id].join(',')+')';label.append(check,text,opacity);list.append(label);
        }
    }
    invalidate(affected){
        ++this.sequence;this.notify('cancelMeshes');
        if(this.gl)for(const e of this.entries.values())if(!affected||affected.includes(e.id)){if(e.buffer)this.gl.deleteBuffer(e.buffer);e.buffer=null;e.count=0;}
        this.draw();
    }
    async refresh(affected) {
        if(!this.gl||!this.el('surfacePanel').open||!this.entries.size)return;
        const ids=[...this.entries.values()].filter(e=>e.visible&&(!affected||affected.includes(e.id)||!e.buffer)).map(e=>e.id);if(!ids.length)return;
        const sequence=++this.sequence,revision=this.ui.revision,version=this.ui.version;this.notify('cancelMeshes');
        // Remove obsolete buffers immediately; stale surfaces must not imply current edits.
        for(const id of ids){const e=this.entries.get(id);if(e.buffer)this.gl.deleteBuffer(e.buffer);e.buffer=null;e.count=0;}
        this.draw();this.el('surfaceStatus').textContent=this.ui.processing==='server-precomputed'&&(!this.ui.revision||this.ui.version==='original')?'بازکردن سطح‌های آمادهٔ سرور…':'به‌روزرسانی سطح نسخهٔ ویرایشی روی همین دستگاه…';this.el('surfaceProgress').value=0;
        try{
            const result=await this.rpc('meshes',{ids,version},value=>{if(sequence===this.sequence)this.el('surfaceProgress').value=value;});
            if(sequence!==this.sequence||revision!==this.ui.revision||version!==this.ui.version)return;
            const gl=this.gl;this.meshCenter=result.center;this.fitCamera();
            for(const mesh of result.meshes){const e=this.entries.get(mesh.id);if(!e?.visible)continue;e.buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,e.buffer);gl.bufferData(gl.ARRAY_BUFFER,mesh.vertices,gl.STATIC_DRAW);e.count=mesh.count;}
            this.el('surfaceProgress').value=1;this.el('surfaceStatus').textContent='سطح آماده است؛ کلیک برای انتخاب ناحیه و نقطهٔ فیزیکی. تعداد مثلث: '+result.meshes.reduce((s,m)=>s+m.count/3,0);this.draw();
        }catch(error){if(sequence===this.sequence)this.showError(error,'surfaceStatus');}
    }
    axes(){
        const c=Math.cos(this.yaw),s=Math.sin(this.yaw),cp=Math.cos(this.pitch),sp=Math.sin(this.pitch);
        return {right:[c,0,-s],up:[s*sp,cp,c*sp],forward:[s*cp,-sp,c*cp]};
    }
    draw(pick=null){
        if(!this.gl||!this.el('surfacePanel').open)return;
        const gl=this.gl,mount=this.el('surfaceMount');if(!mount.clientWidth||!mount.clientHeight)return;this.canvas.style.flex='none';this.canvas.style.height=Math.max(160,mount.clientHeight-this.el('surfacePanel').querySelector('.surface-topline').offsetHeight-this.el('surfaceControls').offsetHeight-this.el('surfaceStatus').offsetHeight-this.el('surfacePanel').querySelector('.view-help').offsetHeight-52)+'px';this.fitCamera();const b=this.canvas.getBoundingClientRect();if(!b.width||!b.height)return;
        const ratio=Math.min(2,window.devicePixelRatio||1),width=Math.round(b.width*ratio),height=Math.round(b.height*ratio);if(this.canvas.width!==width||this.canvas.height!==height){this.canvas.width=width;this.canvas.height=height;}
        gl.bindFramebuffer(gl.FRAMEBUFFER,pick?this.pickBuffer:null);gl.viewport(pick?-pick.x:0,pick?-pick.y:0,width,height);gl.clearColor(pick?0:.06,pick?0:.08,pick?0:.12,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.enable(gl.DEPTH_TEST);gl.disable(gl.DITHER);if(pick)gl.disable(gl.BLEND);else gl.enable(gl.BLEND);gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA);gl.useProgram(this.program);
        const {right,up,forward}=this.axes(),scale=this.zoom/this.radius,aspect=width/height,rows=[right.map(v=>v*scale/aspect),up.map(v=>v*scale),forward.map(v=>-v/(this.radius*3))];
        const columnMajor=m=>new Float32Array([m[0][0],m[1][0],m[2][0],m[0][1],m[1][1],m[2][1],m[0][2],m[1][2],m[2][2]]);
        gl.uniform3f(this.locations.offset,...this.meshCenter.map((v,i)=>v-this.center[i]));gl.uniformMatrix3fv(this.locations.projection,false,columnMajor(rows));gl.uniformMatrix3fv(this.locations.rotation,false,columnMajor([right,up,forward]));
        const entries=[...this.entries.values()].filter(e=>e.visible&&e.opacity>0&&e.buffer);
        for(const e of entries){gl.bindBuffer(gl.ARRAY_BUFFER,e.buffer);gl.enableVertexAttribArray(this.locations.position);gl.vertexAttribPointer(this.locations.position,3,gl.FLOAT,false,24,0);gl.enableVertexAttribArray(this.locations.normal);gl.vertexAttribPointer(this.locations.normal,3,gl.FLOAT,false,24,12);const color=this.data.colors[e.id].map(v=>Math.min(1,v/255*(e.id===this.ui.focus?1.15:1)));gl.uniform4f(this.locations.color,...color,e.opacity);gl.uniform1f(this.locations.pickId,pick?e.id:0);gl.drawArrays(gl.TRIANGLES,0,e.count);}
    }
    async pickAt(x,y){
        if(!this.gl||!this.pickingAvailable)return;const b=this.canvas.getBoundingClientRect(),px=Math.max(0,Math.min(this.canvas.width-1,Math.floor((x-b.left)/b.width*this.canvas.width))),py=Math.max(0,Math.min(this.canvas.height-1,Math.floor((b.bottom-y)/b.height*this.canvas.height))),nx=2*(px+.5)/this.canvas.width-1,ny=2*(py+.5)/this.canvas.height-1,{right,up,forward}=this.axes(),scale=this.zoom/this.radius;
        try{
            this.draw({x:px,y:py});const pixel=new Uint8Array(4),gl=this.gl;gl.readPixels(0,0,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);gl.bindFramebuffer(gl.FRAMEBUFFER,null);this.draw();
            const label=pixel[0];if(!label||!this.entries.has(label))return;
            const depth=(pixel[1]*65536+pixel[2]*256+pixel[3])/16777215,world=this.center.map((v,i)=>v+right[i]*nx*(b.width/b.height)/scale+up[i]*ny/scale+forward[i]*(1-2*depth)*this.radius*3);
            this.focus(label);await this.navigateWorld(world);this.el('surfaceStatus').textContent='انتخاب: '+this.data.labels_fa[label]+' · RAS mm: '+world.map(v=>v.toFixed(2)).join(', ');
        }catch(error){this.gl?.bindFramebuffer(this.gl.FRAMEBUFFER,null);this.draw();this.showError(error,'surfaceStatus');}
    }
    release(){
        ++this.sequence;this.notify('cancelMeshes');if(this.gl){for(const e of this.entries.values())if(e.buffer)this.gl.deleteBuffer(e.buffer);if(this.program)this.gl.deleteProgram(this.program);this.gl.deleteFramebuffer(this.pickBuffer);this.gl.deleteTexture(this.pickTexture);this.gl.deleteRenderbuffer(this.pickDepth);this.gl.clear(this.gl.COLOR_BUFFER_BIT|this.gl.DEPTH_BUFFER_BIT);}
        this.entries.clear();this.gl=null;this.program=null;this.controls();this.el('surfaceProgress').value=0;this.el('surfaceStatus').textContent='پردازش لغو و حافظهٔ سطح آزاد شد. برای ساخت دوباره ناحیه را اضافه کنید.';
    }
}
