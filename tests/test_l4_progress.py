import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.l4_progress import Progress


class ProgressTests(unittest.TestCase):
    def test_arbitrary_chunk_boundaries_and_carriage_returns(self):
        text = "\r 1,048,576 50% 2.00MB/s 0:00:03 (xfr#5, ir-chk=4/10)\r"
        parser = Progress()
        for i in range(0, len(text), 3):
            parser.feed(text[i : i + 3])
        value = parser.finish()
        self.assertEqual(value["bytes"], 1048576)
        self.assertEqual(value["files_transferred"], 5)
        self.assertEqual(value["files_remaining"], 4)
        self.assertEqual(value["files_total"], 10)
        self.assertEqual(value["eta"], "0:00:03")
        self.assertFalse(value["listing_complete"])

    def test_completion_does_not_show_elapsed_time_as_eta(self):
        parser = Progress()
        parser.feed(" 8,192 100% 9.35kB/s 0:00:07 (xfr#2, to-chk=0/3)\n")
        self.assertIsNone(parser.last["eta"])
        self.assertTrue(parser.last["listing_complete"])

    def test_malformed_and_sensitive_text_is_never_a_metric(self):
        parser = Progress()
        parser.feed("private filename: token=fixture-only\n" + "x" * 10000)
        self.assertLessEqual(len(parser.buffer), 4096)
        self.assertIsNone(parser.finish())
        self.assertIsNone(parser.feed("0 999% 0.00MB/s 0:00:00\n"))
