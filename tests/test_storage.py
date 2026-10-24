import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS
from steward.storage import Copier, confined, count_files, inspect_archive, prune_l2


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def archive(self, day, slug=None):
        slug = slug or f"{day:08x}"
        path = self.root / f"steward_l2_202610{day:02d}T040000Z_{slug}.tar"
        data = {
            "slug": slug,
            "date": f"2026-10-{day:02d}T04:00:00+00:00",
            "name": "HA Backup Steward L2 example",
            "protected": True,
        }
        raw = json.dumps(data).encode()
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("./backup.json")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        return path

    def test_three_newest_and_unmanaged_files(self):
        paths = [self.archive(i) for i in range(1, 8)]
        other = self.root / "manual.tar"
        other.write_text("keep me")
        self.assertEqual(len(prune_l2(self.root, 3, "00000007")), 4)
        self.assertEqual([p.exists() for p in paths], [False] * 4 + [True] * 3)
        self.assertTrue(other.exists())

    def test_legacy_archives_can_be_adopted(self):
        old = self.archive(1)
        old = old.rename(self.root / "L2_NASRUI_apps_semanal_2026_10_01_04_00_00.tar")
        self.archive(2)
        prune_l2(self.root, 1, "00000002")
        self.assertFalse(old.exists())

    def test_corrupt_archive_blocks_every_deletion(self):
        paths = [self.archive(i) for i in range(1, 5)]
        paths[0].write_bytes(b"invalid")
        with self.assertRaises(tarfile.TarError):
            prune_l2(self.root, 3, "00000004")
        self.assertTrue(all(p.exists() for p in paths))

    def test_stale_success_and_invalid_retention_block_deletion(self):
        paths = [self.archive(i) for i in range(1, 5)]
        for keep, slug in [(3, "ffffffff"), (3, "00000001"), (0, "00000004")]:
            with self.subTest(keep=keep, slug=slug), self.assertRaises(ValueError):
                prune_l2(self.root, keep, slug)
        self.assertTrue(all(p.exists() for p in paths))

    def test_symlinks_and_hardlinks_are_rejected(self):
        path = self.archive(1)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaises(ValueError):
            confined(link, self.root)
        with self.assertRaises(ValueError):
            count_files(self.root)
        link.unlink()
        os.link(path, link)
        with self.assertRaises(ValueError):
            inspect_archive(path)

    def test_path_escape_is_rejected(self):
        with self.assertRaises(ValueError):
            confined(self.root / ".." / "outside", self.root)


class CopyTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_mode_uses_copy_and_no_retention_or_deletes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, destination = root / "source", root / "destination"
            (source / "backup-steward-test/level-2").mkdir(parents=True)
            (source / "backup-steward-test/level-2/archive.tar").write_text("fixture")
            destination.mkdir()
            cfg = dict(DEFAULTS, source_mount=str(source), destination_mount=str(destination))
            disks = AsyncMock()
            copier = Copier(cfg, disks)
            with patch("steward.storage.command", new_callable=AsyncMock) as command:
                await copier.copy("20261024T040000Z", test=True)
            arguments = [str(arg) for call in command.call_args_list for arg in call.args]
            self.assertIn("copy", arguments)
            self.assertNotIn("sync", arguments)
            self.assertNotIn("--backup-dir", arguments)
            self.assertNotIn("--delete-after", arguments)


if __name__ == "__main__":
    unittest.main()
