"""Native DICOM grid: array axes columns, rows, slices; physical coordinates LPS mm."""
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pydicom
from pydicom.uid import CTImageStorage, MRImageStorage

@dataclass
class NativeVolume:
    array: np.ndarray
    affine_lps: np.ndarray
    datasets: list
    source_sops: list[str]
    @property
    def affine_ras(self):return np.diag([-1.,-1.,1.,1.])@self.affine_lps

def build_native_volume(series):
    paths=series.paths if hasattr(series,'paths') else series
    ds=[pydicom.dcmread(p) if isinstance(p,(str,Path)) else p for p in paths]
    if len(ds)<2:raise ValueError('NOT_3D')
    if any(int(d.get('NumberOfFrames',1))!=1 or str(d.SOPClassUID) not in (str(CTImageStorage),str(MRImageStorage)) for d in ds):raise ValueError('UNSUPPORTED_MULTIFRAME_OR_SOP')
    if any('LOCALIZER' in d.get('ImageType',[]) or 'SCOUT' in d.get('ImageType',[]) for d in ds):raise ValueError('LOCALIZER')
    for field in ['StudyInstanceUID','SeriesInstanceUID','FrameOfReferenceUID','Modality','Rows','Columns']:
        if any(d.get(field) != ds[0].get(field) for d in ds) or not ds[0].get(field):raise ValueError('INCONSISTENT_GEOMETRY')
    for field in ['TemporalPositionIdentifier','EchoNumbers','EchoTime','TriggerTime','DiffusionBValue']:
        values={str(d.get(field,'')) for d in ds}
        if len(values)>1:raise ValueError('MULTIPLE_TIMEPOINTS')
    if any(int(d.get('NumberOfTemporalPositions',1))>1 for d in ds):raise ValueError('MULTIPLE_TIMEPOINTS')
    try:
        iop=np.array(ds[0].ImageOrientationPatient,dtype=float);spacing=np.array(ds[0].PixelSpacing,dtype=float)
        positions=np.array([d.ImagePositionPatient for d in ds],dtype=float)
        orientations=np.array([d.ImageOrientationPatient for d in ds],dtype=float)
    except (AttributeError,ValueError):raise ValueError('MISSING_GEOMETRY') from None
    if iop.shape!=(6,) or positions.shape!=(len(ds),3) or spacing.shape!=(2,) or not np.all(np.isfinite(positions)) or not np.all(np.isfinite(iop)) or not np.all(np.isfinite(spacing)) or np.any(spacing<=0):raise ValueError('INVALID_GEOMETRY')
    x,y=iop[:3],iop[3:]
    if abs(np.linalg.norm(x)-1)>1e-4 or abs(np.linalg.norm(y)-1)>1e-4 or abs(np.dot(x,y))>1e-4:raise ValueError('NON_ORTHOGONAL')
    if not np.allclose(orientations,iop,atol=1e-4,rtol=0):raise ValueError('VARIABLE_ORIENTATION')
    if any(not np.allclose(d.PixelSpacing,spacing,atol=1e-4,rtol=0) for d in ds):raise ValueError('VARIABLE_SPACING')
    normal=np.cross(x,y);normal/=np.linalg.norm(normal)
    order=np.argsort(positions@normal);ds=[ds[i] for i in order];positions=positions[order]
    projection=positions@normal;gaps=np.diff(projection)
    if np.any(gaps<.01):raise ValueError('MULTIPLE_TIMEPOINTS')
    step=float(np.median(gaps));tol=max(.01,.01*step)
    if np.max(np.abs(gaps-step))>tol:raise ValueError('IRREGULAR_SPACING')
    expected=positions[0]+np.outer(projection-projection[0],normal)
    if np.max(np.abs(positions-expected))>.01:raise ValueError('SHEARED_GRID')
    affine=np.eye(4);affine[:3,0]=x*spacing[1];affine[:3,1]=y*spacing[0];affine[:3,2]=normal*step;affine[:3,3]=positions[0]
    pixels=[]
    for d in ds:
        try:p=d.pixel_array
        except (RuntimeError,NotImplementedError,ImportError):raise ValueError('UNSUPPORTED_TRANSFER_SYNTAX') from None
        except Exception:raise ValueError('INVALID_PIXEL_DATA') from None
        if p.shape!=(int(d.Rows),int(d.Columns)):raise ValueError('INVALID_PIXEL_DATA')
        slope=float(d.get('RescaleSlope',1));intercept=float(d.get('RescaleIntercept',0))
        if not np.isfinite(slope) or slope==0 or not np.isfinite(intercept):raise ValueError('INVALID_RESCALE')
        pixels.append(p.astype(np.float32)*slope+intercept)
    array=np.stack(pixels,axis=2).transpose(1,0,2)
    return NativeVolume(array,affine,ds,[str(d.SOPInstanceUID) for d in ds])

def validate_source_order(volume):
    if volume.source_sops!=[str(d.SOPInstanceUID) for d in volume.datasets]:raise ValueError('SOURCE_MAPPING_CHANGED')
    for index,d in enumerate(volume.datasets):
        expected=(volume.affine_lps@np.array([0,0,index,1]))[:3]
        if not np.allclose(expected,d.ImagePositionPatient,atol=.01,rtol=0):raise ValueError('SOURCE_MAPPING_CHANGED')

def resample_masks_to_native(masks,volume):
    from nibabel.processing import resample_from_to
    return {label:(resample_from_to(img,(volume.array.shape,volume.affine_ras),order=0).get_fdata()>0).astype(np.uint8) for label,img in masks.items()}
