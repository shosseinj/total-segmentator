"""Server-built viewer derivatives. Original CT/masks and measurements stay native.

Only display MPR is resampled. All derived assets travel as separate files inside the viewer ZIP;
the browser decodes current planes/selected meshes without reconstructing a volume.
"""
import base64
import io
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image
from scipy.ndimage import find_objects

MPR_MAX_AXIS = 512
MPR_MAX_VOXELS = 16 * 1024 * 1024
MESH_MAX_VERTICES = 2400000
MESH_MAX_ENCODED_BYTES = 64 * 1024 * 1024
AXES = {'axial': [0, 1, 2], 'coronal': [0, 2, 1], 'sagittal': [1, 2, 0]}
FACES = [
    [[0,0,0],[0,0,1],[0,1,1],[0,1,0]],
    [[1,0,0],[1,1,0],[1,1,1],[1,0,1]],
    [[0,0,0],[1,0,0],[1,0,1],[0,0,1]],
    [[0,1,0],[0,1,1],[1,1,1],[1,1,0]],
    [[0,0,0],[0,1,0],[1,1,0],[1,0,0]],
    [[0,0,1],[1,0,1],[1,1,1],[0,1,1]],
]


def png(array):
    stream = io.BytesIO()
    Image.fromarray(array).save(stream, format='PNG', compress_level=3)
    return base64.b64encode(stream.getvalue()).decode('ascii')


