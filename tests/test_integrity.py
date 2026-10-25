"""Aggregate real rclone results without touching user disks or services."""

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS, validate
from steward.integrity import IntegrityFailure, IntegrityRun
from steward.storage import Copier


class IntegrityTests(unittest.TestCase):
    def test_global_unique_file_budget_spans_overlapping_checks(self):
        run = IntegrityRun(3)
        a = run.plan("/media/source", "/media/target/mirror/source", "size")
        b = run.plan("/media/source/l2", "/media/target/mirror/source/l2", "l2_sha256")
        c = run.plan("/media/other", "/media/target/mirror/other", "sha256")
        run.begin(a)
        run.line(a, "* l2/one.tar\n")
        run.line(a, "* l2/one.tar\n")
        run.end(a)
        run.begin(b)
        run.line(b, "* one.tar\n")
        run.end(b)
        self.assertEqual(run.report()["error_count"], 1)
        self.assertEqual([a["errors"], b["errors"]], [1, 1])
        run.begin(c)
        run.line(c, "+ two.bin\n")
        with self.assertRaises(IntegrityFailure):
            run.line(c, "! three.bin\n")
        report = run.report()
        self.assertEqual(report["error_count"], 3)
        self.assertTrue(report["limit_reached"])
        self.assertFalse(report["complete"])
        self.assertEqual(c["status"], "stopped")

    def test_success_and_bounded_error_samples(self):
        run = IntegrityRun(100)
        a = run.plan("/media/source", "/media/target/source", "sha256")
        run.begin(a)
        run.line(a, "= good.flac\n")
        for i in range(12):
            run.line(a, f"* bad-{i}.flac\n")
        run.end(a)
        with self.assertRaises(IntegrityFailure) as caught:
            run.finish()
        report = caught.exception.report
        self.assertTrue(report["complete"])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_count"], 12)
        self.assertEqual(len(report["samples"]), 8)
        self.assertEqual(a["checked"], 13)

    def test_unchecked_area_never_passes(self):
        run = IntegrityRun(100)
        run.plan("/media/source", "/media/target/source", "sha256")
        with self.assertRaises(IntegrityFailure):
            run.finish()
        self.assertEqual(run.report()["status"], "aborted")

    def test_invalid_reports_cannot_be_counted_as_success(self):
        run = IntegrityRun(100)
        a = run.plan("/media/source", "/media/target/source", "sha256")
        for text in ("unexpected", "* /etc/passwd", "* ../outside", "* "):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                run.line(a, text)

    def test_error_limit_validation(self):
        self.assertEqual(validate({})["integrity_error_limit"], 1000)
        for value in (0, -1, True, 1.5, "1000", 1000001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate({"integrity_error_limit": value})


class RealRcloneIntegrityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.source = root / "NAS"
        self.other = root / "photos"
        self.last = root / "files"
        self.target = root / "target"
        self.cfg = dict(
            DEFAULTS,
            source_mount=str(self.source),
            destination_mount=str(self.target),
            additional_sources=[str(self.other), str(self.last)],
        )
        self.disks = AsyncMock()
        self.reports = []
        for source in (self.source, self.other, self.last):
            target = self.target / "mirror" / source.name
            source.mkdir(parents=True)
            target.mkdir(parents=True)
            (source / "good.bin").write_bytes(b"same bytes")
            (target / "good.bin").write_bytes(b"same bytes")

    def copier(self, limit=1000):
        return Copier(dict(self.cfg, integrity_error_limit=limit), self.disks, on_report=self.reports.append)

    def differ(self, source, name):
        (source / name).write_bytes(b"source")
        (self.target / "mirror" / source.name / name).write_bytes(b"target")

    async def test_hash_errors_continue_to_all_remaining_sources(self):
        self.differ(self.source, "header.flac")
        self.differ(self.other, "photo.jpg")
        copier = self.copier()
        await copier.checksum()
        with self.assertRaises(IntegrityFailure) as caught:
            copier.finish_checks()
        report = caught.exception.report
        self.assertEqual(report["error_count"], 2)
        self.assertTrue(report["complete"])
        self.assertEqual([a["status"] for a in report["areas"]], ["failed", "failed", "success"])
        self.assertGreater(report["areas"][-1]["checked"], 0)
        self.assertEqual(self.reports[-1], report)

    async def test_combined_missing_files_and_empty_directory_are_counted_individually(self):
        for path in (self.target / "mirror" / self.source.name).iterdir():
            path.unlink()
        (self.source / "second.bin").write_bytes(b"bytes")
        copier = self.copier()
        await copier.checksum()
        with self.assertRaises(IntegrityFailure) as caught:
            copier.finish_checks()
        self.assertEqual(caught.exception.report["error_count"], 2)
        self.assertEqual(
            [a["status"] for a in caught.exception.report["areas"]], ["failed", "success", "success"]
        )

    async def test_global_limit_stops_second_source_and_leaves_third_unchecked(self):
        self.differ(self.source, "first.bin")
        self.differ(self.other, "second.bin")
        copier = self.copier(limit=2)
        with self.assertRaises(IntegrityFailure):
            await asyncio.wait_for(copier.checksum(), 10)
        report = copier.integrity.report()
        self.assertTrue(report["limit_reached"])
        self.assertEqual(report["error_count"], 2)
        self.assertEqual([a["status"] for a in report["areas"]], ["failed", "stopped", "pending"])

    async def test_unavailable_area_continues_and_marks_overview_failed(self):
        (self.target / "mirror" / self.source.name / "good.bin").unlink()
        (self.target / "mirror" / self.source.name).rmdir()
        copier = self.copier()
        await copier.checksum()
        with self.assertRaises(IntegrityFailure) as caught:
            copier.finish_checks()
        self.assertEqual(caught.exception.report["error_count"], 1)
        self.assertEqual(
            [a["status"] for a in caught.exception.report["areas"]], ["failed", "success", "success"]
        )

    async def test_io_error_continues_but_identity_and_symlink_errors_remain_fatal(self):
        copier = self.copier()
        from steward.storage import count_files

        def scan(path):
            if path == self.source:
                raise PermissionError("fixture")
            return count_files(path)

        with patch("steward.storage.count_files", side_effect=scan):
            await copier.checksum()
        self.assertEqual(
            [a["status"] for a in copier.integrity.report()["areas"]], ["failed", "success", "success"]
        )
        copier = self.copier()
        self.disks.validate_source.side_effect = ValueError("UUID mismatch")
        with self.assertRaisesRegex(ValueError, "UUID mismatch"):
            await copier.checksum()
        self.disks.validate_source.side_effect = None
        (self.source / "link").symlink_to(self.last / "good.bin")
        with self.assertRaisesRegex(ValueError, "Symbolic links"):
            await self.copier().checksum()

    async def test_empty_source_is_reported_without_aborting_other_sources(self):
        (self.source / "good.bin").unlink()
        copier = self.copier()
        await copier.checksum()
        with self.assertRaises(IntegrityFailure) as caught:
            copier.finish_checks()
        self.assertEqual(caught.exception.report["error_count"], 1)
        self.assertEqual(
            [a["status"] for a in caught.exception.report["areas"]], ["failed", "success", "success"]
        )

    async def test_backup_planning_keeps_all_size_l2_and_full_checks(self):
        copier = self.copier()
        copier.plan_checks(include_copy=True, include_full=True)
        areas = copier.integrity.report()["areas"]
        self.assertEqual(len(areas), 7)
        self.assertEqual(
            [a["source"] for a in areas if a["kind"] == "sha256"],
            [str(self.source), str(self.other), str(self.last)],
        )
        self.assertEqual(len([a for a in areas if a["kind"] == "size"]), 3)
        self.assertEqual(len([a for a in areas if a["kind"] == "l2_sha256"]), 1)

    async def test_selected_source_ignores_other_corrupt_areas(self):
        self.differ(self.source, "excluded.flac")
        self.differ(self.last, "excluded.bin")
        copier = self.copier()
        copier.plan_checks(include_copy=False, source=str(self.other))
        await copier.checksum(source=str(self.other))
        report = copier.finish_checks()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(report["areas"]), 1)
        self.assertEqual(report["areas"][0]["source"], str(self.other))
        self.assertEqual(report["areas"][0]["destination"], str(self.target / "mirror" / self.other.name))
        self.assertEqual(report["areas"][0]["checked"], 1)

    async def test_selected_source_failure_has_only_that_area(self):
        self.differ(self.last, "bad.bin")
        copier = self.copier()
        await copier.checksum(source=str(self.last))
        with self.assertRaises(IntegrityFailure) as caught:
            copier.finish_checks()
        self.assertEqual(caught.exception.report["error_count"], 1)
        self.assertEqual(len(caught.exception.report["areas"]), 1)
        self.assertEqual(caught.exception.report["areas"][0]["source"], str(self.last))

    async def test_test_scope_maps_only_the_separate_test_tree(self):
        copier = Copier(dict(self.cfg, execution_mode="test"), self.disks)
        pairs = copier.verification_pairs(test=True, source=str(self.source))
        self.assertEqual(pairs, copier.pairs(test=True))
        self.assertTrue(str(pairs[0][0]).startswith(str(self.source / "backup-steward-test")))
        with self.assertRaises(ValueError):
            copier.verification_pairs(test=True, source=str(self.other))

    async def test_all_success_finishes_with_no_errors(self):
        copier = self.copier()
        await copier.checksum()
        report = copier.finish_checks()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["error_count"], 0)
        self.assertTrue(report["complete"])
