"""Restore UI acceptance against fake endpoints; no HA, mounts or real data."""

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
state = dict(
    version="fixture",
    busy=False,
    can_cancel=False,
    mode="production",
    schedule_enabled=False,
    checksum_frequency="monthly",
    daily_retention_days=3,
    verification_sources=["/media/NAS", "/media/photos", "/media/files"],
    state=dict(state="idle", mode="backup", needs_cleanup=False, events=[]),
)
posts = []
nonempty = True

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
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("**/v1/status", lambda r: r.fulfill(json=state))

        def preview(r):
            body = r.request.post_data_json
            posts.append(("preview", body))
            r.fulfill(
                json=dict(
                    token="fixture-plan",
                    areas=[
                        dict(source=s, mirror="/media/offline/mirror/" + s.split("/")[-1], nonempty=nonempty)
                        for s in body["sources"]
                    ],
                )
            )

        def start(r):
            posts.append(("start", r.request.post_data_json))
            r.fulfill(status=202, json={"accepted": True})

        page.route("**/v1/restore/preview", preview)
        page.route("**/v1/restore/start", start)
        page.goto(base)
        page.wait_for_function("!document.getElementById('restore').disabled")
        page.locator("#restore").click()
        assert page.locator("#restore-all").is_checked()
        assert page.locator("#restore-checksum").is_checked() is False
        page.locator("#accept-confirm").click()
        page.wait_for_function(
            "document.getElementById('accept-confirm').textContent==='Restaurar agora' && !document.getElementById('restore-erase').disabled"
        )
        assert posts[-1][1]["sources"] == state["verification_sources"]
        assert page.locator("#accept-confirm").is_disabled()
        assert "/media/photos" in page.locator("#restore-summary").inner_text()
        if os.environ.get("STEWARD_UI_SCREENSHOT_DIR"):
            shots = Path(os.environ["STEWARD_UI_SCREENSHOT_DIR"])
            shots.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shots / "restore-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(shots / "restore-mobile.png"))
            page.set_viewport_size({"width": 1280, "height": 1000})
        page.locator("#restore-erase").check()
        page.locator("#restore-checksum").check()
        page.locator("#accept-confirm").click()
        page.wait_for_function("!document.getElementById('confirm').open")
        assert posts[-1] == (
            "start",
            dict(token="fixture-plan", erase_sources=state["verification_sources"], checksum=True),
        )
        page.wait_for_function("!document.getElementById('restore').disabled")
        page.locator("#restore").click()
        page.locator("#restore-area-options input").nth(1).uncheck()
        assert not page.locator("#restore-all").is_checked()
        page.locator("#accept-confirm").click()
        page.wait_for_function("!document.getElementById('restore-erase').disabled")
        assert posts[-1][1]["sources"] == ["/media/NAS", "/media/files"]
        page.locator("#restore-area-options input").nth(0).uncheck()
        assert page.locator("#restore-erase-label").is_hidden()
        assert page.locator("#accept-confirm").inner_text() == "Analisar seleção"
        nonempty = False
        page.locator("#accept-confirm").click()
        page.wait_for_function(
            "document.getElementById('accept-confirm').textContent==='Restaurar agora' && !document.getElementById('accept-confirm').disabled"
        )
        assert posts[-1][1]["sources"] == ["/media/files"]
        assert page.locator("#restore-erase-label").is_hidden()
        page.locator("#accept-confirm").click()
        page.wait_for_function("!document.getElementById('confirm').open")
        assert posts[-1] == ("start", dict(token="fixture-plan", erase_sources=[], checksum=False))
        page.wait_for_function("!document.getElementById('restore').disabled")
        page.locator("#restore").click()
        page.locator("#restore-all").uncheck()
        count = len(posts)
        page.locator("#accept-confirm").click()
        page.wait_for_function("document.getElementById('toast').textContent.includes('Seleciona')")
        assert len(posts) == count
        assert not errors, errors
        browser.close()
        print(
            "Restore UI passed: all/multiple/single areas, explicit erase confirmation, empty areas, checksum choice, selection invalidation and empty-selection rejection"
        )
finally:
    server.shutdown()
    server.server_close()
