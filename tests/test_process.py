import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward import telemetry
from steward.storage import command


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / "finish"

    def fake(self, records, code=1, wait=False):
        executable = self.root / "rclone"
        script = f"""#!{sys.executable}
import json,sys,time
from pathlib import Path
assert '--use-json-log' in sys.argv
assert sys.argv[sys.argv.index('--stats')+1] == '60s'
for record in {records!r}:
    print(json.dumps(record),file=sys.stderr,flush=True)
while {wait!r} and not Path({str(self.marker)!r}).exists():
    time.sleep(0.01)
sys.exit({code})
"""
        executable.write_text(script, encoding="utf-8")
        executable.chmod(0o700)
        return patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ.get("PATH", "")})

    async def test_errors_are_logged_before_the_process_finishes(self):
        seen = asyncio.Event()
        logs = []

        def event(message, **kwargs):
            logs.append(message)
            if "sha256 differ" in message:
                seen.set()

        with (
            self.fake([{"level": "error", "msg": "sha256 differ", "object": "music/file.flac"}], wait=True),
            patch("steward.telemetry.event", side_effect=event),
        ):
            task = asyncio.create_task(command("rclone", "check", "source", "target"))
            try:
                await asyncio.wait_for(seen.wait(), 5)
                self.assertFalse(task.done())
                self.marker.touch()
                with self.assertRaisesRegex(RuntimeError, "sha256 differ.*music/file.flac"):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(logs), 1)

    async def test_credentials_urls_and_arbitrary_json_fields_are_not_logged(self):
        telemetry.protect("fixture-private-key")
        record = {
            "level": "error",
            "msg": "fixture-private-key https://user:URLPASS@example.test/a?token=URLTOKEN Bearer BEARERTOKEN password=PASSWORD secret=SECRET token=TOKEN",
            "object": "file.bin",
            "headers": {"Authorization": "HIDDENHEADER"},
        }
        logs = []
        with (
            self.fake([record]),
            patch("steward.telemetry.event", side_effect=lambda message, **kwargs: logs.append(message)),
        ):
            with self.assertRaises(RuntimeError) as caught:
                await command("rclone", "check", "source", "target")
        output = " ".join(logs) + str(caught.exception)
        for secret in (
            "fixture-private-key",
            "URLPASS",
            "URLTOKEN",
            "BEARERTOKEN",
            "PASSWORD",
            "SECRET",
            "TOKEN",
            "HIDDENHEADER",
        ):
            self.assertNotIn(secret, output)
        self.assertIn("file.bin", output)
        self.assertIn("[redacted]", output)

    async def test_progress_is_concise_and_success_does_not_log_each_file(self):
        records = [
            {
                "level": "notice",
                "stats": {"checks": 23, "bytes": 64, "elapsedTime": 2.25},
                "msg": "arbitrary message not relayed",
            },
            {"level": "info", "msg": "Copied", "object": "private-file-name"},
        ]
        logs = []
        with (
            self.fake(records, code=0),
            patch("steward.telemetry.event", side_effect=lambda message, **kwargs: logs.append(message)),
        ):
            self.assertEqual(await command("rclone", "check", "source", "target"), "")
        self.assertEqual(len(logs), 1)
        self.assertIn("verificados=23", logs[0])
        self.assertNotIn("private-file-name", logs[0])
        self.assertNotIn("arbitrary", logs[0])

    async def test_error_details_are_bounded(self):
        records = [{"level": "error", "msg": "sha256 differ", "object": f"file-{i}"} for i in range(12)]
        logs = []
        with (
            self.fake(records),
            patch("steward.telemetry.event", side_effect=lambda message, **kwargs: logs.append(message)),
        ):
            with self.assertRaises(RuntimeError):
                await command("rclone", "check", "source", "target")
        self.assertEqual(len(logs), 9)
        self.assertIn("mais 4", logs[-1])

    async def test_integrity_limit_terminates_live_process_without_waiting_for_completion(self):
        from steward.integrity import IntegrityFailure, IntegrityRun

        executable = self.root / "rclone"
        executable.write_text(
            f"""#!{sys.executable}
import sys,time
print('* bad.bin', flush=True)
time.sleep(30)
""",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        run = IntegrityRun(1)
        area = run.plan("/media/source", "/media/target/source", "sha256")
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ.get("PATH", "")}):
            with self.assertRaises(IntegrityFailure):
                await asyncio.wait_for(
                    command(
                        "rclone", "check", "source", "target", integrity=lambda line: run.line(area, line)
                    ),
                    5,
                )
        self.assertTrue(run.report()["limit_reached"])
        self.assertEqual(run.report()["error_count"], 1)

    async def test_timeout_stops_the_process(self):
        with self.fake([], wait=True):
            with self.assertRaises(TimeoutError):
                await command("rclone", "check", "source", "target", timeout=0.1)
