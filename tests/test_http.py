import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import validate
from steward.engine import Engine
from steward.server import application
from steward.state import State
from steward.supervisor import APIError, Supervisor


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(Path(self.temp.name))
        self.engine = Engine(validate({"encryption_password": "never-expose-this"}), self.state, AsyncMock())
        self.client = TestClient(TestServer(application(self.engine)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def auth(self):
        return {"Authorization": "Bearer " + self.state.identity["api_token"]}

    async def test_status_requires_pairing_token(self):
        response = await self.client.get("/v1/status")
        self.assertEqual(response.status, 401)
        response = await self.client.get("/v1/status", headers=self.auth())
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertNotIn("never-expose-this", text)
        self.assertNotIn(self.state.identity["api_token"], text)

    async def test_integrity_report_survives_reload_and_authenticated_status(self):
        from steward.integrity import IntegrityFailure, IntegrityRun

        report_run = IntegrityRun(1000, lambda report: self.state.update(integrity=report))
        area = report_run.plan("/media/source", "/media/target/source", "sha256")
        report_run.begin(area)
        report_run.line(area, "* never-expose-this.flac")
        report_run.end(area)
        with self.assertRaises(IntegrityFailure):
            report_run.finish()
        reloaded = State(Path(self.temp.name))
        self.assertEqual(reloaded.data["integrity"]["status"], "failed")
        response = await self.client.get("/v1/status", headers=self.auth())
        text = await response.text()
        self.assertNotIn("never-expose-this", text)
        report = (await response.json())["state"]["integrity"]
        self.assertEqual(report["error_count"], 1)
        self.assertEqual(report["areas"][0]["status"], "failed")
        self.assertTrue(report["complete"])

    async def test_active_disk_does_not_report_recovery_during_a_running_job(self):
        self.state.update(state="running", needs_cleanup=True)
        await self.engine.lock.acquire()
        try:
            response = await self.client.get("/v1/status", headers=self.auth())
            result = await response.json()
            self.assertTrue(result["busy"])
            self.assertTrue(result["state"]["needs_cleanup"])
            self.assertFalse(result["state"]["recovery_required"])
        finally:
            self.engine.lock.release()
        self.state.update(state="failed")
        response = await self.client.get("/v1/status", headers=self.auth())
        self.assertTrue((await response.json())["state"]["recovery_required"])

    async def test_scoped_verification_api_and_configured_choices(self):
        response = await self.client.get("/v1/status", headers=self.auth())
        self.assertEqual(
            (await response.json())["verification_sources"],
            ["/media/NASRUI", "/media/immich", "/media/filebrowser"],
        )
        with patch.object(self.engine, "start") as start:
            response = await self.client.post(
                "/v1/run", headers=self.auth(), json={"mode": "checksum", "source": "/media/immich"}
            )
            self.assertEqual(response.status, 202)
            start.assert_called_once_with("checksum", source="/media/immich")
        self.engine.cfg["execution_mode"] = "test"
        response = await self.client.get("/v1/status", headers=self.auth())
        self.assertEqual((await response.json())["verification_sources"], ["/media/NASRUI"])

    async def test_scope_request_types_and_unknown_fields_are_rejected(self):
        with patch.object(self.engine, "start") as start:
            for body in (
                [],
                {"source": "/media/immich"},
                {"mode": "checksum", "source": None},
                {"mode": "checksum", "source": False},
                {"mode": "checksum", "source": ["/media/immich"]},
                {"mode": "checksum", "source": "/media/immich", "path": "/etc"},
            ):
                with self.subTest(body=body):
                    response = await self.client.post("/v1/run", headers=self.auth(), json=body)
                    self.assertEqual(response.status, 400)
            start.assert_not_called()

    async def test_cancel_endpoint_is_authenticated_strict_and_delegates_to_safe_control(self):
        response = await self.client.post("/v1/cancel", json={})
        self.assertEqual(response.status, 401)
        response = await self.client.post("/v1/cancel", headers=self.auth(), json={})
        self.assertEqual(response.status, 400)
        with patch.object(self.engine, "cancel") as cancel:
            for body in ({"force": True}, [], None):
                response = await self.client.post("/v1/cancel", headers=self.auth(), json=body)
                self.assertIn(response.status, (400, 415))
            cancel.assert_not_called()
            response = await self.client.post("/v1/cancel", headers=self.auth(), json={})
            self.assertEqual(response.status, 202)
            cancel.assert_called_once_with()
        with patch.object(self.engine, "start") as start:
            response = await self.client.post("/v1/run", headers=self.auth(), json={"mode": "backup_only"})
            self.assertEqual(response.status, 202)
            start.assert_called_once_with("backup_only")

    async def test_observation_rejects_execution_and_arbitrary_operations(self):
        for mode in ("backup", "reboot; rm -rf /", "checksum"):
            response = await self.client.post("/v1/run", headers=self.auth(), json={"mode": mode})
            self.assertEqual(response.status, 400)
        self.engine.api.power.assert_not_called()

    async def test_manual_destination_route_is_authenticated_guarded_and_strict(self):
        response = await self.client.post("/v1/destination", json={"action": "mount"})
        self.assertEqual(response.status, 401)
        for body in (
            {"action": "mount"},
            {"action": "shutdown"},
            {"action": "format"},
            {"action": "mount", "path": "/dev/sda"},
        ):
            response = await self.client.post("/v1/destination", headers=self.auth(), json=body)
            self.assertEqual(response.status, 400)
        self.engine.api.power.assert_not_called()
        with patch.object(self.engine, "start_destination") as start:
            response = await self.client.post(
                "/v1/destination", headers=self.auth(), json={"action": "mount"}
            )
            self.assertEqual(response.status, 202)
            start.assert_called_once_with("mount")

    async def test_ui_document_and_api_are_not_cached_and_assets_are_content_addressed(self):
        import hashlib
        import re

        with patch.dict("os.environ", {"STEWARD_INGRESS_IP": "127.0.0.1"}):
            client = TestClient(TestServer(application(self.engine, ingress=True)))
            await client.start_server()
            self.addAsyncCleanup(client.close)
            response = await client.get(
                "/", headers={"If-None-Match": "*", "If-Modified-Since": "Wed, 31 Dec 2099 23:59:59 GMT"}
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            html = await response.text()
            self.assertIn('id="backup-policy" hidden', html)
            self.assertIn('id="backup-verification"', html)
            self.assertNotIn('src="static/app.js"', html)
            self.assertNotIn('href="static/style.css"', html)
            paths = re.findall(r'(?:src|href)="(static/([a-f0-9]{64})/(?:app.js|style.css))"', html)
            self.assertEqual(len(paths), 2)
            for path, digest in paths:
                asset = await client.get("/" + path)
                self.assertEqual(asset.status, 200)
                content = await asset.read()
                self.assertEqual(hashlib.sha256(content).hexdigest(), digest)
                self.assertIn("immutable", asset.headers["Cache-Control"])
            unknown = await client.get("/static/" + "0" * 64 + "/app.js")
            self.assertEqual(unknown.status, 404)
            status = await client.get("/v1/status")
            self.assertEqual(status.headers["Cache-Control"], "no-store")
            legacy = await client.get("/static/app.js")
            self.assertEqual(legacy.headers["Cache-Control"], "no-store")

    async def test_ui_asset_url_changes_with_contents_even_when_mtime_and_size_match(self):
        import os
        import re
        from steward.server import STATIC

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict("os.environ", {"STEWARD_INGRESS_IP": "127.0.0.1"}),
        ):
            root = Path(temp)
            for name in ("index.html", "app.js", "style.css"):
                (root / name).write_bytes((STATIC / name).read_bytes())

            async def document():
                with patch("steward.server.STATIC", root):
                    app = application(self.engine, ingress=True)
                client = TestClient(TestServer(app))
                await client.start_server()
                self.addAsyncCleanup(client.close)
                return await (await client.get("/")).text()

            first = await document()
            js = root / "app.js"
            before = js.stat()
            data = js.read_bytes()
            js.write_bytes(data.replace(b"function", b"functioN", 1))
            os.utime(js, ns=(before.st_atime_ns, before.st_mtime_ns))
            second = await document()
            pattern = r"static/[a-f0-9]{64}/app.js"
            self.assertNotEqual(re.search(pattern, first).group(), re.search(pattern, second).group())

    async def test_ui_html_disables_actions_before_any_javascript_or_status_response(self):
        from html.parser import HTMLParser

        class Controls(HTMLParser):
            def __init__(self):
                super().__init__()
                self.disabled = {}

            def handle_starttag(self, tag, attrs):
                if tag in ("button", "select"):
                    attrs = dict(attrs)
                    if "id" in attrs:
                        self.disabled[attrs["id"]] = "disabled" in attrs

        with patch.dict("os.environ", {"STEWARD_INGRESS_IP": "127.0.0.1"}):
            client = TestClient(TestServer(application(self.engine, ingress=True)))
            await client.start_server()
            self.addAsyncCleanup(client.close)
            parser = Controls()
            parser.feed(await (await client.get("/")).text())
            for control in (
                "run",
                "verify",
                "cancel-run",
                "recover",
                "destination-mount",
                "destination-shutdown",
                "settings-toggle",
                "accept-confirm",
                "config-save",
                "pair",
                "copy-token",
                "backup-verification",
                "verification-source",
            ):
                self.assertTrue(parser.disabled[control], control)
            self.assertFalse(parser.disabled["cancel-confirm"])
            self.assertFalse(parser.disabled["close-settings"])

    async def test_ingress_rejects_non_supervisor_clients(self):
        client = TestClient(TestServer(application(self.engine, ingress=True)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        response = await client.get("/")
        self.assertEqual(response.status, 403)

    async def test_pairing_and_config_are_not_on_integration_api(self):
        for path in ("/v1/config", "/v1/pair"):
            response = await self.client.get(path, headers=self.auth())
            self.assertEqual(response.status, 404)

    async def test_ingress_configuration_masks_secrets_and_requires_csrf_header(self):
        with patch.dict("os.environ", {"STEWARD_INGRESS_IP": "127.0.0.1"}):
            client = TestClient(TestServer(application(self.engine, ingress=True)))
            await client.start_server()
            self.addAsyncCleanup(client.close)
            response = await client.get("/v1/config")
            self.assertEqual(response.status, 200)
            self.assertNotIn("never-expose-this", await response.text())
            response = await client.put("/v1/config", json={"l2_keep": 7})
            self.assertEqual(response.status, 403)
            response = await client.put("/v1/config", json={"l2_keep": 7}, headers={"X-Steward-UI": "1"})
            self.assertEqual(response.status, 200)
            self.assertEqual(self.engine.cfg["l2_keep"], 7)
            response = await client.post("/v1/pair", json={}, headers={"X-Steward-UI": "1"})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["api_token"], self.state.identity["api_token"])


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_truncated_transfer_never_replaces_archive(self):
        app = web.Application()

        async def download(request):
            return web.Response(body=b"incomplete")

        app.router.add_get("/backups/abcdef12/download", download)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        with tempfile.TemporaryDirectory() as temp:
            async with ClientSession() as session:
                api = Supervisor(session, "private-fixture", str(server.make_url("/")).rstrip("/"))
                with self.assertRaises(APIError):
                    await api.download("abcdef12", temp, "fixture.tar", expected_size=999)
            self.assertFalse((Path(temp) / "fixture.tar").exists())
            self.assertTrue((Path(temp) / "fixture.tar.partial").exists())

    async def test_complete_transfer_checks_slug_and_sha256(self):
        raw = json.dumps({"slug": "abcdef12", "date": "2026-10-24T04:00:00+00:00"}).encode()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo("backup.json")
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
        payload = buffer.getvalue()
        app = web.Application()

        async def download(request):
            return web.Response(body=payload)

        app.router.add_get("/backups/abcdef12/download", download)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        with tempfile.TemporaryDirectory() as temp:
            async with ClientSession() as session:
                api = Supervisor(session, "private-fixture", str(server.make_url("/")).rstrip("/"))
                path, checksum = await api.download("abcdef12", temp, "fixture.tar", len(payload))
            self.assertTrue(path.exists())
            self.assertEqual(len(checksum), 64)
            self.assertFalse(path.with_suffix(".tar.partial").exists())


if __name__ == "__main__":
    unittest.main()
