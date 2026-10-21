"""Additive geometry/provenance for the offline viewer, never inferred orientation.

NIfTI fallback affines (qform_code == sform_code == 0) are not patient geometry.
The native DICOM pipeline may explicitly supply its already validated mm affine.
"""
import hashlib
import json

import nibabel as nib
import numpy as np


def viewer_geometry(ct, affine, transform, context):
    context = context or {}
    units = ct.header.get_xyzt_units()[0]
    coded = bool(int(ct.header['sform_code']) or int(ct.header['qform_code']))
    source = 'nifti_coded_affine'
    problem = None
    if context.get('validated_affine_ras_mm') is not None:
        supplied = np.asarray(context['validated_affine_ras_mm'], dtype=float)
        if (supplied.shape != (4, 4)
                or not np.allclose(supplied[:3, :3], ct.affine[:3, :3], atol=1e-5, rtol=1e-6)
                or not np.allclose(supplied[:3, 3], ct.affine[:3, 3], atol=.01, rtol=0)
                or not np.allclose(supplied[3], [0, 0, 0, 1], atol=1e-12, rtol=0)):
            raise ValueError('VIEWER_SOURCE_GEOMETRY_MISMATCH')
        scale = 1.
        source = 'validated_native_dicom_mm'
    elif not coded:
        scale = 1.
        problem = 'جهت و مبدأ معتبر موجود نیست (NIfTI بدون qform/sform).'
    elif units not in ('mm', 'meter', 'micron'):
        scale = 1.
        problem = 'واحد فیزیکی NIfTI مشخص نیست؛ هندسهٔ میلی‌متری تأیید نشده است.'
    else:
        scale = {'mm': 1., 'meter': 1000., 'micron': .001}[units]
    a = (supplied @ nib.orientations.inv_ornt_aff(transform, ct.shape)
         if source == 'validated_native_dicom_mm' else np.asarray(affine, dtype=float).copy())
    a[:3] *= scale
    basis = a[:3, :3]
    spacing = np.linalg.norm(basis, axis=0)
    if (not np.all(np.isfinite(a)) or np.any(spacing <= 0)
            or abs(np.linalg.det(basis)) < 1e-12):
        problem = 'هندسهٔ فیزیکی نامعتبر یا تکین است.'
    # Acquisition k axis after the exact same permutation/flips as CT/masks.
    section_axis = int(transform[2, 0])
    return {
        'valid': problem is None, 'unavailable_reason': problem, 'source': source,
        'affine_ras_mm': a.tolist() if problem is None else None,
        'original_affine_ras': ct.affine.tolist(),
        'voxel_transform': nib.orientations.inv_ornt_aff(transform, ct.shape).tolist(),
        'coordinate_system': 'RAS', 'voxel_coordinates': 'centers; boundaries at index +/- 0.5',
        'spacing_mm': spacing.tolist() if problem is None else None,
        'section_axis': section_axis,
        'section_order': 'reoriented index order; not original DICOM instance order',
        'section_definition': 'native_acquisition_planes_no_resampling',
        'section_is_patient_axial': bool(problem is None and np.allclose(
            np.abs(basis[:, section_axis] / spacing[section_axis]), [0, 0, 1], atol=1e-5)),
    }


def original_fingerprint(image, segmentation, geometry):
    """SHA-256 over canonical metadata and original voxel content, slice bounded."""
    digest = hashlib.sha256()
    digest.update(json.dumps({'shape': image.shape, 'geometry': geometry}, sort_keys=True,
                             separators=(',', ':')).encode())
    for z in range(image.shape[2]):
        digest.update(np.asarray(image[:, :, z], dtype='<f8').tobytes(order='C'))
        digest.update(np.asarray(segmentation[:, :, z], dtype='u1').tobytes(order='C'))
    return 'sha256:' + digest.hexdigest()
