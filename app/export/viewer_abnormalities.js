/* Offline, coarse attribution overlays. CT and segmentation assets are immutable. */
(() => {
'use strict';
function inverseAffine(a){
    if(!Array.isArray(a)||a.length!==4||a.some(row=>!Array.isArray(row)||row.length!==4||row.some(v=>!Number.isFinite(v))))throw Error('هندسهٔ نقشه معتبر نیست.');
    const b=a.slice(0,3).map(row=>row.slice(0,3));
    const det=b[0][0]*(b[1][1]*b[2][2]-b[1][2]*b[2][1])-b[0][1]*(b[1][0]*b[2][2]-b[1][2]*b[2][0])+b[0][2]*(b[1][0]*b[2][1]-b[1][1]*b[2][0]);
    if(Math.abs(det)<1e-12)throw Error('هندسهٔ نقشه تکین است.');
    const out=Array.from({length:3},()=>[0,0,0,0]);
    for(let i=0;i<3;i++)for(let j=0;j<3;j++)out[i][j]=(b[(j+1)%3][(i+1)%3]*b[(j+2)%3][(i+2)%3]-b[(j+1)%3][(i+2)%3]*b[(j+2)%3][(i+1)%3])/det;
    for(let i=0;i<3;i++)out[i][3]=-out[i].slice(0,3).reduce((sum,v,j)=>sum+v*a[j][3],0);
    return out;
}
function compose(a,b){return a.map(row=>[0,1,2,3].map(j=>row[0]*b[0][j]+row[1]*b[1][j]+row[2]*b[2][j]+(j===3?row[3]:0)));}
function sample(values,shape,x,y,z){
    const [nx,ny,nz]=shape;
    if(x<-.5||y<-.5||z<-.5||x>=nx-.5||y>=ny-.5||z>=nz-.5)return 0;
    x=Math.max(0,Math.min(nx-1,x));y=Math.max(0,Math.min(ny-1,y));z=Math.max(0,Math.min(nz-1,z));
    const x0=Math.floor(x),y0=Math.floor(y),z0=Math.floor(z),x1=Math.min(x0+1,nx-1),y1=Math.min(y0+1,ny-1),z1=Math.min(z0+1,nz-1),dx=x-x0,dy=y-y0,dz=z-z0;
    const at=(i,j,k)=>values[i+nx*(j+ny*k)],mix=(a,b,t)=>a+(b-a)*t;
    return mix(mix(mix(at(x0,y0,z0),at(x1,y0,z0),dx),mix(at(x0,y1,z0),at(x1,y1,z0),dx),dy),mix(mix(at(x0,y0,z1),at(x1,y0,z1),dx),mix(at(x0,y1,z1),at(x1,y1,z1),dx),dy),dz)/255;
}
class OfflineAbnormalities{
    constructor({data,el,isActive}){
        Object.assign(this,{data,el,isActive});this.meta=data.abnormality_heatmaps;this.result=data.abnormality_classification;
        this.cache=new Map();this.ticket=0;this.index=data.start;this.finding=0;this.ctPixels=null;this.values=null;
        this.ct=el('heatmapCt');this.overlay=el('heatmapOverlay');this.format=new Intl.NumberFormat('fa-IR',{maximumFractionDigits:2});
        this.available=!!this.meta&&this.meta.kind==='model_attribution'&&!!data.geometry?.valid;
        el('heatmapArea').hidden=!this.available;el('heatmapUnavailable').hidden=this.available;
        el('abnormalitiesScores').open=!this.available;
        if(!this.available){if(this.meta&&!data.geometry?.valid)el('heatmapUnavailable').textContent='هندسهٔ معتبر برای تطبیق نقشه با CT موجود نیست؛ overlay نمایش داده نمی‌شود.';return;}
        if(this.meta.shape.length!==3||this.meta.shape.some(n=>!Number.isInteger(n)||n<1||n>64)||this.meta.maps.length!==this.result.abnormalities.length)throw Error('اندازهٔ نقشهٔ حرارتی معتبر نیست.');
        this.transform=compose(inverseAffine(this.meta.affine_ras_mm),data.geometry.affine_ras_mm);
        this.nativeInverse=inverseAffine(data.geometry.affine_ras_mm);
        for(const [i,row] of this.result.abnormalities.entries()){
            if(this.meta.maps[i].name!==row.name)throw Error('ترتیب یافته‌ها با نقشه‌ها یکسان نیست.');
            const option=document.createElement('option');option.value=String(i);option.textContent=row.name+(row.flagged?' · پرچم‌دار':'');el('heatmapFinding').append(option);
        }
        this.finding=Math.max(0,this.result.abnormalities.findIndex(row=>row.flagged));el('heatmapFinding').value=String(this.finding);
        this.index=this.peakSlice()??data.start;el('heatmapSlice').max=data.frames.length-1;
        el('heatmapFinding').onchange=()=>this.selectFinding(Number(el('heatmapFinding').value));
        el('heatmapSlice').oninput=()=>this.setSlice(Number(el('heatmapSlice').value));
        el('heatmapPrevious').onclick=()=>this.setSlice(this.index-1);el('heatmapNext').onclick=()=>this.setSlice(this.index+1);
        el('heatmapPeak').onclick=()=>this.setSlice(this.peakSlice()??this.index);
        for(const id of ['heatmapVisible','heatmapOpacity','heatmapThreshold'])el(id).addEventListener(id==='heatmapVisible'?'change':'input',()=>this.drawOverlay());
        el('heatmapWindow').onchange=()=>this.drawCT();el('heatmapRetry').onclick=()=>this.render();
    }
    peakSlice(){
        const p=this.meta.maps[this.finding].peak_ras_mm;if(!p)return null;
        const row=this.nativeInverse[2],z=Math.round(row[0]*p[0]+row[1]*p[1]+row[2]*p[2]+row[3]);
        return Math.max(0,Math.min(this.data.frames.length-1,z));
    }
    show(){if(this.available)return this.render();}
    hide(){++this.ticket;}
    selectFinding(i){this.finding=i;this.el('heatmapFinding').value=String(i);this.index=this.peakSlice()??this.index;return this.render();}
    setSlice(z){this.index=Math.max(0,Math.min(this.data.frames.length-1,Number(z)));return this.render();}
    async map(i){
        if(this.cache.has(i))return this.cache.get(i);
        const image=await ViewerLoader.pixels(this.meta.maps[i].url),[nx,ny,nz]=this.meta.shape;
        if(image.width!==nx||image.height!==ny*nz)throw Error('ابعاد فایل نقشه معتبر نیست.');
        const values=new Uint8Array(nx*ny*nz);for(let j=0;j<values.length;j++)values[j]=image.pixels[j*4];this.cache.set(i,values);return values;
    }
    async render(){
        const ticket=++this.ticket,z=this.index,finding=this.finding;this.ctPixels=null;this.values=null;
        for(const canvas of [this.ct,this.overlay])canvas.getContext('2d').clearRect(0,0,canvas.width,canvas.height);
        this.overlay.dataset.nonzeroCount='0';
        this.el('heatmapRetry').hidden=true;this.el('heatmapStatus').textContent='در حال بارگذاری تصویر و نقشهٔ مشارکت…';
        this.el('heatmapCounter').textContent='برش '+this.format.format(z+1)+' از '+this.format.format(this.data.frames.length);
        this.el('heatmapSlice').value=z;this.el('heatmapPrevious').disabled=z===0;this.el('heatmapNext').disabled=z===this.data.frames.length-1;
        this.el('heatmapPeak').disabled=this.peakSlice()==null;
        const row=this.result.abnormalities[finding];this.el('heatmapCaseInfo').textContent=row.name+' · امتیاز کل سری: '+this.format.format(row.score)+' · '+(row.flagged?'پرچم‌دار':'بدون پرچم')+' · شبکهٔ نقشه: '+this.meta.spacing_mm.map(n=>this.format.format(n)).join(' × ')+' میلی‌متر';
        try{
            const [image,heatmap]=await Promise.all([ViewerLoader.pixels(this.data.frames[z]),this.map(finding)]);
            if(ticket!==this.ticket||!this.isActive())return;
            for(const canvas of [this.ct,this.overlay]){canvas.width=image.width;canvas.height=image.height;}
            this.ctPixels=image.pixels;this.values=new Float32Array(image.width*image.height);
            const t=this.transform;
            for(let y=0;y<image.height;y++){
                let hx=t[0][1]*y+t[0][2]*z+t[0][3],hy=t[1][1]*y+t[1][2]*z+t[1][3],hz=t[2][1]*y+t[2][2]*z+t[2][3];
                for(let x=0;x<image.width;x++,hx+=t[0][0],hy+=t[1][0],hz+=t[2][0])this.values[x+image.width*y]=sample(heatmap,this.meta.shape,hx,hy,hz);
            }
            this.drawCT();this.drawOverlay();this.el('heatmapStatus').textContent=this.meta.maps[finding].available?'نقشهٔ نسبی مشارکت مدل؛ ماسک ضایعه نیست.':'مشارکت مثبت قابل نمایش برای این یافته ثبت نشده است.';
        }catch(error){if(ticket===this.ticket&&this.isActive()){this.ctPixels=null;this.values=null;this.el('heatmapStatus').textContent='بارگذاری انجام نشد: '+error.message;this.el('heatmapRetry').hidden=false;}}
    }
    drawCT(){
        if(!this.ctPixels)return;
        const presets={soft:[40,400],lung:[-600,1500],bone:[300,1600]},[level,width]=presets[this.el('heatmapWindow').value];
        const ctx=this.ct.getContext('2d'),out=ctx.createImageData(this.ct.width,this.ct.height);
        for(let i=0;i<this.ctPixels.length;i+=4){const hu=(this.ctPixels[i]+256*this.ctPixels[i+1])*this.data.hu_step+this.data.hu_offset;
            const v=Math.round(Math.max(0,Math.min(1,(hu-level+width/2)/width))*255);out.data[i]=out.data[i+1]=out.data[i+2]=v;out.data[i+3]=255;}
        ctx.putImageData(out,0,0);
    }
    drawOverlay(){
        const opacity=Number(this.el('heatmapOpacity').value)/100,threshold=Number(this.el('heatmapThreshold').value)/100;
        this.el('heatmapOpacityValue').textContent=this.format.format(opacity*100)+'٪';this.el('heatmapThresholdValue').textContent=this.format.format(threshold);
        if(!this.values)return;
        const ctx=this.overlay.getContext('2d'),out=ctx.createImageData(this.overlay.width,this.overlay.height),visible=this.el('heatmapVisible').checked;let count=0;
        if(visible)for(let i=0;i<this.values.length;i++){
            const value=this.values[i];if(value<=threshold)continue;
            out.data[4*i]=255;out.data[4*i+1]=Math.round(255*(1-value));out.data[4*i+2]=0;
            out.data[4*i+3]=Math.round(255*opacity*(value-threshold)/(1-threshold));if(out.data[4*i+3])count++;
        }
        ctx.putImageData(out,0,0);this.overlay.dataset.nonzeroCount=String(count);
    }
}
window.OfflineAbnormalities=OfflineAbnormalities;
window.AbnormalitySampling={sample,inverseAffine,compose};
})();
