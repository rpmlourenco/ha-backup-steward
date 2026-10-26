import asyncio
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import validate
from steward.state import State
from steward.supervisor import APIError, Supervisor
from steward import VERSION, telemetry


class LoggingTests(unittest.TestCase):
    def test_console_formatter_redacts_credentials_and_identifies_run(self):
        telemetry.protect("fixture-encryption-key", "fixture-supervisor-token", "fixture-pairing-key")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(telemetry.SafeFormatter("Europe/Madrid"))
        logger = logging.getLogger("steward")
        old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
        logger.handlers, logger.propagate = [handler], False
        logger.setLevel(logging.INFO)
        try:
            telemetry.event(
                "Etapa: fixture-encryption-key fixture-supervisor-token fixture-pairing-key", "run-fixture"
            )
        finally:
            logger.handlers, logger.propagate = old_handlers, old_propagate
            logger.setLevel(old_level)
        result = stream.getvalue()
        self.assertIn("run-fixture", result)
        self.assertIn("INFO", result)
        self.assertEqual(result.count("[redacted]"), 3)
        self.assertNotIn("fixture-encryption-key", result)

    def test_startup_summary_distinguishes_ready_from_schedule_enabled(self):
        with self.assertLogs("steward", level="INFO") as logs:
            telemetry.announce_policy(validate({}))
        output = "\n".join(logs.output)
        self.assertIn("Agenda disponível e pronta: DESATIVADA", output)
        self.assertIn("04:00", output)
        self.assertIn("Europe/Madrid", output)
        with self.assertLogs("steward", level="INFO") as logs:
            telemetry.announce_policy(dict(validate({}), schedule_enabled=True))
        self.assertIn("Agenda disponível e pronta: ATIVA", "\n".join(logs.output))

    def test_state_steps_are_timestamped_logged_and_no_false_recovery_while_busy(self):
        with tempfile.TemporaryDirectory() as temp:
            state = State(temp)
            state.update(state="running", needs_cleanup=True, run_id="fixture-run")
            with self.assertLogs("steward", level="INFO") as logs:
                state.event("l2_create", "A criar L2")
            self.assertIn("A criar L2", logs.output[0])
            self.assertIn("time", state.data["events"][-1])
            self.assertFalse(state.public(busy=True)["recovery_required"])
            state.update(state="failed")
            self.assertTrue(state.public(busy=False)["recovery_required"])

    def test_app_declares_role_required_by_background_job_polling(self):
        root = Path(__file__).resolve().parents[1]
        cfg = json.loads((root / "backup_steward/config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["hassio_role"], "manager")


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_preserves_manual_destination_for_recovery(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            state = State(temp)
            state.update(state="destination_mounted", needs_cleanup=True, destination_manual=True)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-B",
                "-m",
                "steward",
                cwd=root / "backup_steward",
                env=dict(os.environ, STEWARD_DATA=temp),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                async with asyncio.timeout(15):
                    while True:
                        line = (await process.stdout.readline()).decode("utf-8")
                        self.assertTrue(line, "App exited before readiness")
                        if "Interface e API prontas" in line:
                            break
                restored = State(temp)
                self.assertTrue(restored.public()["recovery_required"])
                self.assertEqual(restored.data["state"], "interrupted")
                self.assertTrue(restored.data["destination_manual"])
            finally:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 10)

    async def test_real_app_startup_writes_ready_and_disabled_schedule_to_stdout(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-B",
                "-m",
                "steward",
                cwd=root / "backup_steward",
                env=dict(os.environ, STEWARD_DATA=temp, SUPERVISOR_TOKEN="fixture-startup-token"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            lines = []
            try:
                async with asyncio.timeout(15):
                    while True:
                        line = (await process.stdout.readline()).decode("utf-8")
                        self.assertTrue(line, "App exited before becoming ready: " + "".join(lines))
                        lines.append(line)
                        if "Interface e API prontas" in line:
                            break
                output = "".join(lines)
                self.assertIn(f"HA Backup Steward {VERSION} a iniciar", output)
                self.assertIn("modo=observação", output)
                self.assertIn("Agenda disponível e pronta: DESATIVADA", output)
                self.assertNotIn("fixture-startup-token", output)
            finally:
                if process.returncode is None:
                    process.terminate()
                await asyncio.wait_for(process.wait(), 10)

    async def test_forbidden_job_permission_is_detected_before_reading_core_states(self):
        app = web.Application()

        async def forbidden(request):
            return web.json_response({"result": "error"}, status=403)

        app.router.add_get("/jobs/{job}", forbidden)
        async with TestServer(app) as server, ClientSession() as session:
            api = Supervisor(session, "fixture-token", str(server.make_url("/")).rstrip("/"))
            api.core_state = AsyncMock()
            with self.assertRaisesRegex(APIError, "GET /jobs/.*HTTP 403"):
                await api.preflight(validate({}))
            api.core_state.assert_not_awaited()

    async def test_authorized_missing_probe_allows_preflight(self):
        api = Supervisor(None, "fixture-token")
        api.request = AsyncMock(side_effect=APIError("missing probe", 404))
        api.core_state = AsyncMock(side_effect=["off", "off", "idle", "off"])
        await api.preflight(validate({}))
        self.assertEqual(api.core_state.await_count, 4)


if __name__ == "__main__":
    unittest.main()