def binary_png(array):
    """Lossless RGB byte container; no compression library needed in the browser."""
    raw = np.frombuffer(np.asarray(array, dtype='<f4').tobytes(), dtype=np.uint8)
    width = min(1024, max(1, (len(raw)+2)//3))
    packed = np.zeros(((len(raw)+width*3-1)//(width*3), width, 3), np.uint8)
    packed.reshape(-1)[:len(raw)] = raw
    return {'png': png(packed), 'byte_length': len(raw), 'encoding': 'rgb-png-float32-le'}


def world_bounds(shape, affine):
    corners = np.array([[shape[a]-.5 if k & (1 << a) else -.5 for a in range(3)] for k in range(8)])
    points = corners @ affine[:3, :3].T + affine[:3, 3]
    return {'lower': points.min(axis=0).tolist(), 'upper': points.max(axis=0).tolist()}


def measurements(segmentation, geometry, labels, boxes):
    a = np.array(geometry['affine_ras_mm'])
    axis = geometry['section_axis']
    half = np.abs(a[:3, :3]).sum(axis=1)/2
    voxel = abs(np.linalg.det(a[:3, :3]))
    area = np.linalg.norm(np.cross(a[:3, (axis+1)%3], a[:3, (axis+2)%3]))
    rows = {}
    for key in labels:
        n = int(key)
        row = dict(id=n, count=0, volume_ml=None, dimensions_mm=None, lower=None, upper=None,
                   voxel_lower=None, voxel_upper=None, areas=[0]*segmentation.shape[axis],
                   max_slice=None, max_area_mm2=None, section_points=[], boundary=False,
                   status='empty', revision=0)
        box = boxes[n-1] if n <= len(boxes) else None
        if box:
            coords = np.column_stack(np.nonzero(segmentation[box] == n))
            coords += np.array([s.start for s in box])
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            for start in range(0, len(coords), 262144):
                points = coords[start:start+262144] @ a[:3, :3].T + a[:3, 3]
                lo = np.minimum(lo, points.min(axis=0))
                hi = np.maximum(hi, points.max(axis=0))
            lo -= half
            hi += half
            areas = np.bincount(coords[:, axis], minlength=segmentation.shape[axis])*area
            sections = [None]*segmentation.shape[axis]
            values, indices = np.unique(coords[:, axis], return_index=True)
            for s, i in zip(values, indices):
                sections[int(s)] = coords[i].tolist()
            vlo, vhi = coords.min(axis=0), coords.max(axis=0)
            boundary = bool(np.any(vlo == 0) or np.any(vhi == np.array(segmentation.shape)-1))
            row.update(count=len(coords), volume_ml=len(coords)*voxel/1000,
                       lower=lo.tolist(), upper=hi.tolist(), dimensions_mm=(hi-lo).tolist(),
                       voxel_lower=vlo.tolist(), voxel_upper=vhi.tolist(), areas=areas.tolist(),
                       max_slice=int(np.argmax(areas)), max_area_mm2=float(areas.max()),
                       section_points=sections, boundary=boundary,
                       status='partial' if boundary else 'measured')
        rows[key] = row
    return rows


def mpr_grid(geometry, bounds):
    lower, upper = np.array(bounds['lower']), np.array(bounds['upper'])
    extent = upper-lower
    step = max(min(geometry['spacing_mm']), float(extent.max()/MPR_MAX_AXIS))
    shape = np.maximum(1, np.ceil(extent/step).astype(int))
    while np.prod(shape, dtype=np.int64) > MPR_MAX_VOXELS:
        step *= max(1.01, (np.prod(shape)/MPR_MAX_VOXELS)**(1/3))
        shape = np.maximum(1, np.ceil(extent/step).astype(int))
    spacing = extent/shape
    return shape, spacing


def mpr_assets(image, segmentation, geometry, offset, hu_step, bounds):
    lower, upper = np.array(bounds['lower']), np.array(bounds['upper'])
    shape, spacing = mpr_grid(geometry, bounds)
    inv = np.linalg.inv(geometry['affine_ras_mm'])
    # Array order is patient x,y,z. Every plane samples this same physical grid.
    packed = np.zeros((*shape, 4), dtype=np.uint8)
    x, y = np.meshgrid(upper[0]-(np.arange(shape[0])+.5)*spacing[0],
                       upper[1]-(np.arange(shape[1])+.5)*spacing[1], indexing='ij')
    for z in range(shape[2]):
        world_z = upper[2]-(z+.5)*spacing[2]
        voxel = [np.floor(inv[a,0]*x+inv[a,1]*y+inv[a,2]*world_z+inv[a,3]+.5).astype(np.int32) for a in range(3)]
        valid = np.ones(x.shape, dtype=bool)
        for axis in range(3):
            valid &= (voxel[axis] >= 0) & (voxel[axis] < image.shape[axis])
        p = tuple(v[valid] for v in voxel)
        values = np.rint((image[p]-offset)/hu_step).clip(0, 65535).astype(np.uint16)
        frame = packed[:,:,z]
        frame[valid,0] = values & 255
        frame[valid,1] = values >> 8
        frame[valid,2] = segmentation[p]
        frame[valid,3] = 255
    result = {}
    for kind, axes in AXES.items():
        u, v, f = axes
        display = packed.transpose(v, u, f, 3)
        with ThreadPoolExecutor(max_workers=4) as pool:
            frames = list(pool.map(lambda i: png(display[:,:,i]), range(shape[f])))
        result[kind] = dict(kind=kind, axes=axes, width=int(shape[u]), height=int(shape[v]),
                            lower=lower.tolist(), upper=upper.tolist(), step_u=float(spacing[u]),
                            step_v=float(spacing[v]), step_f=float(spacing[f]), frames=frames)
    return {'planes': result, 'shape': shape.tolist(), 'spacing_mm': spacing.tolist(),
            'sampling': 'nearest native voxel at common RAS grid centers',
            'max_voxels': MPR_MAX_VOXELS, 'max_axis': MPR_MAX_AXIS}


def rectangles(plane):
    """Merge identical consecutive face runs; preserves exact voxel boundaries."""
    active = {}
    for row in range(plane.shape[0]+1):
        runs = []
        if row < plane.shape[0]:
            changes = np.diff(np.pad(plane[row].astype(np.int8), (1,1)))
            runs = list(zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)))
        current = set(runs)
        for run in list(active):
            if run not in current:
                yield active.pop(run), row, *run
        for run in runs:
            active.setdefault(run, row)


def surface_asset(segmentation, n, box, affine, center):
    region = segmentation[box] == n
    origin = np.array([s.start for s in box], dtype=float)-.5
    chunks = []
    count = 0
    for face, corners in enumerate(FACES):
        axis, positive = face//2, face%2
        neighbor = np.zeros_like(region)
        dst, src = [slice(None)]*3, [slice(None)]*3
        dst[axis], src[axis] = (slice(None,-1), slice(1,None)) if positive else (slice(1,None), slice(None,-1))
        neighbor[tuple(dst)] = region[tuple(src)]
        boundary = region & ~neighbor
        uv = [a for a in range(3) if a != axis]
        template = np.array(corners, dtype=float)
        normal = np.cross(affine[:3,:3] @ (template[1]-template[0]),
                          affine[:3,:3] @ (template[2]-template[0]))
        normal /= np.linalg.norm(normal)
        for section in range(region.shape[axis]):
            rects = list(rectangles(np.take(boundary, section, axis=axis)))
            if not rects:
                continue
            count += len(rects)*6
            if count > MESH_MAX_VERTICES:
                return {'unavailable_reason': 'سطح این ناحیه از سقف حافظهٔ نمایشگر بزرگ‌تر است.'}
            rects = np.asarray(rects)
            starts = np.broadcast_to(origin, (len(rects),3)).copy()
            starts[:,axis] += section
            starts[:,uv[0]] += rects[:,0]
            starts[:,uv[1]] += rects[:,2]
            sizes = np.ones((len(rects),3))
            sizes[:,uv[0]] = rects[:,1]-rects[:,0]
            sizes[:,uv[1]] = rects[:,3]-rects[:,2]
            points = starts[:,None,:]+template[None,:,:]*sizes[:,None,:]
            points = points @ affine[:3,:3].T + affine[:3,3] - center
            vertices = np.empty((len(rects),6,6), dtype='<f4')
            vertices[:,:,:3] = points[:,[0,1,2,0,2,3]]
            vertices[:,:,3:] = normal
            chunks.append(vertices.reshape(-1,6))
    vertices = np.concatenate(chunks) if chunks else np.empty((0,6), dtype='<f4')
    return dict(binary_png(vertices), count=count, method='merged_exact_voxel_faces')


def precompute_viewer(image, segmentation, geometry, labels, offset, hu_step):
    if not geometry['valid']:
        return None
    started = time.perf_counter()
    affine = np.array(geometry['affine_ras_mm'])
    boxes = find_objects(segmentation, max_label=255)
    bounds = world_bounds(image.shape, affine)
    center = (np.array(bounds['lower'])+bounds['upper'])/2
    rows = measurements(segmentation, geometry, labels, boxes)
    mpr = mpr_assets(image, segmentation, geometry, offset, hu_step, bounds)
    meshes, encoded_bytes = {}, 0
    for key, row in rows.items():
        if not row['count']:
            continue
        mesh = surface_asset(segmentation, int(key), boxes[int(key)-1], affine, center)
        encoded_bytes += len(mesh.get('png',''))
        if encoded_bytes > MESH_MAX_ENCODED_BYTES:
            mesh = {'unavailable_reason': 'سقف حجم سطح‌های همراه فایل تکمیل شده است.'}
        meshes[key] = mesh
    return dict(schema_version=1, producer='gpu-server/viewer-generator',
                processing='server-precomputed', measurements=rows, mpr=mpr,
                surfaces=dict(meshes=meshes, center=center.tolist(), bounds=bounds),
                generation_seconds=round(time.perf_counter()-started,3))
