/* All paths are relative to index.html, including when hosted under a prefix. */
(() => {
'use strict';
const base=document.baseURI, local=location.protocol==='file:', companions=new Map(), pending=new Map(), decoded=new Map(), jsonCache=new Map(), scripts=new Map();
const MAX_BYTES=32*1024*1024, MAX_IMAGES=12;let bytes=0,decoding=0;const decodeQueue=[];
function acquireDecode(){if(decoding<2){decoding++;return Promise.resolve();}return new Promise(resolve=>decodeQueue.push(resolve));}
function releaseDecode(){if(decodeQueue.length)decodeQueue.shift()();else decoding--;}
const url=path=>new URL(path,base).href;
async function response(path){const r=await fetch(url(path),{cache:'no-cache'});if(!r.ok)throw Error(path+' (HTTP '+r.status+')');return r;}
function once(key,work){if(pending.has(key))return pending.get(key);const p=work().finally(()=>pending.delete(key));pending.set(key,p);return p;}
function register(path,value){const entry=companions.get(path);if(entry){entry.value=value;entry.received=true;}}
function companion(path){
    return new Promise((resolve,reject)=>{
        const node=document.createElement('script'),entry={received:false};companions.set(path,entry);
        const finish=()=>{node.remove();companions.delete(path);};
        node.onload=()=>{finish();entry.received?resolve(entry.value):reject(Error('دادهٔ فایل معتبر نیست: '+path));};
        node.onerror=()=>{finish();reject(Error('بارگذاری '+path+'.js انجام نشد'));};
        node.src=url(path+'.js');document.head.append(node);
    });
}
async function json(path){if(jsonCache.has(path))return jsonCache.get(path);return once('json:'+path,async()=>{const value=local?await companion(path):await(await response(path)).json();jsonCache.set(path,value);return value;});}
async function pixels(path){
    if(decoded.has(path)){const value=decoded.get(path);decoded.delete(path);decoded.set(path,value);return value;}
    return once('image:'+path,async()=>{
        await acquireDecode();let objectUrl=null;
        try{
            const img=new Image();
            if(local)img.src='data:image/png;base64,'+await companion(path);
            else{const blob=await(await response(path)).blob();objectUrl=URL.createObjectURL(blob);img.src=objectUrl;}
            await img.decode();
            const c=document.createElement('canvas');c.width=img.width;c.height=img.height;
            const context=c.getContext('2d',{willReadFrequently:true});context.drawImage(img,0,0);
            const value={width:c.width,height:c.height,pixels:context.getImageData(0,0,c.width,c.height).data};
            if(value.pixels.byteLength<=MAX_BYTES){decoded.set(path,value);bytes+=value.pixels.byteLength;
                while(bytes>MAX_BYTES||decoded.size>MAX_IMAGES){const key=decoded.keys().next().value;bytes-=decoded.get(key).pixels.byteLength;decoded.delete(key);}}
            c.width=c.height=0;return value;
        }catch(error){throw Error('بارگذاری '+path+' انجام نشد: '+error.message);}
        finally{if(objectUrl)URL.revokeObjectURL(objectUrl);releaseDecode();}
    });
}
function script(path){if(scripts.has(path))return scripts.get(path);const p=new Promise((resolve,reject)=>{const node=document.createElement('script');node.src=url(path);node.onload=resolve;node.onerror=()=>{node.remove();scripts.delete(path);reject(Error('بارگذاری '+path+' انجام نشد'));};document.head.append(node);});scripts.set(path,p);return p;}
window.ViewerLoader={url,json,pixels,script,register,stats:()=>({decodedImages:decoded.size,decodedBytes:bytes,pending:pending.size,activeDecodes:decoding,maxBytes:MAX_BYTES,maxImages:MAX_IMAGES})};
async function boot(){
    const status=document.getElementById('packageStatus');
    try{window.ViewerData=await json('data/manifest.json');await script('assets/native-viewer.js');await script('assets/advanced-viewer.js');status.hidden=true;await render(window.ViewerData.start);}
    catch(error){status.hidden=false;status.textContent='بازکردن نمایشگر انجام نشد: '+error.message+' ';const retry=document.createElement('button');retry.textContent='تلاش دوباره';retry.onclick=boot;status.append(retry);}
}
boot();
})();
