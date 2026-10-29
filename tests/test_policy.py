import copy
from datetime import datetime, timezone
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS, public_config, validate
from steward.schedule import checksum_due, due, next_run


class PolicyTests(unittest.TestCase):
    def test_safe_defaults(self):
        cfg = validate({})
        self.assertEqual(cfg["execution_mode"], "observe")
        self.assertFalse(cfg["schedule_enabled"])
        self.assertEqual(cfg["l2_keep"], 3)
        self.assertEqual(cfg["history_days"], 30)

    def test_execution_requires_secret_without_retired_acknowledgement(self):
        base = {
            "execution_mode": "test",
            "source_uuid": "11111111-1111-4111-8111-111111111111",
            "destination_uuid": "22222222-2222-4222-8222-222222222222",
            "power_entity": "switch.disk",
        }
        with self.assertRaises(ValueError):
            validate(base)
        validate(dict(base, encryption_password="not-a-real-key"))
        for old_value in (True, False):
            cfg = validate(
                dict(base, encryption_password="not-a-real-key", legacy_scheduler_disabled=old_value)
            )
            self.assertNotIn("legacy_scheduler_disabled", cfg)
            self.assertNotIn("legacy_scheduler_disabled", public_config(cfg))

    def test_secrets_are_never_in_public_config(self):
        cfg = validate({"encryption_password": "private", "api_token": "private"})
        self.assertNotIn("private", str(public_config(cfg)))

    def test_bad_paths_and_unsafe_limits_are_rejected(self):
        invalid = [
            {"destination_mount": "/media/../etc"},
            {"source_mount": "/mnt/data"},
            {"source_mount": "/media/NASRUI/" + "nested"},
            {"additional_sources": ["/media/HAL3"]},
            {"l2_directory": "../../etc"},
            {"test_directory": "/media/HAL3"},
            {"l2_keep": 0},
            {"l2_keep": True},
            {"max_missing_fraction": 1},
            {"max_delete_files": 0},
            {"history_days": 0},
            {"power_entity": "switch.disk; reboot"},
            {"source_uuid": "invalid"},
            {"schedule_time": "24:00"},
            {"schedule_time": "4:00"},
            {"schedule_enabled": True},
            {"unknown": True},
            {"schedule_enabled": "yes"},
        ]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate(options)

    def test_config_does_not_mutate_defaults(self):
        cfg = validate({})
        cfg["additional_sources"].clear()
        self.assertTrue(DEFAULTS["additional_sources"])


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["schedule_enabled"] = True

    def instant(self, text):
        return datetime.fromisoformat(text).astimezone(timezone.utc)

    def test_daily_time_and_same_day_deduplication(self):
        self.assertFalse(due(self.instant("2026-10-24T03:59:00+02:00"), self.cfg, {}))
        self.assertTrue(due(self.instant("2026-10-24T04:00:00+02:00"), self.cfg, {}))
        self.assertFalse(
            due(self.instant("2026-10-24T20:00:00+02:00"), self.cfg, {"last_schedule_date": "2026-10-24"})
        )

    def test_enabling_after_time_waits_for_next_occurrence_and_survives_restart(self):
        enabled = self.instant("2026-10-29T20:18:24+01:00")
        state = {"schedule_not_before": next_run(enabled, self.cfg)}
        self.assertFalse(due(enabled, self.cfg, state))
        self.assertFalse(due(self.instant("2026-10-30T03:59:59+01:00"), self.cfg, dict(state)))
        self.assertTrue(due(self.instant("2026-10-30T04:00:00+01:00"), self.cfg, dict(state)))
        state["last_schedule_date"] = "2026-10-30"
        self.assertFalse(due(self.instant("2026-10-30T12:00:00+01:00"), self.cfg, state))

    def test_enabling_before_time_still_runs_today(self):
        now = self.instant("2026-10-29T03:00:00+01:00")
        state = {"schedule_not_before": next_run(now, self.cfg)}
        self.assertTrue(due(self.instant("2026-10-29T04:00:00+01:00"), self.cfg, state))

    def test_dst_clock_change_keeps_four_am_local(self):
        spring = next_run(self.instant("2026-03-28T23:00:00+01:00"), self.cfg)
        autumn = next_run(self.instant("2026-10-24T23:00:00+02:00"), self.cfg)
        self.assertEqual(spring, "2026-03-29T02:00:00+00:00")
        self.assertEqual(autumn, "2026-10-25T03:00:00+00:00")

    def test_monthly_checksum_and_catchup_after_failure(self):
        self.assertTrue(checksum_due(self.instant("2026-11-01T04:00:00+01:00"), self.cfg, None))
        self.assertTrue(checksum_due(self.instant("2026-11-02T04:00:00+01:00"), self.cfg, None))
        self.assertFalse(
            checksum_due(self.instant("2026-11-02T04:00:00+01:00"), self.cfg, "2026-11-01T10:00:00+00:00")
        )
        self.assertFalse(
            checksum_due(self.instant("2026-12-01T04:00:00+01:00"), self.cfg, "2026-11-01T10:00:00+00:00")
        )
        self.assertTrue(
            checksum_due(self.instant("2026-12-06T04:00:00+01:00"), self.cfg, "2026-11-01T10:00:00+00:00")
        )

    def test_weekly_disabled_and_year_boundary(self):
        self.cfg["checksum_frequency"] = "weekly"
        now = self.instant("2027-01-03T04:00:00+01:00")
        self.assertTrue(checksum_due(now, self.cfg, "2026-12-27T10:00:00+00:00"))
        self.cfg["checksum_frequency"] = "disabled"
        self.assertFalse(checksum_due(now, self.cfg, None))


if __name__ == "__main__":
    unittest.main()
