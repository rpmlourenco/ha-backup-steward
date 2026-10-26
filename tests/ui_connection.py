"""Browser-only connection safety tests against temporary local fixture files."""

import os
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
        "version": "0.1.12",
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
        assert posts == [] and not errors, (posts, errors)
        browser.close()
        print(
            "Connection UI passed: no-JS/loading locks, delayed active backup, pending polls, outage with open confirmation, malformed state, reconnect and out-of-order replies; zero mutations"
        )
finally:
    server.shutdown()
    server.server_close()
