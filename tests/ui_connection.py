"""Browser-only connection safety tests against temporary local fixture files."""

import os
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1] / "backup_steward/steward/static"


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/static/"):
            self.path = self.path.removeprefix("/static")
        return super().do_GET()


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}/"
actions = (
    "run",
    "verify",
    "restore",
    "cancel-run",
    "recover",
    "destination-mount",
    "destination-shutdown",
    "settings-toggle",
    "accept-confirm",
    "config-save",
    "pair",
    "copy-token",
)


def status(busy):
    return {
        "version": "0.1.16",
        "busy": busy,
        "can_cancel": busy,
        "mode": "production",
        "schedule_enabled": False,
        "schedule_time": "04:00",
        "timezone": "Europe/Madrid",
        "checksum_frequency": "monthly",
        "daily_retention_days": 3,
        "destination_mount": "/media/HAL3",
        "verification_sources": ["/media/NASRUI"],
        "state": {
            "state": "running" if busy else "idle",
            "mode": "backup",
            "needs_cleanup": busy,
            "recovery_required": False,
            "events": [],
        },
    }


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            **(
                {"executable_path": os.environ["STEWARD_TEST_BROWSER"]}
                if os.environ.get("STEWARD_TEST_BROWSER")
                else {}
            ),
        )
        nojs = browser.new_context(java_script_enabled=False)
        page = nojs.new_page()
        page.goto(base)
        for id in actions:
            assert page.locator("#" + id).is_disabled(), id
        nojs.close()
        page = browser.new_page()
        # Advance polls explicitly so response-order tests do not depend on wall-clock timing.
        page.add_init_script(
            "window.testPollers = []; window.setInterval = fn => { window.testPollers.push(fn); return 0; };"
        )
        held = []
        posts = []
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route(r):
            if r.request.method == "POST":
                posts.append(r.request.post_data_json)
                r.fulfill(status=202, json={"accepted": True})
            else:
                held.append(r)

        page.route("**/v1/status", route)
        page.route("**/v1/run", route)

        def hold_refresh():
            count = len(held)
            page.evaluate("void refresh()")
            page.wait_for_function("document.getElementById('run').disabled")
            for _ in range(30):
                if len(held) > count:
                    return held[-1]
                page.wait_for_timeout(10)
            raise AssertionError("status request not observed")

        def all_locked():
            for id in actions:
                assert page.locator("#" + id).is_disabled(), id

        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("document.getElementById('status-label').textContent.includes('ligar')")
        all_locked()
        assert posts == []
        count = len(held)
        page.evaluate("window.testPollers.forEach(fn => fn())")
        page.wait_for_timeout(30)
        assert len(held) == count, "Periodic polling must wait for a pending slow response"
        held[-1].fulfill(json=status(True))
        page.wait_for_function("document.getElementById('status-label').textContent==='Backup em curso'")
        for id in ("run", "verify", "destination-mount", "destination-shutdown"):
            assert page.locator("#" + id).is_disabled(), id
        assert page.locator("#cancel-run").is_enabled()
        response = hold_refresh()
        all_locked()
        response.fulfill(json=status(False))
        page.wait_for_function("!document.getElementById('run').disabled")
        page.locator("#run").click()
        assert page.locator("#accept-confirm").is_enabled()
        response = hold_refresh()
        all_locked()
        response.abort("failed")
        page.wait_for_function("document.getElementById('status-label').textContent==='Ligação indisponível'")
        all_locked()
        assert page.locator("#cancel-confirm").is_enabled()
        error = page.evaluate("api('run','POST',{mode:'backup'}).then(()=>'',e=>e.message)")
        assert "estado" in error and posts == [], (error, posts)
        response = hold_refresh()
        all_locked()
        response.fulfill(json=status(True))
        page.wait_for_function("document.getElementById('status-label').textContent==='Backup em curso'")
        assert page.locator("#accept-confirm").is_disabled()
        page.locator("#cancel-confirm").click()
        response = hold_refresh()
        response.fulfill(json={"busy": False})
        page.wait_for_function("document.getElementById('status-label').textContent==='Ligação indisponível'")
        all_locked()
        old = hold_refresh()
        new = hold_refresh()
        new.fulfill(json=status(True))
        page.wait_for_function("document.getElementById('status-label').textContent==='Backup em curso'")
        old.fulfill(json=status(False))
        page.wait_for_timeout(50)
        assert page.locator("#run").is_disabled()
        assert page.locator("#status-label").inner_text() == "Backup em curso"
        # A later area/global verification or manual disk session must not replace backup results.
        backup = {
            "run_id": "backup-fixture",
            "mode": "backup_only",
            "execution_mode": "production",
            "state": "success",
            "l2": "delivered",
            "l3": "success",
            "retention": "success",
            "safe_to_power_off": True,
            "needs_cleanup": False,
            "last_success": "2026-10-26T19:36:31+00:00",
        }
        for operation in ("checksum", "destination_mounted", "destination_off"):
            value = status(False)
            value["state"].update(
                mode="checksum",
                state=operation if operation != "checksum" else "success",
                run_id="later-operation",
                l2="skipped",
                l3="success",
                retention="not_applicable",
                destination_manual=operation == "destination_mounted",
                last_backup=backup,
                last_backup_success=backup["last_success"],
                last_success="2026-10-27T20:00:00+00:00",
            )
            response = hold_refresh()
            response.fulfill(json=value)
            page.wait_for_function("!document.getElementById('settings-toggle').disabled")
            assert page.locator("#run-id").inner_text() == "backup-fixture"
            assert page.locator("#l2-state").inner_text() == "Concluído"
            assert page.locator("#l3-state").inner_text() == "Concluída"
            assert page.locator("#retention-state").inner_text() == "Aplicada"
            assert page.locator("#cleanup-state").inner_text() == "Disco desligado"
            assert "26" in page.locator("#last-success").inner_text()
            assert "Último backup" in page.locator("#backup-summary").inner_text()
        value = status(True)
        value["state"].update(
            run_id="active-backup",
            mode="backup_only",
            l2="delivered",
            l3="pending",
            retention="success",
            last_backup=backup,
        )
        response = hold_refresh()
        response.fulfill(json=value)
        page.wait_for_function("document.getElementById('run-id').textContent==='active-backup'")
        assert "Backup em curso" in page.locator("#backup-summary").inner_text()
        assert page.locator("#l3-state").inner_text() == "A aguardar"
        value = status(False)
        value["state"].update(mode="checksum", state="success", l2="skipped", retention="pending")
        response = hold_refresh()
        response.fulfill(json=value)
        page.wait_for_function("!document.getElementById('settings-toggle').disabled")
        assert page.locator("#l2-state").inner_text() == "Sem dados"
        assert "Ainda sem resultados" in page.locator("#backup-summary").inner_text()
        assert page.locator("#last-success").inner_text() == "Ainda sem backup registado"
        # Editing the weekday is saveable while idle, with a reason when locked.
        options = json.loads((root.parents[1] / "config.json").read_text(encoding="utf-8"))["options"]
        page.route("**/v1/config", lambda r: r.fulfill(json=options))
        page.locator("#settings-toggle").click()
        page.locator('[name="checksum_weekday"]').select_option("0")
        assert page.locator("#config-save").is_enabled()
        assert page.locator('[name="legacy_scheduler_disabled"]').count() == 0
        assert page.locator("#config-save-reason").inner_text() == ""
        value = status(True)
        value["state"]["integrity"] = {
            "status": "running",
            "error_count": 0,
            "error_limit": 1000,
            "areas": [
                {
                    "kind": "sha256",
                    "source": "/media/first",
                    "destination": "/media/offline/first",
                    "status": "success",
                    "checked": 100,
                    "errors": 0,
                },
                {
                    "kind": "sha256",
                    "source": "/media/second",
                    "destination": "/media/offline/second",
                    "status": "running",
                    "checked": 23,
                    "errors": 0,
                },
                {"kind": "size", "source": "/media/first", "status": "success", "checked": 100, "errors": 0},
            ],
        }
        response = hold_refresh()
        response.fulfill(json=value)
        page.wait_for_function("document.getElementById('checksum-progress').textContent.includes('123')")
        assert page.locator("#config-save").is_disabled()
        assert "durante a execução" in page.locator("#config-save-reason").inner_text()
        assert page.locator('[name="checksum_weekday"]').input_value() == "0"
        page.locator("#close-settings").click()
        assert page.locator("#checksum-progress").is_visible()
        assert "/media/second" in page.locator("#checksum-progress").inner_text()
        value["state"]["integrity"]["areas"][1]["checked"] = 30
        response = hold_refresh()
        response.fulfill(json=value)
        page.wait_for_function("document.getElementById('checksum-progress').textContent.includes('130')")
        response = hold_refresh()
        response.fulfill(json=status(False))
        page.wait_for_function("!document.getElementById('config-save').disabled")
        assert page.locator("#checksum-progress").is_hidden()
        assert posts == [] and not errors, (posts, errors)
        browser.close()
        print(
            "Connection UI passed: no-JS/loading locks, delayed active backup, pending polls, outage with open confirmation, malformed state, reconnect and out-of-order replies; zero mutations"
        )
finally:
    server.shutdown()
    server.server_close()
