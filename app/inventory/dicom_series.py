from collections import defaultdict
import hashlib
import pydicom
from pydicom.uid import UID
from app.contracts import SeriesItem
from app.geometry.native_grid import build_native_volume

INVALID={'DUPLICATE_SOP_CONFLICT','MULTIPLE_PATIENTS','INVALID_PIXEL_DATA','INVALID_GEOMETRY','INVALID_RESCALE'}

def inventory_paths(paths):
    groups=defaultdict(list);patients=set();sops={};conflicts=set()
    for path in paths:
        try:d=pydicom.dcmread(path,stop_before_pixels=True)
        except (pydicom.errors.InvalidDicomError,ValueError,OSError):continue
        if not all(d.get(k) and UID(str(d.get(k))).is_valid for k in ['StudyInstanceUID','SeriesInstanceUID','SOPInstanceUID']):continue
        patients.add((str(d.get('PatientID','')),str(d.get('IssuerOfPatientID',''))))
        key=(str(d.StudyInstanceUID),str(d.SeriesInstanceUID));sop=str(d.SOPInstanceUID)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if sop in sops:
            prev_digest,prev_key=sops[sop]
            if digest!=prev_digest:conflicts.update([key,prev_key])
            continue
        sops[sop]=(digest,key);groups[key].append((path,d))
    result=[]
    for (study,series),rows in groups.items():
        d=rows[0][1];item=SeriesItem(study,series,str(d.get('Modality','')),str(d.get('FrameOfReferenceUID','')),[p for p,_ in rows])
        try:
            if len(patients)>1:raise ValueError('MULTIPLE_PATIENTS')
            if (study,series) in conflicts:raise ValueError('DUPLICATE_SOP_CONFLICT')
            if item.modality not in ('CT','MR'):raise ValueError('UNSUPPORTED_MODALITY')
            v=build_native_volume(item)
            item.source_instances=v.source_sops
            item.paths=[next(p for p,d in rows if str(d.SOPInstanceUID)==uid) for uid in v.source_sops]
            item.geometry_summary={'shape':list(v.array.shape),'affine_lps':v.affine_lps.tolist(),'affine_ras':v.affine_ras.tolist(),'coordinate_units':'mm','array_axes':['column','row','slice'],'slices':len(v.datasets)}
        except ValueError as e:
            item.safe_error=str(e);item.eligibility='invalid' if str(e) in INVALID else 'unsupported'
        result.append(item)
    return result

def inventory_series(input):
    from app.inventory.archive import extract_zip
    return inventory_paths(extract_zip(input.path,input.path.parent/'extracted'))
