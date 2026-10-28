"""Build a self-contained result archive with native CT and original source DICOM."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

import nibabel as nib
import numpy as np
import pydicom

from app.export.persian import persian_name
from tools.common import atomic_write


def file_digest(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):digest.update(block)
    return digest.hexdigest()


def create_portable_package(job_directory,artifacts,source_paths,finalize_result=None):
    """Copy no settings, credentials, browser URLs or absolute paths into the bundle.

    Source paths must be the selected series, in the same order as the native CT.
    Original DICOM bytes are archived without rewriting patient/source metadata.
    """
    root=Path(job_directory);results=root/'results'
    result=json.loads((root/'result.json').read_text())
    state=json.loads((root/'status.json').read_text())
    ct=nib.load(root/'model/native.nii.gz');mask=nib.load(results/'masks.nii.gz')
    if ct.shape!=mask.shape or not np.allclose(ct.affine,mask.affine):raise ValueError('MASK_GEOMETRY_MISMATCH')
    source_paths=list(map(Path,source_paths))
    if len(source_paths)!=ct.shape[2]:raise ValueError('SOURCE_MAPPING_CHANGED')
    sources=[];seen=set();source_identity=None
    affine_lps=np.diag([-1.,-1.,1.,1.])@ct.affine
    for index,path in enumerate(source_paths):
        ds=pydicom.dcmread(path,stop_before_pixels=True)
        identity=(str(ds.StudyInstanceUID),str(ds.SeriesInstanceUID),str(ds.FrameOfReferenceUID))
        uid=str(ds.SOPInstanceUID)
        if (source_identity is not None and identity!=source_identity) or uid in seen:raise ValueError('SOURCE_MAPPING_CHANGED')
        if not np.allclose((affine_lps@np.array([0,0,index,1]))[:3],ds.ImagePositionPatient,atol=.01,rtol=0):raise ValueError('SOURCE_MAPPING_CHANGED')
        if int(ds.Columns)!=ct.shape[0] or int(ds.Rows)!=ct.shape[1]:raise ValueError('SOURCE_MAPPING_CHANGED')
        source_identity=identity;seen.add(uid)
        sources.append((f'dicom/{index+1:06d}.dcm',path,uid))
    additions={'ct':'ct.nii.gz','manifest':'manifest.json','readme':'README-FA.txt','checksums':'SHA256SUMS'}
    shutil.copyfile(root/'model/native.nii.gz',results/'ct.nii.gz');(results/'ct.nii.gz').chmod(0o600)
    readme='''بستهٔ مستقل نتیجهٔ ناحیه‌بندی سی‌تی

۱. ZIP را کامل استخراج کنید و بستهٔ ct-viewer-*.zip را نیز استخراج و index.html را باز کنید.
۲. بستهٔ نمایشگر، تصاویر CT و ماسک همهٔ ناحیه‌ها را همراه دارد؛ به پروژهٔ اصلی، سرور GPU، ورود یا اینترنت نیاز ندارد.
۳. روی «انتخاب همه» بزنید؛ برای دیدن عضو دلخواه از «یافتن» و پیمایش برش‌ها استفاده کنید. همهٔ اعضا در یک برش حضور ندارند.
۴. ct.nii.gz تصویر CT با مقادیر HU و masks.nii.gz ماسک چندناحیه‌ای روی همان شبکه هستند.
۵. جدول شناسهٔ عددی و نام فارسی/لاتین ناحیه‌ها در manifest.json و result.json قرار دارد. شناسه‌ها را از روی رنگ یا ترتیب حدس نزنید.
۶. dicom/ فایل‌های اصلی سری CT انتخاب‌شده را بدون تغییر نگه می‌دارد. segmentation.dcm در صورت موفقیت خروجی، DICOM SEG همان سری است.
۷. measurements.json اندازه‌گیری‌ها و result.json اطلاعات مدل و پردازش را نگه می‌دارند؛ ناحیه‌های خارج از پوشش تصویر ممکن است ناقص یا خالی باشند.
۸. manifest.json فهرست فایل‌ها، هندسه و SHA-256 آن‌ها را دارد. SHA256SUMS با ابزارهای استاندارد قابل بررسی است.
۹. می‌توانید همین ZIP را به‌عنوان یک شیء در MinIO نگه‌داری کنید. برای مشاهده، آن را دریافت و استخراج کنید.
۱۰. برای نمایش مستقیم، پوشهٔ استخراج‌شدهٔ viewer را با HTTP/HTTPS سرو کنید و index.html را باز کنید. فایل‌ها باید کنار هم بمانند.

پاسخ مسیر POST /v1/upload/ بستهٔ ZIP نمایشگر است. پس از استخراج، فایل‌های پشتیبان نسبت به index.html خودکار بارگذاری می‌شوند.
این بسته اطلاعات اصلی DICOM را حفظ می‌کند و ناشناس‌سازی نشده است. نتایج خودکار و بازبینی‌نشده‌اند.
'''
    atomic_write(results/'README-FA.txt',readme)
    paths={name:results/name for key,name in {**artifacts,**additions}.items() if key not in ('archive','manifest','checksums')}
    paths.update({name:path for name,path,_ in sources})
    files={name:{'size_bytes':path.stat().st_size,'sha256':file_digest(path)} for name,path in paths.items()}
    present={row['label'] for row in result['structures'] if row.get('volume_ml')}
    manifest={'schema_version':'1.0','package_type':'portable_ct_segmentation','job_id':root.name,
              'viewer_entrypoint':artifacts.get('viewer'),
              'self_contained':True,'viewer_available':'viewer' in artifacts,
              'source_dicom_included':True,'dicom_metadata_preserved':True,
              'review_status':result['review_status'],'model_release':result['model_release'],'quality':result['quality'],
              'selected_series_index':state.get('selected_series_index'),
              'series_selection_policy':state.get('series_selection_policy','manual'),
              'source':{'study_instance_uid':source_identity[0],'series_instance_uid':source_identity[1],
                        'frame_of_reference_uid':source_identity[2],
                        'instances':[{'path':name,'sop_instance_uid':uid} for name,_,uid in sources]},
              'geometry':{'shape':list(ct.shape),'affine_ras_mm':ct.affine.tolist(),
                          'array_axes':['column','row','slice'],'ct_units':'HU','mask_background':0},
              'processed_region_count':len(result['labels']),'nonempty_region_count':len(present),
              'labels':{number:{'name':name,'name_fa':persian_name(name),'present':name in present} for number,name in result['labels'].items()},
              'files':files}
    descriptor,temporary=tempfile.mkstemp(prefix='.portable-',suffix='.zip',dir=results)
    os.close(descriptor)
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for name,path in paths.items():
                # Gzip NIfTI and PNG are already compressed. Recompressing them
                # consumes CPU and offers little additional size reduction.
                compression=zipfile.ZIP_STORED if name.endswith(('.gz','.png')) else zipfile.ZIP_DEFLATED
                archive.write(path,name,compress_type=compression)
            # Finalize timings after the large payload is compressed, before
            # hashing result metadata, so the archive and its checksums agree.
            if finalize_result is not None:
                result=finalize_result()
                atomic_write(root/'result.json',json.dumps(result))
            result_path=root/'result.json'
            files['result.json']={'size_bytes':result_path.stat().st_size,'sha256':file_digest(result_path)}
            atomic_write(results/'manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            checksums={name:metadata['sha256'] for name,metadata in files.items()}
            checksums['manifest.json']=file_digest(results/'manifest.json')
            atomic_write(results/'SHA256SUMS',''.join(f'{digest}  {name}\n' for name,digest in sorted(checksums.items())))
            for name,path in [('result.json',result_path),('manifest.json',results/'manifest.json'),('SHA256SUMS',results/'SHA256SUMS')]:archive.write(path,name)
        os.replace(temporary,results/'results.zip')
    finally:Path(temporary).unlink(missing_ok=True)
    return additions
