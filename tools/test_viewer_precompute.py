"""Physical accuracy of server derivatives, independent of the browser worker."""
import base64
import io
import unittest

import numpy as np
from PIL import Image

from app.export.viewer_precompute import precompute_viewer, mpr_grid, MPR_MAX_AXIS, MPR_MAX_VOXELS


def decode(value):
    return np.array(Image.open(io.BytesIO(base64.b64decode(value))))


class ServerViewerTests(unittest.TestCase):
    def prepare(self, *, oblique=False, section_axis=2):
        self.shape = (9,8,7)
        self.image = np.arange(np.prod(self.shape), dtype=np.int16).reshape(self.shape)-1000
        self.labels = np.zeros(self.shape, np.uint8)
        self.labels[2:5,2:6,2:5] = 1
        self.labels[0,0,0] = 2
        self.a = np.diag([2.,3.,4.,1.])
        self.a[:3,3] = [31,-42,19]
        if oblique:
            t = .41
            self.a[:3,:3] = np.array([[1,.13,0],[0,np.cos(t),-np.sin(t)],[0,np.sin(t),np.cos(t)]]) @ self.a[:3,:3]
        self.geometry = dict(valid=True, affine_ras_mm=self.a.tolist(), spacing_mm=np.linalg.norm(self.a[:3,:3],axis=0).tolist(), section_axis=section_axis)
        return precompute_viewer(self.image,self.labels,self.geometry,{'1':'left','2':'partial','3':'empty'},-1000.,1.)

    def test_server_measurements_use_original_voxels_and_physical_boundaries(self):
        for oblique in (False,True):
            for axis in (0,1,2):
                p = self.prepare(oblique=oblique,section_axis=axis)
                r = p['measurements']['1']
                coords = np.column_stack(np.nonzero(self.labels == 1))
                world = coords @ self.a[:3,:3].T + self.a[:3,3]
                half = abs(self.a[:3,:3]).sum(axis=1)/2
                np.testing.assert_allclose(r['lower'],world.min(axis=0)-half)
                np.testing.assert_allclose(r['upper'],world.max(axis=0)+half)
                self.assertAlmostEqual(r['volume_ml'],len(coords)*abs(np.linalg.det(self.a[:3,:3]))/1000)
                area = np.linalg.norm(np.cross(self.a[:3,(axis+1)%3],self.a[:3,(axis+2)%3]))
                np.testing.assert_allclose(r['areas'],np.bincount(coords[:,axis],minlength=self.shape[axis])*area)
                self.assertEqual(r['status'],'measured')
                self.assertTrue(p['measurements']['2']['boundary'])
                self.assertIsNone(p['measurements']['3']['volume_ml'])

    def test_every_mpr_pixel_matches_independent_native_affine_mapping(self):
        p = self.prepare(oblique=True)
        inv = np.linalg.inv(self.a)
        for kind, spec in p['mpr']['planes'].items():
            u,v,f = spec['axes']
            for z, encoded in enumerate(spec['frames']):
                rgba = decode(encoded)
                yy,xx = np.indices(rgba.shape[:2])
                world = np.ones((*xx.shape,4))
                world[:,:,u] = spec['upper'][u]-(xx+.5)*spec['step_u']
                world[:,:,v] = spec['upper'][v]-(yy+.5)*spec['step_v']
                world[:,:,f] = spec['upper'][f]-(z+.5)*spec['step_f']
                coords = np.floor((world @ inv.T)[:,:,:3]+.5).astype(int)
                valid = np.all((coords >= 0) & (coords < self.shape),axis=2)
                np.testing.assert_array_equal(rgba[:,:,3]>0,valid)
                coords = tuple(coords[valid].T)
                np.testing.assert_array_equal(rgba[:,:,0][valid].astype(int)+256*rgba[:,:,1][valid].astype(int),self.image[coords]+1000)
                np.testing.assert_array_equal(rgba[:,:,2][valid],self.labels[coords])

    def test_compressed_merged_surface_preserves_bounds_volume_and_normals(self):
        p = self.prepare(oblique=True)
        asset = p['surfaces']['meshes']['1']
        raw = decode(asset['png']).reshape(-1)[:asset['byte_length']].tobytes()
        vertices = np.frombuffer(raw,dtype='<f4').reshape(-1,6)
        self.assertEqual(asset['count'],36,'a cuboid merges to exactly twelve triangles')
        points = vertices[:,:3].astype(float)+p['surfaces']['center']
        row = p['measurements']['1']
        np.testing.assert_allclose(points.min(axis=0),row['lower'],atol=1e-5)
        np.testing.assert_allclose(points.max(axis=0),row['upper'],atol=1e-5)
        triangles = points.reshape(-1,3,3)
        volume = abs(np.einsum('ij,ij->i',triangles[:,0],np.cross(triangles[:,1],triangles[:,2])).sum()/6)
        self.assertAlmostEqual(volume/1000,row['volume_ml'],places=6)
        np.testing.assert_allclose(np.linalg.norm(vertices[:,3:],axis=1),1,atol=1e-6)
        edges = np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0])
        np.testing.assert_allclose(edges/np.linalg.norm(edges,axis=1)[:,None],vertices[::3,3:],atol=1e-6)

    def test_server_grid_budget_and_unavailable_geometry(self):
        bounds={'lower':[0,0,0],'upper':[3000,3000,3000]}
        shape, spacing = mpr_grid({'spacing_mm':[.1,.1,.1]},bounds)
        self.assertLessEqual(int(np.prod(shape)),MPR_MAX_VOXELS)
        self.assertLessEqual(int(max(shape)),MPR_MAX_AXIS)
        np.testing.assert_allclose(shape*spacing,[3000,3000,3000])
        self.assertIsNone(precompute_viewer(None,None,{'valid':False},None,None,None))


if __name__=='__main__':unittest.main()
