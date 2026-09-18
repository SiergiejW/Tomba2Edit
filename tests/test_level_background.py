import unittest

import numpy as np

from functions import sky_gradient
from gui.level.level_viewer import clamped_background_pitch


class LevelBackgroundTests(unittest.TestCase):
    def test_pitch_stops_before_texture_edge_enters_view(self):
        self.assertEqual(clamped_background_pitch(89, 145), 50)
        self.assertEqual(clamped_background_pitch(-89, 145), -50)
        self.assertEqual(clamped_background_pitch(12, 145), 12)

    def test_synthetic_skies_extend_their_edge_colours(self):
        for overlay in ("A04.BIN", "A0E.BIN", "A0L.BIN"):
            with self.subTest(overlay=overlay):
                image = sky_gradient.image(overlay)
                self.assertFalse(np.any(np.all(image == 0, axis=2)))


if __name__ == "__main__":
    unittest.main()
