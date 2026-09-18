"""Small synthetic sequences exercise branches without requiring a ROM."""
import struct
import unittest

from gui.anmp.anmp_parser import Frame
from gui.anmp.sequences import (
    Clip, SequenceError, Step, candidate_banks, read_clip, scale_states)


class SequenceTests(unittest.TestCase):
    base = 0x80100000

    def entry(self, pose, flags):
        return struct.pack("<4H", pose, 0, 0, flags)

    def test_inline_hold_tween_and_terminal(self):
        data = (self.entry(8, 2) + self.entry(3, 0x2004)
                + self.entry(19, 0x8003))
        c = read_clip(data, self.base, self.base, {3, 8, 19})
        self.assertEqual([s.pose for s in c.steps], [8, 3, 19])
        self.assertEqual(c.starts, [0, 2, 6])
        self.assertEqual(c.sample(1), (0, 1, 0.0))
        self.assertEqual(c.sample(4), (1, 2, 0.5))
        self.assertEqual(c.sample(8), (2, None, 0.0))
        self.assertIsNone(c.advance(8))

    def test_intro_followed_by_indirect_loop(self):
        data = (self.entry(2, 3) + self.entry(7, 0x2002)
                + self.entry(1, 0xE004) + struct.pack("<I", self.base + 8))
        c = read_clip(data, self.base, self.base, {1, 2, 7})
        self.assertEqual(c.loop_start, 1)
        self.assertEqual(c.advance(8), 3)
        self.assertEqual(c.sample(7), (2, 1, 0.5))

    def test_zero_duration_terminal_is_a_static_pose(self):
        c = read_clip(self.entry(5, 0x8000), self.base, self.base, {5})
        self.assertEqual(c.duration, 1)
        self.assertEqual(c.sample(0), (0, None, 0.0))
        self.assertIsNone(c.advance(0))

    def test_invalid_pose_link_and_unterminated_data_are_rejected(self):
        for data in (self.entry(5, 0x8001), self.entry(1, 1),
                     self.entry(1, 0x4001), self.entry(1, 0),
                     self.entry(1, 0x4001) + struct.pack("<I", self.base - 4)):
            with self.subTest(data=data):
                with self.assertRaises(SequenceError):
                    read_clip(data, self.base, self.base, {1})

    def test_sparse_pose_ids_are_not_list_indices(self):
        c = read_clip(self.entry(40, 0x8001), self.base, self.base, {2, 40})
        self.assertEqual(c.steps[0].pose, 40)
        with self.assertRaises(SequenceError):
            read_clip(self.entry(1, 0x8001), self.base, self.base, {2, 40})

    def test_candidate_bank_requires_complete_compatible_sequences(self):
        data = (struct.pack("<3I", self.base + 12, self.base + 20, self.base + 28)
                + self.entry(0, 0x8001) + self.entry(2, 0x8001)
                + self.entry(5, 0x8001))
        banks = candidate_banks(data, self.base, {0, 2, 5})
        self.assertEqual(len(banks), 1)
        self.assertEqual([c.steps[0].pose for c in banks[0].clips], [0, 2, 5])
        self.assertFalse(banks[0].verified)
        self.assertEqual(candidate_banks(data, self.base, {0, 2}), [])

    def test_single_clip_bank_is_available_for_tiny_pose_archives(self):
        data = (struct.pack("<I", self.base + 4)
                + self.entry(0, 1) + self.entry(1, 1)
                + self.entry(2, 1) + self.entry(3, 0x8001))
        self.assertEqual(candidate_banks(data, self.base, {0, 1, 2, 3}), [])
        banks = candidate_banks(
            data, self.base, {0, 1, 2, 3}, min_clips=1)
        self.assertEqual(len(banks), 1)
        self.assertEqual([step.pose for step in banks[0].clips[0].steps],
                         [0, 1, 2, 3])

    def test_scale_state_persists_and_tween_target_applies_immediately(self):
        unit = Frame(0, 0, 1, [(0, 0, 0)])
        double = Frame(1, 0, 0x41, [(0, 0, 0)],
                       scales=[(8192, 4096, 8192)])
        ordinary = Frame(2, 0, 1, [(0, 0, 0)])
        clip = Clip(0, [
            Step(0, 0, 0, 0, 0x2004),  # tween reads pose 1 now
            Step(8, 1, 0, 0, 2),
            Step(16, 2, 0, 0, 0x8002),
        ])
        states = scale_states(clip, {0: unit, 1: double, 2: ordinary})
        self.assertEqual(states[0], ((2.0, 1.0, 2.0),))
        self.assertEqual(states[1], ((2.0, 1.0, 2.0),))
        self.assertEqual(states[2], ((2.0, 1.0, 2.0),))


if __name__ == "__main__":
    unittest.main()
