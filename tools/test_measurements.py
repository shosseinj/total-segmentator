"""The single-pass labelmap measurement must preserve patient-space quantities."""
from types import SimpleNamespace
import unittest
import numpy as np
from app.export.measurements import measure,measure_labelmap


class MeasurementTests(unittest.TestCase):
    def test_rotated_native_grid_matches_per_mask_reference_in_both_orders(self):
        rng=np.random.default_rng(174)
        mask=rng.choice(np.array([0,1,2,85],dtype=np.uint8),size=(9,8,7))
        theta=.37;rotation=np.array([[np.cos(theta),-np.sin(theta),0],[np.sin(theta),np.cos(theta),0],[0,0,1]])
        a=np.eye(4);a[:3,:3]=rotation@np.diag([.7,1.3,2.2]);a[:3,3]=[42,-13,7]
        labels={1:'spleen',2:'kidney_right',85:'muscle',117:'empty'}
        for order in ('C','F'):
            array=np.array(mask,order=order);volume=SimpleNamespace(array=array,affine_lps=a)
            result=measure_labelmap(array,labels,volume)
            self.assertEqual(result['review_status'],'unreviewed')
            for row,(number,name) in zip(result['structures'],labels.items()):
                expected=measure({name:array==number},volume)['structures'][0]
                self.assertEqual(row['segment_number'],number)
                for key in ('label','measurement_status','volume_ml','method','touches_scan_boundary','warnings'):
                    self.assertEqual(row[key],expected[key])
                if row['patient_axis_extents_mm'] is None:self.assertIsNone(expected['patient_axis_extents_mm'])
                else:np.testing.assert_allclose(list(row['patient_axis_extents_mm'].values()),list(expected['patient_axis_extents_mm'].values()),rtol=1e-12,atol=1e-12)

    def test_volume_and_extents_include_voxel_edges(self):
        mask=np.zeros((8,9,10),np.uint8);mask[2:5,2:6,2:7]=5
        volume=SimpleNamespace(array=mask,affine_lps=np.diag([2.,3.,4.,1.]))
        row=measure_labelmap(mask,{5:'liver'},volume)['structures'][0]
        self.assertAlmostEqual(row['volume_ml'],3*4*5*24/1000)
        self.assertEqual(row['patient_axis_extents_mm'],{'left_right':6.,'anterior_posterior':12.,'superior_inferior':20.})
        self.assertFalse(row['touches_scan_boundary'])
        self.assertEqual(row['measurement_status'],'measured')
        with self.assertRaisesRegex(ValueError,'MASK_GEOMETRY_MISMATCH'):
            measure_labelmap(mask[:,:,:2],{5:'liver'},volume)


if __name__=='__main__':unittest.main()
