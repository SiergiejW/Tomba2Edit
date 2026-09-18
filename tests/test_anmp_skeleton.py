import unittest

import numpy as np

from gui.anmp.skeleton import pose_transforms


class ScaledSkeletonTests(unittest.TestCase):
    def test_sea_anemone_mode_inherits_joint_length_not_mesh_width(self):
        hierarchy = (("root", None), ("child", 0))
        pivots = np.array(((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
        rotations = ((0.0, 0.0, 0.0),) * 2
        scales = ((2.0, 1.0, 2.0),) * 2

        normal = pose_transforms(
            rotations, (0, 0, 0), hierarchy, pivots, scales=scales)
        anemone = pose_transforms(
            rotations, (0, 0, 0), hierarchy, pivots, scales=scales,
            inherit_scales=False)

        # Parent stretch still moves the child joint from x=1 to x=2.
        np.testing.assert_allclose(anemone[1][1], (2.0, 0.0, 0.0))
        # Generic scaled actors inherit 2x * 2x; the anemone's custom
        # renderer rebuilds rotation and leaves only the child's own 2x.
        self.assertAlmostEqual(normal[1][0][0, 0], 4.0)
        self.assertAlmostEqual(anemone[1][0][0, 0], 2.0)


if __name__ == "__main__":
    unittest.main()
