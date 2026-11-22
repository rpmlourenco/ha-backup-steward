"""Mirror regressions use temporary keys/files and fake APIs only."""

import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from aiohttp.test_utils import TestClient, TestServer
from steward.config import validate
from steward.engine import Engine
from steward.l4 import Mirror, Plan, execute, known_hosts, rsync_args
from steward.l4_policy import public_key
from steward.server import application
from steward.state import State

KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(32)).decode()
UUID = "11111111-1111-4111-8111-111111111111"


def policy(**changes):
    return dict(
        enabled=True,
        host="mirror.example.test",
        host_key=KEY,
        destination_uuid=UUID,
        sources=[{"source": "/media/immich", "name": "immich", "uuid": UUID}],
        **changes,
    )


class PolicyTests(unittest.TestCase):
    def test_upgrade_disabled_and_no_shared_mutable_defaults(self):
        first, second = validate({}), validate({})
        self.assertFalse(first["l4"]["enabled"])
        first["l4"]["sources"].append("changed")
        self.assertEqual(second["l4"]["sources"], [])

    def test_reject_unsafe_transport_or_paths(self):
        for changes in (
            {"host": "https://server"},
            {"host": "-oProxyCommand=sh"},
            {"host_key": KEY + "\nother"},
            {"sources": [{"source": "/etc", "name": "immich", "uuid": UUID}]},
            {"sources": [{"source": "/media/immich", "name": "../outside", "uuid": UUID}]},
            {"port": True},
            {"enabled": True},
            {"enabled": False, "schedule_enabled": True},
        ):
            raw = dict(policy(), **changes) if changes != {"enabled": True} else changes
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                validate({"l4": raw})

    def test_reject_authorized_keys_options_and_private_keys(self):
        for text in ("restrict " + KEY, "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh-ed25519 AAAA"):
            with self.assertRaises(ValueError):
                public_key(text)

    def test_streaming_plan_counts_over_capture_limit(self):
        plan = Plan()
        data = (">f+++++++++|500\n" + "*deleting  |0\n") * 10000
        for i in range(0, len(data), 123):
            plan.feed(data[i : i + 123])
        plan.finish()
        self.assertEqual(plan.deletes, 10000)
        self.assertEqual(plan.bytes, 5000000)

    def test_no_password_or_unsafe_host_policy_in_command(self):
        cfg = validate({"l4": policy()})["l4"]
        args = rsync_args(cfg, Path("/data/steward/l4-ssh"), cfg["sources"][0])
        self.assertIn("--delete-delay", args)
        self.assertNotIn("--inplace", args)
        self.assertIn("StrictHostKeyChecking=yes", " ".join(args))
        self.assertEqual(known_hosts(cfg).split()[0], "[mirror.example.test]:2222")


class MirrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(validate({"l4": policy()}), State(Path(self.tmp.name)), AsyncMock())
        self.engine.mirror = Mirror(self.engine)

    async def test_disabled_and_nonproduction_never_start(self):
        for mode in ("observe", "test"):
            self.engine.cfg["execution_mode"] = mode
            with self.assertRaises(ValueError):
                self.engine.mirror.start()
        self.engine.cfg["execution_mode"] = "production"
        self.engine.cfg["l4"]["enabled"] = False
        with self.assertRaises(ValueError):
            self.engine.mirror.start()

    async def test_subprocess_errors_are_drained_and_mapped_without_raw_details(self):
        code = "import os; os.write(2,b'x'*100000); os.write(2,b'Permission denied (publickey) fixture-private-detail'); raise SystemExit(255)"
        with self.assertRaises(ValueError) as failure:
            await execute([sys.executable, "-c", code], timeout=5)
        self.assertIn("não está autorizada", str(failure.exception))
        self.assertNotIn("fixture-private-detail", str(failure.exception))

    async def test_subprocess_cancellation_reaps_the_worker(self):
        import os

        pidfile = Path(self.tmp.name) / "pid"
        code = (
            "import os,time; from pathlib import Path; Path("
            + repr(str(pidfile))
            + ").write_text(str(os.getpid())); time.sleep(60)"
        )
        task = asyncio.create_task(execute([sys.executable, "-c", code]))
        try:
            for _ in range(200):
                if pidfile.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(pidfile.exists())
            pid = int(pidfile.read_text(encoding="utf-8"))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_cleanup_and_busy_block_start(self):
        self.engine.cfg["execution_mode"] = "production"
        self.engine.state.update(needs_cleanup=True)
        with self.assertRaises(ValueError):
            self.engine.mirror.start()
        self.engine.state.update(needs_cleanup=False)
        async with self.engine.lock:
            with self.assertRaises(ValueError):
                self.engine.mirror.start()

    async def test_cancel_drains_job_and_preserves_local_outcomes(self):
        entered = asyncio.Event()

        async def worker():
            entered.set()
            await asyncio.sleep(60)

        self.engine.state.update(l2="delivered", l3="success", last_success="local-success")
        self.engine.mirror.task = asyncio.create_task(worker())
        await entered.wait()
        await self.engine.mirror.cancel()
        self.assertTrue(self.engine.mirror.task.done())
        self.assertEqual(self.engine.state.data["last_success"], "local-success")

    async def test_schedule_disabled_observe_and_same_date(self):
        mirror = self.engine.mirror
        self.engine.cfg["execution_mode"] = "production"
        self.engine.cfg["l4"]["schedule_enabled"] = True
        instant = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        self.assertTrue(mirror.due(instant))
        mirror.update(schedule_date="2026-11-22")
        self.assertFalse(mirror.due(instant))
        mirror.update(schedule_date=None)
        self.engine.cfg["l4"]["enabled"] = False
        self.assertFalse(mirror.due(instant))

    async def test_ingress_only_and_schema(self):
        with patch.dict("os.environ", {"STEWARD_INGRESS_IP": "127.0.0.1"}):
            async with TestClient(TestServer(application(self.engine, ingress=True))) as client:
                response = await client.get("/v1/l4/status")
                self.assertEqual(response.status, 200)
                response = await client.post("/v1/l4/run", json={})
                self.assertEqual(response.status, 403)
                response = await client.post("/v1/l4/run", json={}, headers={"X-Steward-UI": "1"})
                self.assertEqual(response.status, 400)
                response = await client.put(
                    "/v1/l4/config",
                    json=dict(self.engine.cfg["l4"], enabled=False),
                    headers={"X-Steward-UI": "1"},
                )
                self.assertEqual(response.status, 200)
                persisted = json.loads(
                    (self.engine.state.directory / "config.json").read_text(encoding="utf-8")
                )
                self.assertFalse(persisted["l4"]["enabled"])

    async def test_probe_does_not_start_rsync_or_change_local_result(self):
        with patch.object(self.engine.mirror, "probe", new=AsyncMock()) as probe:
            await self.engine.mirror.test()
        probe.assert_awaited_once()
        self.assertEqual(self.engine.state.data["l4"]["connection"], "success")
        self.assertIsNone(self.engine.state.data.get("last_success"))

    async def test_progress_parses_before_throttling_and_flushes_final_counts(self):
        mirror = self.engine.mirror
        mirror.update(source="photos", run_id="fixture")
        mirror.progress("\r 1,000 10% 1.00MB/s 0:00:09 (xfr#1, to-chk=9/10)\r")
        mirror.progress(" 2,000 20% 1.00MB/s 0:00:08 (xfr#2, to-chk=8/10)\r")
        self.assertEqual(mirror.parser.last["bytes"], 2000)
        mirror.publish_progress(force=True)
        self.assertEqual(mirror.status()["bytes"], 2000)
        self.assertEqual(mirror.status()["files_transferred"], 2)
        for i in range(50):
            mirror.event("planning", f"Etapa {i}")
        self.assertEqual(len(mirror.status()["events"]), 40)

    async def test_connection_progress_and_result_are_visible_with_l4_disabled(self):
        mirror = self.engine.mirror
        self.engine.cfg["l4"]["enabled"] = False
        entered, release = asyncio.Event(), asyncio.Event()

        async def probe(*args):
            entered.set()
            await release.wait()

        with patch.object(mirror, "probe", new=probe):
            task = asyncio.create_task(mirror.test())
            try:
                await entered.wait()
                self.assertTrue(mirror.status()["testing"])
                self.assertEqual(mirror.status()["connection"], "testing")
                self.assertIsNone(mirror.status()["tested_at"])
            finally:
                release.set()
                result = await task
        self.assertFalse(result["testing"])
        self.assertFalse(result["enabled"])
        self.assertEqual(result["connection"], "success")
        self.assertIn("confirmados", result["connection_message"])
        self.assertIsNotNone(result["tested_at"])

    async def test_failure_replaces_previous_test_and_persists_its_reason(self):
        mirror = self.engine.mirror
        self.engine.cfg["l4"]["enabled"] = False
        mirror.update(connection="success", tested_at="previous", connection_message="Old success")
        with patch.object(mirror, "probe", new=AsyncMock(side_effect=ValueError("Porta recusou a ligação."))):
            with self.assertRaises(ValueError):
                await mirror.test()
        result = mirror.status()
        self.assertEqual(result["connection"], "failed")
        self.assertEqual(result["connection_message"], "Porta recusou a ligação.")
        self.assertNotEqual(result["tested_at"], "previous")
        self.assertFalse(result["testing"])
        self.assertFalse(result["busy"])
        self.assertFalse(result["enabled"])
        self.assertIsNone(self.engine.state.data.get("last_success"))

    async def test_restart_marks_a_pending_connection_test_as_interrupted(self):
        self.engine.mirror.update(connection="testing", tested_at=None)
        restored = Mirror(self.engine)
        self.assertEqual(restored.status()["connection"], "interrupted")
        self.assertFalse(restored.status()["testing"])

    async def test_disable_during_transfer_cancels_and_preserves_remote_and_local_results(self):
        self.engine.cfg.update(
            execution_mode="production",
            source_uuid=UUID,
            destination_uuid="22222222-2222-4222-8222-222222222222",
            power_entity="switch.fixture",
            encryption_password="fixture-only",
        )
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.sleep(60)

        self.engine.mirror.task = asyncio.create_task(work())
        self.engine.task = self.engine.mirror.task
        self.engine.state.update(last_success="local-result", l4={"last_success": "remote-result"})
        await started.wait()
        with patch.dict("os.environ", {"STEWARD_INGRESS_IP": "127.0.0.1"}):
            async with TestClient(TestServer(application(self.engine, ingress=True))) as client:
                response = await client.put(
                    "/v1/l4/config",
                    json=dict(self.engine.cfg["l4"], enabled=False, schedule_enabled=False),
                    headers={"X-Steward-UI": "1"},
                )
                self.assertEqual(response.status, 200, await response.text())
        self.assertTrue(self.engine.mirror.task.done())
        self.assertFalse(self.engine.cfg["l4"]["enabled"])
        self.assertEqual(self.engine.state.data["last_success"], "local-result")
        self.assertEqual(self.engine.state.data["l4"]["last_success"], "remote-result")

    async def test_full_worker_success_and_plan_guard_preserve_local_dates(self):
        root = Path(self.tmp.name) / "source"
        root.mkdir()
        (root / "fixture.txt").write_text("fixture", encoding="utf-8")
        self.engine.cfg["l4"]["sources"][0]["source"] = str(root)
        self.engine.state.update(last_success="local-result")
        copies = []

        async def execute(args, *positional, progress=None):
            if len(positional) > 1:
                progress = positional[1]
            if args[0] == "findmnt":
                return json.dumps({"filesystems": [{"uuid": UUID, "fstype": "ext4"}]})
            if "--dry-run" in args:
                progress(">f+++++++++|7\n")
            else:
                copies.append(args)
            return ""

        with (
            patch("steward.l4.execute", new=execute),
            patch.object(
                self.engine.mirror, "probe", new=AsyncMock(return_value={"free_bytes": 100 * 1024**3})
            ),
        ):
            await self.engine.mirror.run()
        self.assertEqual(len(copies), 1)
        self.assertEqual(self.engine.state.data["l4"]["status"], "success")
        self.assertEqual(self.engine.state.data["last_success"], "local-result")

        copies.clear()
        self.engine.cfg["l4"]["max_delete_files"] = 1

        async def excessive(args, *positional, progress=None):
            if len(positional) > 1:
                progress = positional[1]
            if args[0] == "findmnt":
                return await execute(args)
            if "--dry-run" in args:
                progress("*deleting  |0\n*deleting  |0\n")
            else:
                copies.append(args)
            return ""

        with (
            patch("steward.l4.execute", new=excessive),
            patch.object(
                self.engine.mirror, "probe", new=AsyncMock(return_value={"free_bytes": 100 * 1024**3})
            ),
        ):
            await self.engine.mirror.run()
        self.assertEqual(copies, [])
        self.assertEqual(self.engine.state.data["l4"]["status"], "failed")
        self.assertEqual(self.engine.state.data["last_success"], "local-result")
