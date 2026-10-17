import numpy as np

def measure(masks,volume):
    a=volume.affine_lps;voxel_ml=abs(np.linalg.det(a[:3,:3]))/1000
    half=np.abs(a[:3,:3]).sum(axis=1)/2;structures=[]
    for number,label in enumerate(sorted(masks),1):
        mask=np.asarray(masks[label],dtype=bool)
        if mask.shape!=volume.array.shape:raise ValueError('MASK_GEOMETRY_MISMATCH')
        count=int(np.count_nonzero(mask));boundary=False;extents=None
        if count:
            boundary=any(np.any(np.take(mask,[0,mask.shape[axis]-1],axis=axis)) for axis in range(3))
            lower=np.full(3,np.inf);upper=np.full(3,-np.inf)
            # Bound memory per slice while retaining true rotated patient-axis bounds.
            for z in range(mask.shape[2]):
                x,y=np.nonzero(mask[:,:,z])
                if not len(x):continue
                centres=np.column_stack([x,y,np.full(len(x),z)])@a[:3,:3].T+a[:3,3]
                lower=np.minimum(lower,centres.min(axis=0)-half);upper=np.maximum(upper,centres.max(axis=0)+half)
            extents=dict(zip(['left_right','anterior_posterior','superior_inferior'],(upper-lower).tolist()))
        structures.append({'segment_number':number,'label':label,'measurement_status':'empty' if not count else 'partial' if boundary else 'measured','volume_ml':count*voxel_ml if count else None,'patient_axis_extents_mm':extents,'method':'native_grid_voxel_count_and_patient_lps_bounds','touches_scan_boundary':boundary,'warnings':['EMPTY_SEGMENT'] if not count else ['PARTIAL_COVERAGE'] if boundary else []})
    return {'schema_version':'1.0','review_status':'unreviewed','structures':structures}


def measure_labelmap(labelmap,labels,volume):
    if labelmap.shape!=volume.array.shape:raise ValueError('MASK_GEOMETRY_MISMATCH')
    # Visit foreground voxels once per slice rather than scanning the entire
    # volume for every class. Scalar affine terms also avoid thousands of tiny
    # BLAS matrix products and work identically with C/F-order NIfTI arrays.
    a=volume.affine_lps;voxel_ml=abs(np.linalg.det(a[:3,:3]))/1000
    half=np.abs(a[:3,:3]).sum(axis=1)/2
    capacity=max(256,max(map(int,labels),default=0)+1)
    counts=np.zeros(capacity,dtype=np.int64)
    lower=np.full((capacity,3),np.inf);upper=np.full((capacity,3),-np.inf)
    boundary=set()
    for axis in range(3):
        for face in (0,-1):
            selector=[slice(None)]*3;selector[axis]=face
            boundary.update(int(n) for n in np.unique(labelmap[tuple(selector)]))
    for z in range(labelmap.shape[2]):
        plane=labelmap[:,:,z];x,y=np.nonzero(plane)
        if not len(x):continue
        ids=plane[x,y]
        counts+=np.bincount(ids,minlength=capacity)
        for axis in range(3):
            coordinates=x*a[axis,0]+y*a[axis,1]+z*a[axis,2]+a[axis,3]
            np.minimum.at(lower[:,axis],ids,coordinates)
            np.maximum.at(upper[:,axis],ids,coordinates)
    structures=[]
    for number,label in labels.items():
        number=int(number);count=int(counts[number]);touches=bool(count and number in boundary)
        extents=dict(zip(['left_right','anterior_posterior','superior_inferior'],(upper[number]-lower[number]+2*half).tolist())) if count else None
        structures.append({'segment_number':number,'label':label,'measurement_status':'empty' if not count else 'partial' if touches else 'measured',
            'volume_ml':count*voxel_ml if count else None,'patient_axis_extents_mm':extents,
            'method':'native_grid_voxel_count_and_patient_lps_bounds','touches_scan_boundary':touches,
            'warnings':['EMPTY_SEGMENT'] if not count else ['PARTIAL_COVERAGE'] if touches else []})
    return {'schema_version':'1.0','review_status':'unreviewed','structures':structures}
