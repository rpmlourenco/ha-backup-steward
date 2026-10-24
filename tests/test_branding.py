"""Packaging contract for local HA branding and the repository icon."""

from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BrandingTests(unittest.TestCase):
    def test_app_repository_and_integration_share_the_same_icon(self):
        original = (ROOT / "backup_steward/icon.png").read_bytes()
        brand = ROOT / "custom_components/backup_steward/brand"
        self.assertEqual((ROOT / "icon.png").read_bytes(), original)
        self.assertEqual((brand / "icon.png").read_bytes(), original)
        self.assertEqual(struct.unpack(">II", original[16:24]), (256, 256))
        high_resolution = (brand / "icon@2x.png").read_bytes()
        self.assertEqual(high_resolution[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", high_resolution[16:24]), (512, 512))


if __name__ == "__main__":
    unittest.main()
