import numpy as np
import highdicom as hd
from pydicom.sr.coding import Code
from pydicom.uid import generate_uid
from app.geometry.native_grid import validate_source_order

ANATOMY={'liver':Code('10200004','SCT','Liver'),'spleen':Code('78961009','SCT','Spleen'),'kidney_left':Code('46862004','SCT','Left kidney'),'kidney_right':Code('9846003','SCT','Right kidney')}
COLORS={'liver':(65,35,55),'spleen':(65,-30,40),'kidney_left':(65,-35,-20),'kidney_right':(65,35,-35)}

def export_seg(masks,volume,model,path,max_bytes=1024**3):
    validate_source_order(volume)
    if np.prod(volume.array.shape)*4>max_bytes:raise ValueError('EXPORT_MEMORY_LIMIT')
    labels=np.zeros(volume.array.shape,np.uint8);descriptions=[]
    for number,label in enumerate(sorted(masks),1):
        if label not in ANATOMY:raise ValueError('UNKNOWN_ANATOMICAL_CODE')
        mask=np.asarray(masks[label],dtype=bool)
        if mask.shape!=labels.shape:raise ValueError('MASK_GEOMETRY_MISMATCH')
        if np.any(labels[mask]):raise ValueError('OVERLAPPING_SEGMENTS')
        labels[mask]=number
        descriptions.append(hd.seg.SegmentDescription(segment_number=number,segment_label=label,segmented_property_category=Code('85756007','SCT','Tissue'),segmented_property_type=ANATOMY[label],algorithm_type='AUTOMATIC',display_color=hd.color.CIELabColor(*COLORS[label]),algorithm_identification=hd.AlgorithmIdentificationSequence(name='TotalSegmentator',version=model['model_release'],family=Code('123110','DCM','Artificial Intelligence'))))
    if not np.any(labels):raise ValueError('NO_SEGMENTS')
    seg=hd.seg.Segmentation(source_images=volume.datasets,pixel_array=labels.transpose(2,1,0),segmentation_type='BINARY',segment_descriptions=descriptions,series_instance_uid=generate_uid(),series_number=900,sop_instance_uid=generate_uid(),instance_number=1,manufacturer='SharifClinic',manufacturer_model_name='TotalSegmentator',software_versions=[model['model_release'],model['pipeline_version']],device_serial_number='segmentation-worker',omit_empty_frames=True,content_description='Automatic segmentation; unreviewed',content_label='AUTOSEG')
    seg.save_as(path,enforce_file_format=True)
    if path.stat().st_size>max_bytes:
        path.unlink();raise ValueError('ARTIFACT_SIZE_LIMIT')
    return path


def export_labelmap_seg(labelmap,labels,volume,model,path,max_bytes=1024**3):
    """DICOM LABELMAP SEG supports the complete non-overlapping model efficiently."""
    validate_source_order(volume)
    if labelmap.shape!=volume.array.shape:raise ValueError('MASK_GEOMETRY_MISMATCH')
    if not np.any(labelmap):raise ValueError('NO_SEGMENTS')
    descriptions=[]
    for number,label in labels.items():
        anatomy=ANATOMY.get(label) or Code(str(number),'99TOTALSEG',label.replace('_',' '),model['model_release'])
        descriptions.append(hd.seg.SegmentDescription(segment_number=int(number),segment_label=label,
            segmented_property_category=Code('85756007','SCT','Tissue'),segmented_property_type=anatomy,
            algorithm_type='AUTOMATIC',algorithm_identification=hd.AlgorithmIdentificationSequence(
                name='TotalSegmentator',version=model['model_release'],family=Code('123110','DCM','Artificial Intelligence'))))
    seg=hd.seg.Segmentation(source_images=volume.datasets,pixel_array=labelmap.transpose(2,1,0),
        segmentation_type='LABELMAP',segment_descriptions=descriptions,series_instance_uid=generate_uid(),
        series_number=900,sop_instance_uid=generate_uid(),instance_number=1,manufacturer='SharifClinic',
        manufacturer_model_name='TotalSegmentator',software_versions=[model['model_release'],model['pipeline_version']],
        device_serial_number='segmentation-worker',omit_empty_frames=True,
        content_description='Automatic segmentation; unreviewed',content_label='AUTOSEG')
    seg.save_as(path,enforce_file_format=True)
    if path.stat().st_size>max_bytes:
        path.unlink();raise ValueError('ARTIFACT_SIZE_LIMIT')
    return path
